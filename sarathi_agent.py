"""
sarathi_agent.py  v10.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Google Cloud Agent Builder  +  Gemini 3.5 Flash  +  MongoDB MCP Server
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Stack:
  • LLM          : gemini-3.5-flash  via  Vertex AI (google-cloud-aiplatform)
  • Agent layer  : Google Cloud Agent Builder / Vertex AI Agent Engine
  • Database     : MongoDB Atlas  via  @mongodb-js/mongodb-mcp-server (MCP)
  • Fallback DB  : mongo_db.py (pymongo) when MCP server is unavailable

Auth (no API key on Cloud Run):
  • Production  : Application Default Credentials (gcloud auth / service account)
  • Local dev   : GEMINI_API_KEY in .env  OR  `gcloud auth application-default login`
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import os, json, sys, argparse
from typing import Any

# ── Gemini / Vertex AI SDK ────────────────────────────────────────────────────
# Unified google-genai SDK works for both Vertex AI (no key) and AI Studio (key).
try:
    from google import genai as _genai
    from google.genai import types as _gtypes
    _SDK_OK = True
except ImportError:
    _SDK_OK = False
    print("⚠️  google-genai not installed. Run: pip install google-genai --break-system-packages")

# ── MongoDB MCP client ────────────────────────────────────────────────────────
from mcp_client import init_mcp, get_mcp_client

# ── MongoDB direct fallback ────────────────────────────────────────────────────
from mongo_db import (
    db_save_submission,
    db_get_submissions_for_user,
    db_log_audit,
    db_save_conversation,
    db_get_conversation_history,
    db_get_agent_memory,
    db_update_agent_memory,
)

# ── Config ────────────────────────────────────────────────────────────────────
GEMINI_MODEL     = "gemini-3.5-flash"          # GA as of May 19 2026
EMBED_MODEL      = "text-embedding-005"         # 768-dim — matches Atlas index
GCP_PROJECT      = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
GCP_LOCATION     = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
GEMINI_API_KEY   = os.environ.get("GEMINI_API_KEY", "")


def _make_client() -> "_genai.Client | None":
    """
    Build a Gemini client preferring Vertex AI (free trial eligible).
    Falls back to API key for local development.
    """
    if not _SDK_OK:
        return None

    # Vertex AI path — used in production (Cloud Run ADC, no API key needed)
    if GCP_PROJECT:
        try:
            client = _genai.Client(
                vertexai=True,
                project=GCP_PROJECT,
                location=GCP_LOCATION,
            )
            print(f"[Agent] 🟢 Vertex AI client ready  ({GCP_PROJECT}/{GCP_LOCATION})")
            return client
        except Exception as e:
            print(f"[Agent] Vertex AI init failed ({e}), trying API key...")

    # API-key path — local dev convenience
    if GEMINI_API_KEY:
        try:
            client = _genai.Client(api_key=GEMINI_API_KEY)
            print("[Agent] 🟡 Gemini API-key client ready (dev mode)")
            return client
        except Exception as e:
            print(f"[Agent] API-key client failed: {e}")

    print("[Agent] ❌  No Gemini client — set GOOGLE_CLOUD_PROJECT or GEMINI_API_KEY")
    return None


# Module-level client (created once at import time)
_client: "_genai.Client | None" = _make_client()


# ── Sarathi tool definitions passed to Gemini function calling ────────────────
# These mirror the MCP server's capabilities; results are routed through
# the MCP client when available, or pymongo (mongo_db.py) as fallback.

SARATHI_TOOLS = [
    {
        "name": "sarathi_save_submission",
        "description": (
            "Save a citizen's government scheme application to MongoDB Atlas. "
            "Call this after collecting and confirming all required form fields."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "ref_id":         {"type": "string",  "description": "Unique reference ID (UUID)"},
                "user_id":        {"type": "string",  "description": "Registered user ObjectId"},
                "service_name":   {"type": "string",  "description": "Scheme name e.g. 'Rythu Bandhu'"},
                "encrypted_data": {"type": "string",  "description": "Fernet-encrypted JSON payload"},
                "integrity_hash": {"type": "string",  "description": "SHA-256 of plaintext"},
                "language":       {"type": "string",  "description": "BCP-47 code e.g. 'te-IN'"},
                "ip_address":     {"type": "string",  "description": "User's IP address"},
            },
            "required": ["ref_id", "user_id", "service_name",
                         "encrypted_data", "integrity_hash", "language"],
        },
    },
    {
        "name": "sarathi_get_submission",
        "description": "Retrieve a specific submission by reference ID for a given user.",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string"},
                "ref_id":  {"type": "string"},
            },
            "required": ["user_id", "ref_id"],
        },
    },
    {
        "name": "sarathi_list_submissions",
        "description": "List a user's past scheme submissions (most recent first).",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string"},
                "limit":   {"type": "integer", "description": "Max to return (default 10)"},
            },
            "required": ["user_id"],
        },
    },
    {
        "name": "sarathi_check_eligibility",
        "description": (
            "Ask Gemini to check whether a user is eligible for a given scheme "
            "based on their answers. Returns {eligible, reason, also_qualifies}."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "scheme_name": {"type": "string"},
                "answers":     {"type": "object", "description": "Field→value map"},
            },
            "required": ["scheme_name", "answers"],
        },
    },
    {
        "name": "sarathi_collect_form",
        "description": (
            "Ask the user a single question to collect one required form field. "
            "Call once per missing field, never batch questions."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "field_name":  {"type": "string", "description": "Field to collect"},
                "question":    {"type": "string", "description": "Question in user's language"},
                "field_type":  {"type": "string", "enum": ["text", "number", "date", "choice"]},
            },
            "required": ["field_name", "question"],
        },
    },
]


# ── Tool execution (routes through MCP → pymongo fallback) ────────────────────

def execute_tool(tool_name: str, args: dict) -> dict:
    """
    Execute a Sarathi tool call.
    MongoDB operations are routed through the MCP client when available,
    falling back to direct pymongo calls (mongo_db.py) otherwise.
    """
    mcp = get_mcp_client()

    # ── sarathi_save_submission ───────────────────────────────────────────────
    if tool_name == "sarathi_save_submission":
        if mcp:
            # Route through MCP → MongoDB Atlas insertOne
            result = mcp.call_tool("insertOne", {
                "collection": "form_submissions",
                "database":   os.environ.get("MONGODB_DB", "sarathi_db"),
                "document": {
                    "ref_id":         args["ref_id"],
                    "user_id":        args["user_id"],
                    "service_name":   args["service_name"],
                    "encrypted_data": args["encrypted_data"],
                    "integrity_hash": args["integrity_hash"],
                    "language":       args["language"],
                    "ip_address":     args.get("ip_address", "agent"),
                    "status":         "submitted",
                },
            })
            if result.get("error"):
                print(f"[MCP] insertOne failed: {result['error']}, falling back to pymongo")
                mcp = None  # fall through to pymongo

        if not mcp:
            db_save_submission(
                ref_id=args["ref_id"], user_id=args["user_id"],
                service_name=args["service_name"], encrypted=args["encrypted_data"],
                hash_val=args["integrity_hash"], lang=args["language"],
                ip=args.get("ip_address", "agent"),
            )
            db_log_audit(args["user_id"], "AGENT_SUBMIT", ref_id=args["ref_id"],
                         details=f"Service:{args['service_name']}|Lang:{args['language']}")

        return {"success": True, "ref_id": args["ref_id"],
                "message": f"Application saved. Reference ID: {args['ref_id']}"}

    # ── sarathi_get_submission ────────────────────────────────────────────────
    elif tool_name == "sarathi_get_submission":
        if mcp:
            result = mcp.call_tool("find", {
                "collection": "form_submissions",
                "database":   os.environ.get("MONGODB_DB", "sarathi_db"),
                "filter":     {"user_id": args["user_id"], "ref_id": args["ref_id"]},
                "limit":      1,
            })
            if not result.get("error") and isinstance(result, list) and result:
                return {"found": True, "submission": result[0]}

        rows = db_get_submissions_for_user(args["user_id"])
        match = next((r for r in rows if r["ref_id"] == args["ref_id"]), None)
        return {"found": bool(match), "submission": match} if match else \
               {"found": False, "message": "No submission found."}

    # ── sarathi_list_submissions ──────────────────────────────────────────────
    elif tool_name == "sarathi_list_submissions":
        limit = args.get("limit", 10)
        if mcp:
            result = mcp.call_tool("find", {
                "collection": "form_submissions",
                "database":   os.environ.get("MONGODB_DB", "sarathi_db"),
                "filter":     {"user_id": args["user_id"]},
                "limit":      limit,
                "sort":       {"created_at": -1},
            })
            if not result.get("error") and isinstance(result, list):
                return {"submissions": result, "count": len(result)}

        rows = db_get_submissions_for_user(args["user_id"])[:limit]
        return {"submissions": rows, "count": len(rows)}

    # ── sarathi_check_eligibility ─────────────────────────────────────────────
    elif tool_name == "sarathi_check_eligibility":
        if not _client:
            return {"eligible": False, "reason": "AI unavailable", "also_qualifies": []}
        try:
            prompt = (
                f"Check eligibility for: {args['scheme_name']}\n"
                f"User answers: {json.dumps(args['answers'], ensure_ascii=False)}\n"
                "Reply ONLY with valid JSON: "
                '{"eligible":true/false,"reason":"...","also_qualifies":[]}'
            )
            resp = _client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=_gtypes.GenerateContentConfig(temperature=0.0),
            )
            raw = (resp.text or "").strip().lstrip("```json").rstrip("```").strip()
            return json.loads(raw)
        except Exception as e:
            return {"eligible": False, "reason": str(e), "also_qualifies": []}

    # ── sarathi_collect_form ──────────────────────────────────────────────────
    elif tool_name == "sarathi_collect_form":
        return {"asking": args["field_name"], "question": args["question"]}

    return {"error": f"Unknown tool: {tool_name}"}


# ── Embedding (for vector search) ────────────────────────────────────────────

def get_embedding(text: str) -> list[float] | None:
    """
    Generate a 768-dim text embedding via Vertex AI text-embedding-005.
    Matches the existing Atlas Vector Search index dimensions.
    """
    if not _client:
        return None
    try:
        result = _client.models.embed_content(
            model=EMBED_MODEL,
            contents=text,
        )
        if result and result.embeddings:
            return list(result.embeddings[0].values)
    except Exception as e:
        print(f"[Agent] Embedding failed: {e}")
    return None


# ── Agent loop (Google Cloud Agent Builder style) ─────────────────────────────

def run_agent(
    user_message: str,
    conversation_history: list,
    user_id: str,
    scheme_name: str,
    lang: str = "en-IN",
) -> dict:
    """
    Single-turn Vertex AI agent call with Gemini 3.5 Flash + function calling.
    This is the Agent Builder / Agent Engine runtime loop.

    Returns {"reply": str, "tool_calls": list, "history": list}
    """
    if not _client or not _SDK_OK:
        return {
            "reply": "AI service unavailable. Please check configuration.",
            "tool_calls": [],
            "history": conversation_history,
        }

    lang_names = {
        "te-IN": "Telugu", "hi-IN": "Hindi", "en-IN": "English",
        "ta-IN": "Tamil",  "kn-IN": "Kannada", "ml-IN": "Malayalam",
        "or-IN": "Odia",   "pa-IN": "Punjabi", "mr-IN": "Marathi",
    }
    lang_name = lang_names.get(lang, "English")

    # Load agent memory from MongoDB (never re-ask known facts)
    memory = {}
    try:
        memory = db_get_agent_memory(user_id) or {}
    except Exception:
        pass
    memory_str = json.dumps(memory, ensure_ascii=False) if memory else "None"

    system_prompt = f"""You are Sarathi, a warm AI assistant helping Indian citizens apply \
for government welfare schemes. Current scheme: {scheme_name}.
Language: {lang_name} (BCP-47: {lang}). User ID: {user_id}.

Known facts about this user (do NOT ask again):
{memory_str}

Workflow:
1. Collect required fields conversationally, ONE question at a time.
2. Confirm all fields with the user before saving.
3. On confirmation, call sarathi_save_submission with the encrypted data.
4. Return the reference ID with a congratulatory message.

Rules:
- Always speak in {lang_name}. Never mix languages unless the user does.
- Never ask for Aadhaar in full — mask it as XXXX-XXXX-XXXX after capture.
- Be simple and patient; many users are rural citizens with limited literacy.
- Use sarathi_collect_form for each field, one at a time.
- Update agent memory via sarathi_check_eligibility when you learn new facts."""

    # Build Gemini tool declarations from SARATHI_TOOLS list
    tool_declarations = []
    for t in SARATHI_TOOLS:
        params = t["parameters"]
        # Convert JSON Schema → google.genai Schema type
        props = {}
        for pname, pdef in params.get("properties", {}).items():
            ptype = pdef.get("type", "string").upper()
            try:
                gtype = getattr(_gtypes.Type, ptype)
            except AttributeError:
                gtype = _gtypes.Type.STRING
            props[pname] = _gtypes.Schema(
                type=gtype,
                description=pdef.get("description", ""),
                enum=pdef.get("enum"),
            )
        schema = _gtypes.Schema(
            type=_gtypes.Type.OBJECT,
            properties=props,
            required=params.get("required", []),
        )
        tool_declarations.append(
            _gtypes.FunctionDeclaration(
                name=t["name"],
                description=t["description"],
                parameters=schema,
            )
        )

    gemini_tools = [_gtypes.Tool(function_declarations=tool_declarations)]

    # Build message history for the API call
    contents = []
    for turn in conversation_history:
        role = "user" if turn["role"] == "user" else "model"
        parts = turn.get("parts", [{"text": turn.get("content", "")}])
        contents.append(_gtypes.Content(role=role, parts=[
            _gtypes.Part(text=p["text"]) if "text" in p else _gtypes.Part(**p)
            for p in parts
        ]))
    contents.append(_gtypes.Content(
        role="user",
        parts=[_gtypes.Part(text=user_message)],
    ))

    tool_calls_made = []
    reply_text = ""
    history = list(conversation_history)
    history.append({"role": "user", "parts": [{"text": user_message}]})

    # ── Agentic loop: max 6 tool-use rounds (Agent Builder style) ─────────────
    for _round in range(6):
        try:
            response = _client.models.generate_content(
                model=GEMINI_MODEL,
                contents=contents,
                config=_gtypes.GenerateContentConfig(
                    system_instruction=system_prompt,
                    tools=gemini_tools,
                    temperature=0.2,
                    thinking_config=_gtypes.ThinkingConfig(
                        thinking_budget=1024,   # light reasoning for agentic tasks
                    ),
                ),
            )
        except Exception as e:
            print(f"[Agent] generate_content failed on round {_round}: {e}")
            reply_text = f"I encountered an issue. Please try again. ({e})"
            break

        candidate = response.candidates[0] if response.candidates else None
        if not candidate:
            break

        # Collect function calls from the response
        fn_calls = []
        for part in candidate.content.parts:
            if part.function_call and part.function_call.name:
                fn_calls.append(part.function_call)

        if not fn_calls:
            # Final text reply
            reply_text = "".join(
                part.text for part in candidate.content.parts
                if hasattr(part, "text") and part.text
            )
            history.append({"role": "model", "parts": [{"text": reply_text}]})
            break

        # Execute tool calls and feed results back
        fn_results_parts = []
        for fn in fn_calls:
            result = execute_tool(fn.name, dict(fn.args))
            tool_calls_made.append({"tool": fn.name, "args": dict(fn.args), "result": result})
            fn_results_parts.append(
                _gtypes.Part(
                    function_response=_gtypes.FunctionResponse(
                        name=fn.name,
                        response=result,
                    )
                )
            )

        # Append model function-call turn + tool result turn to contents
        contents.append(_gtypes.Content(
            role="model",
            parts=[_gtypes.Part(function_call=_gtypes.FunctionCall(
                name=fn.name, args=dict(fn.args)
            )) for fn in fn_calls],
        ))
        contents.append(_gtypes.Content(role="user", parts=fn_results_parts))

        history.append({
            "role": "model",
            "parts": [{"function_call": {"name": fn.name, "args": dict(fn.args)}}
                      for fn in fn_calls],
        })

    # Persist conversation + update memory
    try:
        for turn in [
            {"user_id": user_id, "role": "user",      "content": user_message, "lang": lang},
            {"user_id": user_id, "role": "assistant",  "content": reply_text,   "lang": lang},
        ]:
            db_save_conversation(
                user_id=turn["user_id"], session_id="agent",
                role=turn["role"], content=turn["content"], lang=turn["lang"],
                scheme_name=scheme_name,
            )
    except Exception:
        pass

    return {"reply": reply_text, "tool_calls": tool_calls_made, "history": history}


# ── CLI test / MCP launcher ───────────────────────────────────────────────────

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    parser = argparse.ArgumentParser(description="Sarathi Agent v10")
    parser.add_argument("--mcp",  action="store_true", help="Start MongoDB MCP server only")
    parser.add_argument("--test", action="store_true", help="Run interactive agent test")
    args = parser.parse_args()

    if args.mcp:
        print("Starting MongoDB MCP server...")
        mcp = init_mcp()
        if mcp:
            print(f"MCP server running. Tools: {[t['name'] for t in mcp.list_tools()]}")
            try:
                import signal
                signal.pause()
            except (AttributeError, KeyboardInterrupt):
                pass
            finally:
                mcp.stop()
        else:
            sys.exit(1)

    elif args.test:
        print(f"Sarathi Agent v10 — {GEMINI_MODEL} via Vertex AI")
        print(f"Initializing MCP server...")
        init_mcp()
        print("Type a message (Ctrl+C to quit):\n")
        history: list = []
        try:
            while True:
                msg = input("You: ").strip()
                if not msg:
                    continue
                result = run_agent(
                    user_message=msg,
                    conversation_history=history,
                    user_id="test-user",
                    scheme_name="Rythu Bandhu",
                    lang="en-IN",
                )
                print(f"\nSarathi: {result['reply']}")
                if result["tool_calls"]:
                    print(f"[Tools used: {[t['tool'] for t in result['tool_calls']]}]")
                print()
                history = result["history"]
        except KeyboardInterrupt:
            mcp = get_mcp_client()
            if mcp:
                mcp.stop()
            print("\nBye!")
    else:
        parser.print_help()
