"""
mongo_db.py -- MongoDB Atlas database layer for Sarathi AI
Replaces SQLite. All db_* functions exposed identically so server.py
needs zero call-site changes.

Collections used:
  users               -- registered citizens
  form_submissions    -- encrypted scheme applications
  audit_log           -- every action taken in the system
  rate_limits         -- per-user sliding-window counters
  translation_cache   -- cached Google Translate results
  otps                -- one-time passwords for login (TTL auto-delete)
  india_schemes       -- myScheme.gov.in data with Gemini embeddings (Vector Search)
"""

import os, time, hashlib
from datetime import datetime, timezone
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError, ServerSelectionTimeoutError, PyMongoError

# ── Connection ────────────────────────────────────────────────────────────────
_MONGO_DB = os.environ.get("MONGODB_DB", "sarathi_db")  # can be set at import time
_client: MongoClient | None = None
_last_uri: str = ""   # track if URI changed (e.g. .env reloaded)

class DBNotAvailable(RuntimeError):
    """Raised when MongoDB is unreachable -- allows routes to show friendly errors."""
    pass

def _get_db():
    """
    Returns a MongoDB database handle.
    URI is read from os.environ at CALL TIME (not at import time) so that
    load_dotenv() in server.py always takes effect even if mongo_db was
    imported before the .env file was processed.
    """
    global _client, _last_uri
    # Read URI fresh every call -- zero overhead since MongoClient is cached
    uri = os.environ.get("MONGODB_URI", "").strip()

    if not uri:
        raise DBNotAvailable("MONGODB_URI not set in .env -- add your Atlas URI (mongodb+srv://...)")

    # Re-create client if URI changed (e.g. .env was edited and server restarted)
    if _client is None or uri != _last_uri:
        _last_uri = uri
        _client = None   # drop old connection

        # Only use TLS for Atlas SRV URIs -- not for localhost/plain connections
        _is_atlas = "mongodb+srv://" in uri or "mongodb.net" in uri
        opts: dict = dict(
            serverSelectionTimeoutMS=8000,
            connectTimeoutMS=8000,
            socketTimeoutMS=12000,
            maxPoolSize=10,
        )
        if _is_atlas:
            opts.update(tls=True)   # Atlas certs are valid; no need to skip verification
        _client = MongoClient(uri, **opts)

    return _client[os.environ.get("MONGODB_DB", "sarathi_db")]


def _db_safe(func):
    """Decorator: wraps DB functions to convert connection errors into DBNotAvailable."""
    import functools
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except DBNotAvailable:
            raise   # already a friendly error -- re-raise as-is
        except ServerSelectionTimeoutError as e:
            uri = os.environ.get("MONGODB_URI", "")
            if not uri:
                msg = "MONGODB_URI not set in .env file. Copy .env.example to .env and add your Atlas URI."
            elif "localhost" in uri:
                msg = "MONGODB_URI points to localhost which is not running. Use your Atlas URI (mongodb+srv://...)."
            else:
                msg = ("MongoDB Atlas connection timed out. "
                       "Most likely cause: IP not whitelisted in Atlas. "
                       "Fix: Atlas Dashboard > Network Access > Add IP > Allow 0.0.0.0/0")
            raise DBNotAvailable(msg) from e
        except PyMongoError as e:
            raise DBNotAvailable(f"MongoDB error: {e}") from e
    return wrapper


# ── Initialise collections & indexes ─────────────────────────────────────────
@_db_safe
def init_mongo():
    db = _get_db()

    # users
    db.users.create_index("mobile",  unique=True)
    db.users.create_index("email",   sparse=True)

    # form_submissions
    db.form_submissions.create_index("ref_id",  unique=True)
    db.form_submissions.create_index("user_id")
    db.form_submissions.create_index([("submitted_at", DESCENDING)])

    # audit_log
    db.audit_log.create_index("user_id")
    db.audit_log.create_index([("timestamp", DESCENDING)])

    # rate_limits -- TTL index auto-purges old windows after 7 days
    db.rate_limits.create_index(
        [("user_id", ASCENDING), ("action", ASCENDING)],
        unique=True,
    )
    db.rate_limits.create_index("window_start", expireAfterSeconds=604800)

    # translation_cache -- TTL 30 days
    db.translation_cache.create_index("cache_key", unique=True)
    db.translation_cache.create_index("created_at", expireAfterSeconds=2592000)

    # otps -- auto-delete after expiry using TTL index
    db.otps.create_index("mobile", unique=True)
    db.otps.create_index("expires_at", expireAfterSeconds=0)

    # Agent memory, conversation history, eligibility results
    db_init_memory_indexes()

    print("✅ MongoDB Atlas ready -- collections & indexes initialised.")


# ── USER HELPERS ──────────────────────────────────────────────────────────────

@_db_safe
def db_get_user_by_mobile(mobile: str) -> dict | None:
    doc = _get_db().users.find_one(
        {"mobile": mobile, "is_active": True}
    )
    if doc:
        doc["id"] = str(doc["_id"])  # server.py uses user["id"] for session
        doc.pop("_id", None)
    return doc


@_db_safe
def db_get_user_by_id(user_id: str) -> dict | None:
    """Look up a user by their string ObjectId. Used by eligibility engine and SMS."""
    from bson import ObjectId
    try:
        doc = _get_db().users.find_one({"_id": ObjectId(str(user_id)), "is_active": True})
    except Exception:
        return None
    if doc:
        doc["id"] = str(doc["_id"])
        doc.pop("_id", None)
    return doc


@_db_safe
def db_create_user(first: str, last: str, email: str, mobile: str, pw_hash: str,
                   dob: str = None, age: str = None, gender: str = None,
                   caste: str = None,
                   village: str = None, mandal: str = None,
                   district: str = None, state: str = None, pincode: str = None,
                   aadhaar: str = None) -> str:
    """
    Inserts a new citizen user and returns the string user_id.
    Raises DuplicateKeyError if mobile already exists.
    """
    from bson import ObjectId
    result = _get_db().users.insert_one({
        "first_name":  first,
        "last_name":   last,
        "email":       email or None,
        "mobile":      mobile,
        "password":    pw_hash,
        "lang_pref":   "en-IN",
        # Extended citizen profile
        "dob":         dob,
        "age":         age,
        "gender":      gender,
        "caste":       caste or None,
        "village":     village,
        "mandal":      mandal,
        "district":    district,
        "state":       state,
        "pincode":     pincode,
        "aadhaar":     aadhaar or None,   # stored encrypted in prod
        "created_at":  _now(),
        "is_active":   True,
    })
    return str(result.inserted_id)


@_db_safe
def db_update_password(mobile: str, new_pw_hash: str) -> None:
    """Update a user's password hash. Called from forgot-password reset flow."""
    _get_db().users.update_one(
        {"mobile": mobile},
        {"$set": {"password": new_pw_hash, "updated_at": _now()}}
    )


# ── SUBMISSION HELPERS ────────────────────────────────────────────────────────

@_db_safe
def db_save_submission(
    ref_id: str,
    user_id,
    service_name: str,
    encrypted: str,
    hash_val: str,
    lang: str,
    ip: str,
) -> None:
    _get_db().form_submissions.insert_one({
        "ref_id":          ref_id,
        "user_id":         str(user_id),
        "service_name":    service_name,
        "encrypted_data":  encrypted,
        "integrity_hash":  hash_val,
        "language":        lang,
        "status":          "submitted",
        "ip_address":      ip,
        "submitted_at":    _now(),
        "reviewed_at":     None,
    })


@_db_safe
def db_get_submissions_for_user(user_id) -> list[dict]:
    cursor = _get_db().form_submissions.find(
        {"user_id": str(user_id)},
        {"_id": 0, "ref_id": 1, "service_name": 1, "language": 1,
         "status": 1, "submitted_at": 1},
    ).sort("submitted_at", DESCENDING).limit(50)
    return list(cursor)


# ── AUDIT LOG ─────────────────────────────────────────────────────────────────

@_db_safe
def db_log_audit(user_id, action: str, ref_id=None, details=None, ip=None) -> None:
    _get_db().audit_log.insert_one({
        "user_id":    str(user_id) if user_id else None,
        "ref_id":     ref_id,
        "action":     action,
        "details":    details,
        "ip_address": ip,
        "timestamp":  _now(),
    })


# ── RATE LIMITING ─────────────────────────────────────────────────────────────

@_db_safe
def db_check_rate_limit(user_id, action: str,
                         max_count: int = 10, window_seconds: int = 60) -> bool:
    now    = time.time()
    cutoff = now - window_seconds
    db     = _get_db()
    key    = {"user_id": str(user_id), "action": action}

    doc = db.rate_limits.find_one(key)

    if not doc:
        db.rate_limits.insert_one({**key, "window_start": now, "count": 1})
        return True

    if doc["window_start"] < cutoff:
        # window expired -- reset
        db.rate_limits.update_one(key, {"$set": {"window_start": now, "count": 1}})
        return True

    if doc["count"] >= max_count:
        return False

    db.rate_limits.update_one(key, {"$inc": {"count": 1}})
    return True


# ── ADMIN STATS ───────────────────────────────────────────────────────────────

@_db_safe
def db_get_admin_stats() -> dict:
    db     = _get_db()
    total  = db.form_submissions.count_documents({})
    users  = db.users.count_documents({})

    # scheme breakdown
    by_scheme = {
        d["_id"]: d["count"]
        for d in db.form_submissions.aggregate([
            {"$group": {"_id": "$service_name", "count": {"$sum": 1}}}
        ])
    }

    # language breakdown
    by_lang = {
        d["_id"]: d["count"]
        for d in db.form_submissions.aggregate([
            {"$group": {"_id": "$language", "count": {"$sum": 1}}}
        ])
    }

    recent = list(
        db.form_submissions.find(
            {},
            {"_id": 0, "ref_id": 1, "service_name": 1,
             "status": 1, "submitted_at": 1}
        ).sort("submitted_at", DESCENDING).limit(10)
    )

    return {
        "total":     total,
        "users":     users,
        "by_scheme": by_scheme,
        "by_lang":   by_lang,
        "recent":    recent,
    }


# ── TRANSLATION CACHE ─────────────────────────────────────────────────────────

@_db_safe
def db_get_translation(cache_key: str) -> str | None:
    doc = _get_db().translation_cache.find_one(
        {"cache_key": cache_key}, {"_id": 0, "translated": 1}
    )
    return doc["translated"] if doc else None


@_db_safe
def db_set_translation(cache_key: str, translated: str) -> None:
    _get_db().translation_cache.update_one(
        {"cache_key": cache_key},
        {"$set": {"translated": translated, "created_at": _now()}},
        upsert=True,
    )


# ── VECTOR SEARCH -- SCHEME ELIGIBILITY ───────────────────────────────────────

@_db_safe
def db_find_matching_schemes(user_query_embedding: list,
                             state: str = None,
                             limit: int = 10) -> list[dict]:
    """
    Uses MongoDB Atlas Vector Search to find schemes matching a user's profile.
    Requires the 'scheme_vector_index' vector index to be created in Atlas UI.

    Args:
        user_query_embedding: Gemini embedding of the user's profile text
        state: optional state name to filter results (also always includes All India)
        limit: max number of results

    Returns:
        list of scheme dicts with scheme_name, state, eligibility, benefits,
        official_link, and vectorSearchScore
    """
    pipeline: list = [
        {
            "$vectorSearch": {
                "index":         "scheme_vector_index",
                "path":          "embedding",
                "queryVector":   user_query_embedding,
                "numCandidates": 100,
                "limit":         limit,
            }
        },
        {
            "$addFields": {
                "score": {"$meta": "vectorSearchScore"}
            }
        },
    ]

    if state:
        pipeline.append({
            "$match": {
                "$or": [
                    {"state": state},
                    {"state": "All India"},
                    {"state": {"$exists": False}},
                ]
            }
        })

    pipeline.append({
        "$project": {
            "_id": 0,
            "scheme_name": 1, "state": 1, "category": 1,
            "eligibility": 1, "benefits": 1,
            "official_link": 1, "score": 1,
        }
    })

    try:
        return list(_get_db().india_schemes.aggregate(pipeline))
    except Exception as e:
        print(f"Vector search failed: {e}")
        return []


# ── OTP -- LOGIN SECURITY ──────────────────────────────────────────────────────

@_db_safe
def db_create_otp(mobile: str, otp: str, ttl_seconds: int = 600) -> None:
    """
    Store a hashed OTP with expiry for the given mobile number.
    Overwrites any existing OTP for this mobile.
    TTL index on expires_at auto-deletes expired docs.
    """
    _get_db().otps.update_one(
        {"mobile": mobile},
        {"$set": {
            "mobile":     mobile,
            "otp_hash":   hashlib.sha256(otp.encode()).hexdigest(),
            "expires_at": time.time() + ttl_seconds,
            "attempts":   0,
            "created_at": _now(),
        }},
        upsert=True,
    )


@_db_safe
def db_verify_otp(mobile: str, otp: str) -> tuple[bool, str]:
    """
    Verify a submitted OTP against the stored hash.

    Returns:
        (True, "")               on success -- OTP is deleted after use
        (False, "error message") on failure
    """
    doc = _get_db().otps.find_one({"mobile": mobile})
    if not doc:
        return False, "OTP not found. Please request a new one."

    if doc["attempts"] >= 3:
        return False, "Too many failed attempts. Please request a new OTP."

    if time.time() > doc["expires_at"]:
        _get_db().otps.delete_one({"mobile": mobile})
        return False, "OTP has expired. Please request a new one."

    # Increment attempt count BEFORE checking (prevents timing-based attacks)
    _get_db().otps.update_one({"mobile": mobile}, {"$inc": {"attempts": 1}})

    if doc["otp_hash"] != hashlib.sha256(otp.encode()).hexdigest():
        return False, "Incorrect OTP. Please try again."

    # Delete OTP after successful use -- single-use enforcement
    _get_db().otps.delete_one({"mobile": mobile})
    return True, ""


@_db_safe
def db_check_login_attempts(mobile: str, ip: str) -> bool:
    """
    Rate-limit login attempts: max 5 per mobile+IP per 15 minutes.
    Returns True if the attempt is allowed, False if rate-limited.
    """
    key = f"login_{mobile}_{ip}"
    return db_check_rate_limit(key, "login_attempt", max_count=5, window_seconds=900)


# ── UTILITY ───────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── ADMIN -- SUBMISSIONS WITH USER DATA ───────────────────────────────────────

@_db_safe
def db_get_admin_submissions(limit: int = 200) -> list[dict]:
    """Return all submissions joined with basic user info for the admin panel."""
    db = _get_db()
    submissions = list(
        db.form_submissions.find(
            {},
            {"_id": 0, "ref_id": 1, "user_id": 1, "service_name": 1,
             "language": 1, "status": 1, "submitted_at": 1,
             "reviewed_at": 1, "admin_note": 1, "ip_address": 1}
        ).sort("submitted_at", DESCENDING).limit(limit)
    )

    # Enrich with user info
    user_ids = list({s["user_id"] for s in submissions})
    users_map = {}
    for uid in user_ids:
        try:
            from bson import ObjectId
            u = db.users.find_one(
                {"_id": ObjectId(str(uid))},
                {"_id": 0, "first_name": 1, "last_name": 1,
                 "mobile": 1, "email": 1, "state": 1, "district": 1}
            )
            if u:
                users_map[uid] = u
        except Exception:
            pass

    for s in submissions:
        u = users_map.get(s.get("user_id", ""), {})
        s["applicant_name"]  = f"{u.get('first_name','')} {u.get('last_name','')}".strip() or "Unknown"
        s["applicant_mobile"]= u.get("mobile", "--")
        s["applicant_email"] = u.get("email", "--")
        s["applicant_state"] = u.get("state", "--")
        s["applicant_district"] = u.get("district", "--")

    return submissions


@_db_safe
def db_update_submission_status(ref_id: str, status: str, admin_note: str = "") -> bool:
    """Approve or reject a submission. status must be 'approved' or 'rejected'."""
    if status not in ("approved", "rejected"):
        return False
    result = _get_db().form_submissions.update_one(
        {"ref_id": ref_id},
        {"$set": {
            "status":      status,
            "admin_note":  admin_note,
            "reviewed_at": _now(),
        }}
    )
    return result.modified_count > 0


@_db_safe
def db_get_submission_detail(ref_id: str) -> dict | None:
    """Get full submission record by ref_id for admin detail view."""
    return _get_db().form_submissions.find_one(
        {"ref_id": ref_id},
        {"_id": 0}
    )


# ══════════════════════════════════════════════════════════════════════════════
# AGENT MEMORY  (new — MCP document requirement §2)
# ══════════════════════════════════════════════════════════════════════════════
#
# Collections:
#   conversations      – every chat turn (user / assistant)
#   agent_memory       – persistent profile per user (auto-merged after each turn)
#   eligibility_results– history of eligibility checks per user
#
# These are added to init_mongo() indexes below so they work immediately.
# ══════════════════════════════════════════════════════════════════════════════

@_db_safe
def db_init_memory_indexes():
    """Create indexes for the three new memory collections.
    Called from init_mongo() — safe to call multiple times (idempotent)."""
    db = _get_db()

    # conversations
    db.conversations.create_index("user_id")
    db.conversations.create_index("session_id")
    db.conversations.create_index([("timestamp", DESCENDING)])
    # TTL — purge conversations older than 90 days
    db.conversations.create_index("timestamp", expireAfterSeconds=7776000)

    # agent_memory — one doc per user (upserted)
    db.agent_memory.create_index("user_id", unique=True)
    db.agent_memory.create_index("updated_at")

    # eligibility_results
    db.eligibility_results.create_index("user_id")
    db.eligibility_results.create_index([("checked_at", DESCENDING)])
    # TTL — purge results older than 30 days
    db.eligibility_results.create_index("checked_at", expireAfterSeconds=2592000)


# ── Conversation history ──────────────────────────────────────────────────────

@_db_safe
def db_save_conversation(user_id: str, session_id: str,
                         role: str, content: str,
                         lang: str = "en-IN",
                         scheme_name: str = None) -> None:
    """Append a single conversation turn to the conversations collection.

    Args:
        user_id    : registered user ObjectId string
        session_id : client-generated UUID for this chat session
        role       : "user" | "assistant"
        content    : message text
        lang       : BCP-47 language code
        scheme_name: optional scheme being discussed
    """
    _get_db().conversations.insert_one({
        "user_id":     str(user_id),
        "session_id":  session_id,
        "role":        role,
        "content":     content,
        "lang":        lang,
        "scheme_name": scheme_name,
        "timestamp":   _now(),
    })


@_db_safe
def db_get_conversation_history(user_id: str,
                                 session_id: str = None,
                                 limit: int = 20) -> list[dict]:
    """Return recent conversation turns for a user.

    If session_id is given, only turns from that session are returned.
    Results are ordered oldest-first so they can be fed directly to Gemini.
    """
    db     = _get_db()
    query  = {"user_id": str(user_id)}
    if session_id:
        query["session_id"] = session_id

    turns = list(
        db.conversations.find(query, {"_id": 0})
        .sort("timestamp", DESCENDING)
        .limit(limit)
    )
    turns.reverse()  # oldest first for Gemini
    return turns


# ── Agent memory (persistent profile) ────────────────────────────────────────

@_db_safe
def db_get_agent_memory(user_id: str) -> dict:
    """Return the persisted agent memory for a user.

    Returns an empty dict if no memory exists yet — callers should
    merge this with the live user profile before passing to Gemini.
    """
    doc = _get_db().agent_memory.find_one(
        {"user_id": str(user_id)}, {"_id": 0}
    )
    return doc or {}


@_db_safe
def db_update_agent_memory(user_id: str, new_facts: dict) -> None:
    """Upsert (merge) new facts into the agent memory for a user.

    Only keys present in new_facts are touched; existing keys are preserved.
    This lets the agent incrementally learn from each conversation turn.

    Example new_facts:
        {"state": "Telangana", "occupation": "farmer", "income": 150000}
    """
    if not new_facts:
        return
    set_payload = {f"memory.{k}": v for k, v in new_facts.items()}
    set_payload["user_id"]    = str(user_id)
    set_payload["updated_at"] = _now()

    _get_db().agent_memory.update_one(
        {"user_id": str(user_id)},
        {"$set": set_payload},
        upsert=True,
    )


# ── Eligibility results ───────────────────────────────────────────────────────

@_db_safe
def db_save_eligibility_result(user_id: str, result: dict,
                                scheme_name: str = None) -> None:
    """Persist the output of check_eligibility_india() for a user."""
    _get_db().eligibility_results.insert_one({
        "user_id":     str(user_id),
        "scheme_name": scheme_name,
        "result":      result,
        "checked_at":  _now(),
    })


@_db_safe
def db_get_last_eligibility_result(user_id: str) -> dict | None:
    """Return the most recent eligibility result for a user (or None)."""
    doc = _get_db().eligibility_results.find_one(
        {"user_id": str(user_id)},
        {"_id": 0},
        sort=[("checked_at", DESCENDING)],
    )
    return doc


# ══════════════════════════════════════════════════════════════════════════════
# HYBRID SEARCH — Atlas Text Search + Vector Search  (§4 from MCP document)
# ══════════════════════════════════════════════════════════════════════════════
#
# Architecture:
#   User Query → Atlas Search (exact keywords)
#                 ↓
#              Vector Search (semantic embedding)
#                 ↓
#              Merge & de-duplicate by scheme_name
#                 ↓
#              Return to Gemini
#
# Requires two Atlas indexes on india_schemes collection:
#   1. A standard text search index named "scheme_text_index" on
#      fields: scheme_name, eligibility, benefits, category
#   2. The existing vector search index "scheme_vector_index" on embedding
#
# ══════════════════════════════════════════════════════════════════════════════

@_db_safe
def db_hybrid_search_schemes(text_query: str,
                              query_embedding: list,
                              state: str = None,
                              limit: int = 10) -> list[dict]:
    """Hybrid Atlas Search + Vector Search for scheme discovery.

    Args:
        text_query       : raw natural-language query for Atlas text search
        query_embedding  : Gemini embedding of the query (may be empty list)
        state            : optional state filter
        limit            : total results to return

    Returns:
        Deduplicated, merged list of scheme dicts.
        Each dict has: scheme_name, state, category, eligibility, benefits,
                        official_link, score (float), source ("text"|"vector"|"both")
    """
    db = _get_db()
    half = max(1, limit // 2)

    # ── Branch A: Atlas full-text search ─────────────────────────────────────
    text_results: list[dict] = []
    try:
        text_pipeline: list = [
            {
                "$search": {
                    "index": "scheme_text_index",
                    "text": {
                        "query": text_query,
                        "path": ["scheme_name", "eligibility", "benefits", "category"],
                        "fuzzy": {"maxEdits": 1},
                    },
                }
            },
            {"$addFields": {"score": {"$meta": "searchScore"}}},
        ]
        if state:
            text_pipeline.append({
                "$match": {
                    "$or": [
                        {"state": state},
                        {"state": "All India"},
                        {"state": {"$exists": False}},
                    ]
                }
            })
        text_pipeline += [
            {"$limit": half},
            {
                "$project": {
                    "_id": 0,
                    "scheme_name": 1, "state": 1, "category": 1,
                    "eligibility": 1, "benefits": 1,
                    "official_link": 1, "score": 1,
                }
            },
        ]
        text_results = list(db.india_schemes.aggregate(text_pipeline))
    except Exception as e:
        # Atlas text index may not be configured yet — fall through gracefully
        print(f"Atlas text search unavailable (index not yet created?): {e}")

    # ── Branch B: Vector Search ───────────────────────────────────────────────
    vector_results: list[dict] = []
    if query_embedding:
        try:
            vector_results = db_find_matching_schemes(
                query_embedding, state=state, limit=half
            )
        except Exception as e:
            print(f"Vector search error in hybrid: {e}")

    # ── Merge & de-duplicate ──────────────────────────────────────────────────
    seen:   dict[str, dict] = {}   # keyed by scheme_name
    merged: list[dict]      = []

    def _add(doc: dict, source: str):
        name = doc.get("scheme_name", "").strip()
        if not name:
            return
        if name in seen:
            # Already seen — upgrade source label and keep higher score
            existing = seen[name]
            existing["source"] = "both"
            existing["score"]  = max(existing.get("score", 0), doc.get("score", 0))
        else:
            doc["source"] = source
            seen[name]    = doc
            merged.append(doc)

    for d in text_results:
        _add(d, "text")
    for d in vector_results:
        _add(d, "vector")

    # Sort: "both" first, then by score descending
    merged.sort(key=lambda x: (x["source"] != "both", -x.get("score", 0)))
    return merged[:limit]
