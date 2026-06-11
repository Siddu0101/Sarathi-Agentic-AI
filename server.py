"""
Project Sarathi — Elite Production Server v8.1  (MongoDB MCP Edition)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Hackathon track : Google Cloud Rapid Agent Hackathon — MongoDB MCP partner
AI backbone     : Gemini 2.0 Flash  (gemini-3.5-flash)
Database        : MongoDB Atlas  (via pymongo + MongoDB MCP Server)
MCP integration : @mongodb-js/mongodb-mcp-server  (see mcp_config.json)
Agent builder   : Google Cloud Agent Builder (Vertex AI Agent)

Key changes from v3.1:
  • SQLite / Firestore removed — MongoDB Atlas is the single data store
  • All db_* functions delegated to mongo_db.py
  • Gemini model upgraded to gemini-3.5-flash
  • MCP server wires Gemini Agent Builder directly to Atlas collections
  • mcp_config.json declares tools for Agent Builder's function calling
  • v8.1: Agent memory, hybrid search, gevent-only async mode
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

# ── gevent monkey-patch MUST be the very first executable line ────────────
# Must run before any stdlib module (socket, threading, ssl …) is imported.
# On Cloud Run: gunicorn --worker-class gevent already called patch_all(),
#   this call is then a cheap no-op.
# On localhost (python server.py): this call is required for gevent to work.
import gevent.monkey
gevent.monkey.patch_all()
# ─────────────────────────────────────────────────────────────────────────────

import os, uuid, json, hashlib, hmac as hmac_module, time, base64, io
import json
from datetime import datetime, timedelta
from functools import wraps

from flask import Flask, render_template, jsonify, request, session, redirect, flash, abort, send_file
from flask_socketio import SocketIO, emit, join_room
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv

load_dotenv()

# ── Cryptography ──────────────────────────────────────────────────────────
try:
    from cryptography.fernet import Fernet
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False

try:
    import qrcode
    from PIL import Image
    QR_AVAILABLE = True
except ImportError:
    QR_AVAILABLE = False

try:
    from google.cloud import speech as gcp_speech
    GCP_STT = True
except ImportError:
    GCP_STT = False

try:
    from google.cloud import texttospeech as gcp_tts
    GCP_TTS_AVAILABLE = True
except ImportError:
    GCP_TTS_AVAILABLE = False

try:
    from google.cloud import translate_v2 as translate
    GCP_TRANSLATE = True
except ImportError:
    GCP_TRANSLATE = False

try:
    from google import genai as _genai_module
    from google.genai import types as _genai_types
    GEMINI_AVAILABLE = True

    _gcp_project  = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
    _gcp_location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
    _gemini_key   = os.environ.get("GEMINI_API_KEY", "")

    if _gcp_project:
        # Production path — Vertex AI (covered by $300 free trial, no API key)
        genai_client = _genai_module.Client(
            vertexai=True,
            project=_gcp_project,
            location=_gcp_location,
        )
        print(f"[Server] Gemini via Vertex AI ({_gcp_project}/{_gcp_location})")
    elif _gemini_key:
        # Dev path — direct API key
        genai_client = _genai_module.Client(api_key=_gemini_key)
        print("[Server] Gemini via API key (dev mode)")
    else:
        genai_client = None
except ImportError:
    GEMINI_AVAILABLE = False
    genai_client = None

try:
    from google.cloud import secretmanager
    SECRET_MGR = True
except ImportError:
    SECRET_MGR = False

# ── MongoDB MCP Partner Integration ───────────────────────────────────────────
# All persistence is handled by MongoDB Atlas via mongo_db.py.
# The MongoDB MCP server (@mongodb-js/mongodb-mcp-server) defined in
# mcp_config.json gives Gemini Agent Builder direct tool access to Atlas.
from mongo_db import (
    init_mongo,
    db_get_user_by_mobile,
    db_get_user_by_id,
    db_create_user,
    db_save_submission,
    db_get_submissions_for_user,
    db_log_audit,
    db_check_rate_limit,
    db_get_admin_stats,
    db_get_translation,
    db_set_translation,
    db_update_password,
    # Feature 1 — Eligibility
    db_find_matching_schemes,
    # Feature 3 — Secure OTP login
    db_create_otp,
    db_verify_otp,
    db_check_login_attempts,
    db_get_admin_submissions,
    db_update_submission_status,
    db_get_submission_detail,
    DBNotAvailable,
    # Feature 4 — Agent Memory (MCP document §2)
    db_save_conversation,
    db_get_conversation_history,
    db_get_agent_memory,
    db_update_agent_memory,
    db_save_eligibility_result,
    db_get_last_eligibility_result,
    # Feature 5 — Hybrid Search (MCP document §4)
    db_hybrid_search_schemes,
)

# ════════════════════════════════════════════════════════════════════════
app = Flask(__name__)
app.secret_key = os.environ.get("SARATHI_SECRET", os.urandom(32).hex())
app.jinja_env.filters["enumerate"] = enumerate
app.jinja_env.filters["translate"] = lambda text, lang: translate_text(str(text), lang) if text else ""
CORS(app, origins="*")

# ── SocketIO — gevent mode only ───────────────────────────────────────────────
# gevent.monkey.patch_all() was called at the top of this file (before any
# imports), so gevent owns the stdlib. async_mode must match — "gevent" here,
# --worker-class gevent in the Dockerfile CMD. No eventlet, no threading.
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="gevent",
                    ping_timeout=60, ping_interval=25,
                    logger=False, engineio_logger=False)

# ── Session security ──────────────────────────────────────────────────
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=8)
app.config["SESSION_COOKIE_HTTPONLY"]    = True
app.config["SESSION_COOKIE_SAMESITE"]    = "Lax"
app.config["SESSION_COOKIE_SECURE"]      = os.environ.get("FLASK_ENV") == "production"

# ── GCP credentials check ─────────────────────────────────────────────
def _gcp_creds_ok() -> bool:
    adc = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
    if adc:
        return os.path.exists(adc)
    try:
        import google.auth
        creds, _ = google.auth.default()
        return creds is not None
    except Exception:
        return False

_GCP_CREDS_OK = _gcp_creds_ok()

USE_GOOGLE_STT = os.environ.get("USE_GOOGLE_STT",  "true").lower() == "true" and GCP_STT          and _GCP_CREDS_OK
USE_GOOGLE_TTS = os.environ.get("USE_GOOGLE_TTS",  "true").lower() == "true" and GCP_TTS_AVAILABLE and _GCP_CREDS_OK
USE_GEMINI     = os.environ.get("USE_GEMINI",      "true").lower() == "true" and GEMINI_AVAILABLE  and genai_client is not None
USE_TRANSLATE  = os.environ.get("USE_TRANSLATE",   "true").lower() == "true" and GCP_TRANSLATE     and _GCP_CREDS_OK
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "SarathiAdmin@2026!")

# ════════════════════════════════════════════════════════════════════════
# ENCRYPTION
# ════════════════════════════════════════════════════════════════════════
ENCRYPTION_KEY_FILE = "sarathi_enc.key"

def _load_key_from_secret_manager():
    sm_key = os.environ.get("SECRET_MANAGER_KEY_NAME")
    if not sm_key or not SECRET_MGR:
        return None
    try:
        client = secretmanager.SecretManagerServiceClient()
        response = client.access_secret_version(request={"name": sm_key})
        return response.payload.data
    except Exception as e:
        print(f"⚠️  Secret Manager error: {e}")
        return None

def load_or_create_encryption_key():
    if not CRYPTO_AVAILABLE:
        return None
    raw = _load_key_from_secret_manager()
    if raw:
        return Fernet(raw)
    if os.path.exists(ENCRYPTION_KEY_FILE):
        with open(ENCRYPTION_KEY_FILE, "rb") as f:
            return Fernet(f.read())
    key = Fernet.generate_key()
    with open(ENCRYPTION_KEY_FILE, "wb") as f:
        f.write(key)
    os.chmod(ENCRYPTION_KEY_FILE, 0o600)
    print("🔑 New encryption key generated.")
    return Fernet(key)

fernet = load_or_create_encryption_key()

def encrypt_data(data: str) -> str:
    return fernet.encrypt(data.encode()).decode() if fernet else data

def decrypt_data(token: str) -> str:
    if fernet:
        try:
            return fernet.decrypt(token.encode()).decode()
        except Exception:
            return "[Decryption Failed]"
    return token

def compute_integrity_hash(data: str, salt: str) -> str:
    # FIX: key must be bytes
    key = (str(app.secret_key) + salt).encode("utf-8")
    return hmac_module.new(key, data.encode("utf-8"), hashlib.sha256).hexdigest()

# ════════════════════════════════════════════════════════════════════════
# DATABASE
# ════════════════════════════════════════════════════════════════════════
# ── MongoDB initialisation ────────────────────────────────────────────────────
try:
    init_mongo()
except Exception as _mongo_err:
    print(f"⚠️  MongoDB connection failed: {_mongo_err}")
    print("   Set MONGODB_URI in your .env file and ensure Atlas is reachable.")



# ════════════════════════════════════════════════════════════════════════
# FIELD VALIDATION (v3.1 — pattadar relaxed, optional fields skip)
# ════════════════════════════════════════════════════════════════════════
import re

OPTIONAL_FIELDS = {"village", "mandal", "district", "address", "nominee", "remarks"}

FIELD_VALIDATORS = {
    "aadhaar":    {"pattern": r"^\d{12}$",                "msg": "Aadhaar must be exactly 12 digits."},
    "mobile":     {"pattern": r"^\d{10}$",                "msg": "Mobile must be exactly 10 digits."},
    "pincode":    {"pattern": r"^\d{6}$",                 "msg": "Pincode must be 6 digits."},
    "ifsc":       {"pattern": r"^[A-Z]{4}0[A-Z0-9]{6}$", "msg": "IFSC must be 11 chars (e.g. SBIN0001234)."},
    "pan":        {"pattern": r"^[A-Z]{5}[0-9]{4}[A-Z]$","msg": "PAN must be 10 chars (e.g. ABCDE1234F)."},
    "name":       {"min_len": 2, "no_all_digits": True,   "msg": "Name must be ≥2 chars and not all digits."},
    "pattadar":   {"pattern": r"^[A-Za-z0-9/\-]{4,20}$", "msg": "Pattadar No. must be 4–20 alphanumeric chars."},
    "land_acres": {"pattern": r"^\d+(\.\d{1,4})?$",      "msg": "Enter land in acres, e.g. 2 or 2.50."},
    "account":    {"pattern": r"^\d{9,18}$",              "msg": "Bank account must be 9–18 digits."},
    "age":        {"pattern": r"^\d{1,3}$",               "msg": "Age must be a number."},
    "income":     {"pattern": r"^\d+$",                   "msg": "Income must be a number in rupees."},
}

def validate_field(field_id: str, value: str) -> tuple[bool, str]:
    fid = field_id.lower().strip()
    if fid in OPTIONAL_FIELDS:
        return True, ""
    if not value or not value.strip():
        return False, f"{field_id.replace('_',' ').title()} cannot be empty."
    v = value.strip()
    for key, rules in FIELD_VALIDATORS.items():
        if key in fid:
            if "pattern" in rules and not re.match(rules["pattern"], v):
                return False, rules["msg"]
            if "min_len" in rules and len(v) < rules["min_len"]:
                return False, rules["msg"]
            if rules.get("no_all_digits") and v.isdigit():
                return False, rules["msg"]
    return True, ""

# ════════════════════════════════════════════════════════════════════════
# GOOGLE CLOUD TTS
# ════════════════════════════════════════════════════════════════════════
# ── State → Primary Language mapping (Indian states) ─────────────────────────
STATE_LANGUAGE_MAP = {
    "andhra pradesh": "te-IN",      "arunachal pradesh": "en-IN",
    "assam": "as-IN",               "bihar": "hi-IN",
    "chhattisgarh": "hi-IN",        "goa": "kok-IN",
    "gujarat": "gu-IN",             "haryana": "hi-IN",
    "himachal pradesh": "hi-IN",    "jharkhand": "hi-IN",
    "karnataka": "kn-IN",           "kerala": "ml-IN",
    "madhya pradesh": "hi-IN",      "maharashtra": "mr-IN",
    "manipur": "mni-IN",            "meghalaya": "en-IN",
    "mizoram": "en-IN",             "nagaland": "en-IN",
    "odisha": "or-IN",              "punjab": "pa-IN",
    "rajasthan": "hi-IN",           "sikkim": "ne-IN",
    "tamil nadu": "ta-IN",          "telangana": "te-IN",
    "tripura": "bn-IN",             "uttar pradesh": "hi-IN",
    "uttarakhand": "hi-IN",         "west bengal": "bn-IN",
    # Union Territories
    "delhi": "hi-IN",               "jammu and kashmir": "ur-IN",
    "ladakh": "hi-IN",              "chandigarh": "pa-IN",
    "puducherry": "ta-IN",          "lakshadweep": "ml-IN",
    "dadra and nagar haveli": "gu-IN", "daman and diu": "gu-IN",
    "andaman and nicobar": "hi-IN",
}

def get_user_language(state: str) -> str:
    """Return BCP-47 language code for a given Indian state."""
    key = state.strip().lower()
    lang = STATE_LANGUAGE_MAP.get(key, "en-IN")
    # Fallback unsupported GCP TTS langs to closest supported
    FALLBACK = {"kok-IN":"mr-IN","mni-IN":"as-IN","as-IN":"hi-IN",
                "ne-IN":"hi-IN","or-IN":"hi-IN"}
    return FALLBACK.get(lang, lang)

LANG_DISPLAY = {
    "te-IN":"తెలుగు","hi-IN":"हिन्दी","en-IN":"English","ta-IN":"தமிழ்",
    "kn-IN":"ಕನ್ನಡ","ml-IN":"മലയാളം","bn-IN":"বাংলা","mr-IN":"मराठी",
    "gu-IN":"ગુજરાતી","pa-IN":"ਪੰਜਾਬੀ","ur-IN":"اردو","or-IN":"ଓଡ଼ିଆ",
    "as-IN":"অসমীয়া","ne-IN":"नेपाली","kok-IN":"कोंकणी","mni-IN":"মৈতৈলোন্",
}

WAVENET_VOICES = {
    "te-IN": ("te-IN-Standard-A","FEMALE"),
    "hi-IN": ("hi-IN-Wavenet-B", "MALE"),
    "en-IN": ("en-IN-Wavenet-A", "FEMALE"),
    "ta-IN": ("ta-IN-Wavenet-A", "FEMALE"),
    "kn-IN": ("kn-IN-Wavenet-A", "FEMALE"),
    "ml-IN": ("ml-IN-Wavenet-A", "FEMALE"),
    "bn-IN": ("bn-IN-Wavenet-A", "FEMALE"),
    "mr-IN": ("mr-IN-Wavenet-A", "FEMALE"),
    "gu-IN": ("gu-IN-Wavenet-A", "FEMALE"),
    "pa-IN": ("pa-IN-Standard-A","FEMALE"),
    "ur-IN": ("ur-IN-Standard-A","MALE"),
    "as-IN": ("as-IN-Standard-A","FEMALE"),
    "or-IN": ("or-IN-Standard-A","FEMALE"),
    "ne-IN": ("hi-IN-Wavenet-B", "MALE"),   # Nepali fallback
    "kok-IN":("mr-IN-Wavenet-A","FEMALE"),  # Konkani fallback
    "mni-IN":("as-IN-Standard-A","FEMALE"), # Manipuri fallback
}

def synthesize_speech_gcp(text: str, lang: str) -> bytes | None:
    if not USE_GOOGLE_TTS:
        return None
    try:
        client = gcp_tts.TextToSpeechClient()
        vname, gender = WAVENET_VOICES.get(lang, ("en-IN-Wavenet-A","FEMALE"))
        ssml_gender = gcp_tts.SsmlVoiceGender.FEMALE if gender == "FEMALE" else gcp_tts.SsmlVoiceGender.MALE
        response = client.synthesize_speech(
            input=gcp_tts.SynthesisInput(text=text),
            voice=gcp_tts.VoiceSelectionParams(language_code=lang, name=vname, ssml_gender=ssml_gender),
            audio_config=gcp_tts.AudioConfig(
                audio_encoding=gcp_tts.AudioEncoding.MP3,
                speaking_rate=0.88, pitch=0.0))
        return response.audio_content
    except Exception as e:
        print(f"TTS error: {e}"); return None

# ════════════════════════════════════════════════════════════════════════
# GOOGLE CLOUD TRANSLATE
# ════════════════════════════════════════════════════════════════════════
LANG_BCP_TO_GCP = {
    "te-IN":"te","hi-IN":"hi","ta-IN":"ta","kn-IN":"kn",
    "ml-IN":"ml","bn-IN":"bn","mr-IN":"mr","gu-IN":"gu",
    "pa-IN":"pa","ur-IN":"ur","en-IN":"en","or-IN":"or",
    "as-IN":"as","ne-IN":"ne","kok-IN":"kok","mni-IN":"mni",
}

def translate_text(text: str, target_lang_bcp: str) -> str:
    if target_lang_bcp == "en-IN": return text
    cache_key = hashlib.md5(f"{text}:{target_lang_bcp}".encode()).hexdigest()
    cached = db_get_translation(cache_key)
    if cached: return cached
    if not USE_TRANSLATE: return text
    try:
        client = translate.Client()
        result = client.translate(text, target_language=LANG_BCP_TO_GCP.get(target_lang_bcp,"en"))
        translated = result["translatedText"]
        db_set_translation(cache_key, translated)
        return translated
    except Exception as e:
        print(f"Translate error: {e}"); return text

def detect_language(text: str) -> str:
    if not USE_TRANSLATE: return "en-IN"
    try:
        client = translate.Client()
        detected = client.detect_language(text)["language"]
        return {v: k for k, v in LANG_BCP_TO_GCP.items()}.get(detected, "en-IN")
    except Exception:
        return "en-IN"

# ════════════════════════════════════════════════════════════════════════
# GEMINI AI
# ════════════════════════════════════════════════════════════════════════
SCHEMES_CONTEXT = """
India Government Schemes (25 National Schemes):
AGRICULTURE: PM-KISAN (₹6000/yr farmers), PMFBY (crop insurance)
HEALTH: Ayushman Bharat-PMJAY (₹5L health cover), Swachh Bharat Mission, Jal Jeevan Mission
FINANCE: PM Jan Dhan Yojana (zero-balance accounts), PM Mudra Yojana (loans ₹50K-10L),
  PMJJBY (life insurance ₹2L), PMSBY (accident insurance ₹2L), Atal Pension Yojana
HOUSING/WELFARE: PMAY (housing for all), PM Ujjwala Yojana (LPG connections),
  MGNREGA (100 days employment), PM SVANidhi (street vendors ₹10K), PM Vishwakarma (artisans)
ENERGY: PM Surya Ghar (300 units free solar electricity), PMGSY (rural roads)
WOMEN/CHILD: Lakhpati Didi (SHG women ₹1L+/yr), Sukanya Samriddhi (girl child savings),
  Poshan Abhiyaan (nutrition)
SKILLS/EMPLOYMENT: PMKVY (free skill training), Stand-Up India (SC/ST/women loans ₹10L-1Cr),
  PM-SYM (unorganized worker pension ₹3000/month)
HEALTHCARE/AGRICULTURE: PMBJP (generic medicines), PMMSY (fisheries development)
"""

# ── 25 National Scheme Data ───────────────────────────────────────────────────
SCHEME_DATA = {
  "pm_kisan": {
    "id":"pm_kisan","icon":"🌾","color":"#2e7d32","color_light":"#388e3c",
    "name":"PM-KISAN","full_name":"Pradhan Mantri Kisan Samman Nidhi",
    "ministry":"Ministry of Agriculture & Farmers Welfare, Govt of India",
    "tagline":"₹6,000 per year income support for all eligible farmer families",
    "benefit_pill":"₹2,000 × 3 installments/year","category":"Agriculture & Farmers",
    "description":"Under PM-KISAN, eligible landholding farmer families receive ₹6,000 per year as financial support, disbursed in three equal installments of ₹2,000 directly into their bank accounts.",
    "eligibility":"Landholding farmer families. Excludes institutional landholders, govt employees, and income-tax payers.",
    "documents":["Aadhaar Card","Land Ownership Records","Bank Passbook (IFSC)","Mobile Number"],
    "external_url":"https://pmkisan.gov.in",
    "fields":[
      {"id":"name","label_en":"Farmer Full Name","numeric":False,"id_field":False,"is_name":True},
      {"id":"aadhaar","label_en":"Aadhaar Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"mobile","label_en":"Mobile Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"account","label_en":"Bank Account Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"ifsc","label_en":"Bank IFSC Code","numeric":False,"id_field":True,"is_name":False},
      {"id":"land_acres","label_en":"Agricultural Land (Acres)","numeric":True,"id_field":False,"is_name":False},
      {"id":"state","label_en":"State","numeric":False,"id_field":False,"is_name":False},
    ]
  },
  "pmfby": {
    "id":"pmfby","icon":"🌱","color":"#1b5e20","color_light":"#2e7d32",
    "name":"PMFBY","full_name":"Pradhan Mantri Fasal Bima Yojana",
    "ministry":"Ministry of Agriculture & Farmers Welfare, Govt of India",
    "tagline":"Comprehensive crop insurance protecting farmers from natural calamities",
    "benefit_pill":"Low premium, full crop loss coverage","category":"Agriculture & Farmers",
    "description":"PMFBY provides financial support to farmers suffering crop loss due to unforeseen events like natural calamities, pests and diseases. Premium: Kharif 2%, Rabi 1.5%, Commercial/Horticulture 5%.",
    "eligibility":"All farmers growing notified crops in notified areas. Compulsory for loanee farmers, optional for others.",
    "documents":["Aadhaar Card","Land Records / Khasra-Khatauni","Bank Passbook","Sowing Certificate","Mobile Number"],
    "external_url":"https://pmfby.gov.in",
    "fields":[
      {"id":"name","label_en":"Farmer Full Name","numeric":False,"id_field":False,"is_name":True},
      {"id":"aadhaar","label_en":"Aadhaar Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"mobile","label_en":"Mobile Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"account","label_en":"Bank Account Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"ifsc","label_en":"Bank IFSC Code","numeric":False,"id_field":True,"is_name":False},
      {"id":"land_acres","label_en":"Land under Cultivation (Acres)","numeric":True,"id_field":False,"is_name":False},
      {"id":"crop_type","label_en":"Crop Type (e.g. Wheat, Rice)","numeric":False,"id_field":False,"is_name":False},
      {"id":"state","label_en":"State","numeric":False,"id_field":False,"is_name":False},
    ]
  },
  "ayushman_bharat": {
    "id":"ayushman_bharat","icon":"🏥","color":"#1565c0","color_light":"#1976d2",
    "name":"Ayushman Bharat","full_name":"Pradhan Mantri Jan Arogya Yojana (PM-JAY)",
    "ministry":"Ministry of Health & Family Welfare, Govt of India",
    "tagline":"₹5 lakh health cover per family per year for secondary & tertiary care",
    "benefit_pill":"₹5 Lakh/family/year — cashless treatment","category":"Health & Sanitation",
    "description":"Ayushman Bharat PM-JAY is India's flagship health assurance mission. It provides ₹5 lakh per family per year for cashless hospitalization at empanelled public and private hospitals across India.",
    "eligibility":"Families identified based on SECC database. BPL families, specific occupational criteria as per SECC data.",
    "documents":["Aadhaar Card","Ration Card","Mobile Number","Family details"],
    "external_url":"https://pmjay.gov.in",
    "fields":[
      {"id":"name","label_en":"Applicant Full Name","numeric":False,"id_field":False,"is_name":True},
      {"id":"aadhaar","label_en":"Aadhaar Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"mobile","label_en":"Mobile Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"age","label_en":"Age","numeric":True,"id_field":False,"is_name":False},
      {"id":"gender","label_en":"Gender (Male/Female/Other)","numeric":False,"id_field":False,"is_name":False},
      {"id":"district","label_en":"District","numeric":False,"id_field":False,"is_name":False},
      {"id":"state","label_en":"State","numeric":False,"id_field":False,"is_name":False},
    ]
  },
  "swachh_bharat": {
    "id":"swachh_bharat","icon":"🚽","color":"#e65100","color_light":"#f57c00",
    "name":"Swachh Bharat Mission","full_name":"Swachh Bharat Mission (Grameen)",
    "ministry":"Ministry of Jal Shakti, Govt of India",
    "tagline":"Free household toilet construction for BPL families — clean India campaign",
    "benefit_pill":"₹12,000 incentive for toilet construction","category":"Health & Sanitation",
    "description":"Swachh Bharat Mission aims to eliminate open defecation and ensure access to sanitation. Eligible households get ₹12,000 (₹10,000 from Centre + ₹2,000 from State) for toilet construction.",
    "eligibility":"BPL households without existing toilets. SC/ST households, small/marginal farmers, landless agricultural laborers.",
    "documents":["Aadhaar Card","BPL Card / Ration Card","Mobile Number","Address Proof","Bank Passbook"],
    "external_url":"https://swachhbharatmission.gov.in",
    "fields":[
      {"id":"name","label_en":"Applicant Full Name","numeric":False,"id_field":False,"is_name":True},
      {"id":"aadhaar","label_en":"Aadhaar Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"mobile","label_en":"Mobile Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"account","label_en":"Bank Account Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"ifsc","label_en":"Bank IFSC Code","numeric":False,"id_field":True,"is_name":False},
      {"id":"village","label_en":"Village / Ward","numeric":False,"id_field":False,"is_name":False},
      {"id":"district","label_en":"District","numeric":False,"id_field":False,"is_name":False},
      {"id":"state","label_en":"State","numeric":False,"id_field":False,"is_name":False},
    ]
  },
  "jal_jeevan": {
    "id":"jal_jeevan","icon":"💧","color":"#0277bd","color_light":"#0288d1",
    "name":"Jal Jeevan Mission","full_name":"Jal Jeevan Mission (Har Ghar Jal)",
    "ministry":"Ministry of Jal Shakti, Govt of India",
    "tagline":"Safe tap water connection to every rural household — Har Ghar Jal",
    "benefit_pill":"Free tap water connection","category":"Health & Sanitation",
    "description":"Jal Jeevan Mission aims to provide safe and adequate drinking water through household tap connections to every rural household by 2024 with minimum service level of 55 LPCD.",
    "eligibility":"All rural households without functional household tap connections.",
    "documents":["Aadhaar Card","Address Proof","Land Record / House Ownership","Mobile Number"],
    "external_url":"https://jaljeevanmission.gov.in",
    "fields":[
      {"id":"name","label_en":"Household Head Full Name","numeric":False,"id_field":False,"is_name":True},
      {"id":"aadhaar","label_en":"Aadhaar Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"mobile","label_en":"Mobile Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"village","label_en":"Village Name","numeric":False,"id_field":False,"is_name":False},
      {"id":"district","label_en":"District","numeric":False,"id_field":False,"is_name":False},
      {"id":"state","label_en":"State","numeric":False,"id_field":False,"is_name":False},
    ]
  },
  "jan_dhan": {
    "id":"jan_dhan","icon":"🏦","color":"#4a148c","color_light":"#6a1b9a",
    "name":"PM Jan Dhan Yojana","full_name":"Pradhan Mantri Jan Dhan Yojana (PMJDY)",
    "ministry":"Ministry of Finance, Govt of India",
    "tagline":"Zero-balance savings account with ₹2 lakh accident insurance cover",
    "benefit_pill":"Zero-balance account + ₹2L insurance","category":"Financial Inclusion",
    "description":"PMJDY ensures universal access to banking services. Features include zero-balance account, RuPay debit card, ₹2 lakh accidental insurance, ₹30,000 life cover, and overdraft facility up to ₹10,000.",
    "eligibility":"Any Indian citizen above 10 years without a bank account.",
    "documents":["Aadhaar Card","Voter ID / Passport (any one ID proof)","Passport size photo","Mobile Number"],
    "external_url":"https://pmjdy.gov.in",
    "fields":[
      {"id":"name","label_en":"Full Name","numeric":False,"id_field":False,"is_name":True},
      {"id":"aadhaar","label_en":"Aadhaar Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"mobile","label_en":"Mobile Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"age","label_en":"Age","numeric":True,"id_field":False,"is_name":False},
      {"id":"gender","label_en":"Gender","numeric":False,"id_field":False,"is_name":False},
      {"id":"district","label_en":"District","numeric":False,"id_field":False,"is_name":False},
      {"id":"state","label_en":"State","numeric":False,"id_field":False,"is_name":False},
    ]
  },
  "mudra_yojana": {
    "id":"mudra_yojana","icon":"💼","color":"#bf360c","color_light":"#d84315",
    "name":"PM Mudra Yojana","full_name":"Pradhan Mantri Mudra Yojana (PMMY)",
    "ministry":"Ministry of Finance, Govt of India",
    "tagline":"Collateral-free loans up to ₹10 lakh for small businesses & entrepreneurs",
    "benefit_pill":"Shishu ₹50K | Kishore ₹5L | Tarun ₹10L","category":"Financial Inclusion",
    "description":"PMMY provides micro-credit loans to non-corporate, non-farm small and micro enterprises. Three loan categories: Shishu (up to ₹50,000), Kishore (₹50,001-5 lakh), Tarun (₹5-10 lakh).",
    "eligibility":"Non-farm micro/small enterprises. No collateral required. Any Indian citizen with viable business plan.",
    "documents":["Aadhaar Card","PAN Card","Bank Statement (6 months)","Business proof","Mobile Number"],
    "external_url":"https://mudra.org.in",
    "fields":[
      {"id":"name","label_en":"Applicant Full Name","numeric":False,"id_field":False,"is_name":True},
      {"id":"aadhaar","label_en":"Aadhaar Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"mobile","label_en":"Mobile Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"pan","label_en":"PAN Number","numeric":False,"id_field":True,"is_name":False},
      {"id":"business_type","label_en":"Type of Business","numeric":False,"id_field":False,"is_name":False},
      {"id":"loan_amount","label_en":"Loan Amount Required (₹)","numeric":True,"id_field":False,"is_name":False},
      {"id":"account","label_en":"Bank Account Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"ifsc","label_en":"Bank IFSC Code","numeric":False,"id_field":True,"is_name":False},
      {"id":"state","label_en":"State","numeric":False,"id_field":False,"is_name":False},
    ]
  },
  "pmjjby": {
    "id":"pmjjby","icon":"🛡️","color":"#1a237e","color_light":"#283593",
    "name":"PMJJBY","full_name":"Pradhan Mantri Jeevan Jyoti Bima Yojana",
    "ministry":"Ministry of Finance, Govt of India",
    "tagline":"₹2 lakh life insurance cover for just ₹436/year",
    "benefit_pill":"₹2 Lakh life cover @ ₹436/year","category":"Insurance",
    "description":"PMJJBY offers a one-year renewable life insurance cover of ₹2 lakh for death due to any cause. Premium is just ₹436/year auto-debited from bank account. Available to people aged 18-50.",
    "eligibility":"Age 18-50, savings bank account, consent for auto-debit.",
    "documents":["Aadhaar Card","Bank Account with auto-debit facility","Mobile Number","Nominee details"],
    "external_url":"https://financialservices.gov.in",
    "fields":[
      {"id":"name","label_en":"Full Name","numeric":False,"id_field":False,"is_name":True},
      {"id":"aadhaar","label_en":"Aadhaar Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"mobile","label_en":"Mobile Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"age","label_en":"Age (must be 18-50)","numeric":True,"id_field":False,"is_name":False},
      {"id":"account","label_en":"Bank Account Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"ifsc","label_en":"Bank IFSC Code","numeric":False,"id_field":True,"is_name":False},
      {"id":"nominee","label_en":"Nominee Name","numeric":False,"id_field":False,"is_name":False},
      {"id":"state","label_en":"State","numeric":False,"id_field":False,"is_name":False},
    ]
  },
  "pmsby": {
    "id":"pmsby","icon":"⚕️","color":"#33691e","color_light":"#558b2f",
    "name":"PMSBY","full_name":"Pradhan Mantri Suraksha Bima Yojana",
    "ministry":"Ministry of Finance, Govt of India",
    "tagline":"₹2 lakh accident insurance cover for just ₹20/year",
    "benefit_pill":"₹2L accidental cover @ ₹20/year","category":"Insurance",
    "description":"PMSBY offers accident insurance with ₹2 lakh cover for accidental death or permanent total disability and ₹1 lakh for permanent partial disability. Premium: ₹20/year.",
    "eligibility":"Age 18-70, savings bank account, consent for auto-debit.",
    "documents":["Aadhaar Card","Bank Account","Mobile Number","Nominee details"],
    "external_url":"https://financialservices.gov.in",
    "fields":[
      {"id":"name","label_en":"Full Name","numeric":False,"id_field":False,"is_name":True},
      {"id":"aadhaar","label_en":"Aadhaar Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"mobile","label_en":"Mobile Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"age","label_en":"Age (must be 18-70)","numeric":True,"id_field":False,"is_name":False},
      {"id":"account","label_en":"Bank Account Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"ifsc","label_en":"Bank IFSC Code","numeric":False,"id_field":True,"is_name":False},
      {"id":"nominee","label_en":"Nominee Name","numeric":False,"id_field":False,"is_name":False},
      {"id":"state","label_en":"State","numeric":False,"id_field":False,"is_name":False},
    ]
  },
  "atal_pension": {
    "id":"atal_pension","icon":"👴","color":"#4e342e","color_light":"#6d4c41",
    "name":"Atal Pension Yojana","full_name":"Atal Pension Yojana (APY)",
    "ministry":"Ministry of Finance, Govt of India",
    "tagline":"Guaranteed pension of ₹1,000 to ₹5,000/month after age 60",
    "benefit_pill":"₹1,000–₹5,000/month pension guaranteed","category":"Pension",
    "description":"APY guarantees a fixed monthly pension between ₹1,000 and ₹5,000 after age 60 to subscribers. The contribution amount depends on the pension chosen and the age of entry. Government co-contributes 50% or ₹1,000/year (whichever is lower) for eligible subscribers.",
    "eligibility":"Indian citizen aged 18-40, savings bank account, not an income-tax payer.",
    "documents":["Aadhaar Card","Savings Bank Account","Mobile Number"],
    "external_url":"https://npscra.nsdl.co.in",
    "fields":[
      {"id":"name","label_en":"Full Name","numeric":False,"id_field":False,"is_name":True},
      {"id":"aadhaar","label_en":"Aadhaar Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"mobile","label_en":"Mobile Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"age","label_en":"Age (18-40)","numeric":True,"id_field":False,"is_name":False},
      {"id":"account","label_en":"Bank Account Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"ifsc","label_en":"Bank IFSC Code","numeric":False,"id_field":True,"is_name":False},
      {"id":"pension_amount","label_en":"Desired Monthly Pension (1000/2000/3000/4000/5000)","numeric":True,"id_field":False,"is_name":False},
      {"id":"state","label_en":"State","numeric":False,"id_field":False,"is_name":False},
    ]
  },
  "pmay": {
    "id":"pmay","icon":"🏠","color":"#880e4f","color_light":"#ad1457",
    "name":"PM Awas Yojana","full_name":"Pradhan Mantri Awas Yojana (PMAY)",
    "ministry":"Ministry of Housing & Urban Affairs, Govt of India",
    "tagline":"Subsidized housing for all — pucca house for every family",
    "benefit_pill":"Up to ₹2.67 lakh interest subsidy","category":"Housing & Welfare",
    "description":"PMAY provides financial assistance to construct/acquire a pucca house. Rural (Gramin) component offers ₹1.20 lakh in plains and ₹1.30 lakh in hilly/NE regions. Urban component offers interest subsidy of up to 6.5% on home loans.",
    "eligibility":"BPL families, EWS/LIG/MIG income groups without pucca house. No existing central assistance taken before.",
    "documents":["Aadhaar Card","Income Certificate","BPL Card if applicable","Bank Passbook","Land documents"],
    "external_url":"https://pmaymis.gov.in",
    "fields":[
      {"id":"name","label_en":"Applicant Full Name","numeric":False,"id_field":False,"is_name":True},
      {"id":"aadhaar","label_en":"Aadhaar Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"mobile","label_en":"Mobile Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"income","label_en":"Annual Family Income (₹)","numeric":True,"id_field":False,"is_name":False},
      {"id":"account","label_en":"Bank Account Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"ifsc","label_en":"Bank IFSC Code","numeric":False,"id_field":True,"is_name":False},
      {"id":"district","label_en":"District","numeric":False,"id_field":False,"is_name":False},
      {"id":"state","label_en":"State","numeric":False,"id_field":False,"is_name":False},
    ]
  },
  "ujjwala": {
    "id":"ujjwala","icon":"🔥","color":"#e65100","color_light":"#f57c00",
    "name":"PM Ujjwala Yojana","full_name":"Pradhan Mantri Ujjwala Yojana (PMUY)",
    "ministry":"Ministry of Petroleum & Natural Gas, Govt of India",
    "tagline":"Free LPG connection for BPL women — clean cooking fuel for rural households",
    "benefit_pill":"Free LPG connection + first refill","category":"Housing & Welfare",
    "description":"PMUY provides free LPG (cooking gas) connections to women of BPL/SC/ST/PM Awas Yojana households. Benefits include free connection, first refill, and stove for eligible beneficiaries.",
    "eligibility":"Adult women from BPL household, SC/ST/OBC household, PM Awas Yojana beneficiary, or having annual income below ₹1 lakh. No existing LPG connection.",
    "documents":["Aadhaar Card","BPL/Ration Card","Bank Passbook","Passport photo","Address Proof"],
    "external_url":"https://pmuy.gov.in",
    "fields":[
      {"id":"name","label_en":"Applicant Woman's Full Name","numeric":False,"id_field":False,"is_name":True},
      {"id":"aadhaar","label_en":"Aadhaar Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"mobile","label_en":"Mobile Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"account","label_en":"Bank Account Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"ifsc","label_en":"Bank IFSC Code","numeric":False,"id_field":True,"is_name":False},
      {"id":"village","label_en":"Village / Ward Name","numeric":False,"id_field":False,"is_name":False},
      {"id":"district","label_en":"District","numeric":False,"id_field":False,"is_name":False},
      {"id":"state","label_en":"State","numeric":False,"id_field":False,"is_name":False},
    ]
  },
  "mgnrega": {
    "id":"mgnrega","icon":"⛏️","color":"#37474f","color_light":"#455a64",
    "name":"MGNREGA","full_name":"Mahatma Gandhi National Rural Employment Guarantee Act",
    "ministry":"Ministry of Rural Development, Govt of India",
    "tagline":"100 days guaranteed wage employment per year for rural households",
    "benefit_pill":"100 days employment @ minimum wages","category":"Employment & Welfare",
    "description":"MGNREGA guarantees at least 100 days of wage employment in a financial year to every rural household whose adult members volunteer to do unskilled manual work. Wages are paid within 15 days.",
    "eligibility":"Adult members of rural households willing to do unskilled manual work.",
    "documents":["Aadhaar Card","Job Card (if existing)","Bank Passbook","Address Proof","Photograph"],
    "external_url":"https://nrega.nic.in",
    "fields":[
      {"id":"name","label_en":"Applicant Full Name","numeric":False,"id_field":False,"is_name":True},
      {"id":"aadhaar","label_en":"Aadhaar Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"mobile","label_en":"Mobile Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"age","label_en":"Age","numeric":True,"id_field":False,"is_name":False},
      {"id":"account","label_en":"Bank Account Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"ifsc","label_en":"Bank IFSC Code","numeric":False,"id_field":True,"is_name":False},
      {"id":"village","label_en":"Village Name","numeric":False,"id_field":False,"is_name":False},
      {"id":"district","label_en":"District","numeric":False,"id_field":False,"is_name":False},
      {"id":"state","label_en":"State","numeric":False,"id_field":False,"is_name":False},
    ]
  },
  "pm_svanidhi": {
    "id":"pm_svanidhi","icon":"🛒","color":"#006064","color_light":"#00838f",
    "name":"PM SVANidhi","full_name":"PM Street Vendor AtmaNirbhar Nidhi",
    "ministry":"Ministry of Housing & Urban Affairs, Govt of India",
    "tagline":"Collateral-free working capital loans for street vendors",
    "benefit_pill":"₹10K→₹20K→₹50K collateral-free loans","category":"Employment & Welfare",
    "description":"PM SVANidhi provides affordable collateral-free working capital loans starting at ₹10,000 (extendable to ₹20K and ₹50K on repayment) to street vendors to resume their livelihoods. Includes digital transaction incentives.",
    "eligibility":"Street vendors operating before March 24, 2020, with valid Certificate of Vending or letter from ULB.",
    "documents":["Aadhaar Card","Certificate of Vending / ULB Letter","Bank Passbook","Mobile Number"],
    "external_url":"https://pmsvanidhi.mohua.gov.in",
    "fields":[
      {"id":"name","label_en":"Vendor Full Name","numeric":False,"id_field":False,"is_name":True},
      {"id":"aadhaar","label_en":"Aadhaar Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"mobile","label_en":"Mobile Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"account","label_en":"Bank Account Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"ifsc","label_en":"Bank IFSC Code","numeric":False,"id_field":True,"is_name":False},
      {"id":"trade_type","label_en":"Type of Vending Trade","numeric":False,"id_field":False,"is_name":False},
      {"id":"district","label_en":"District","numeric":False,"id_field":False,"is_name":False},
      {"id":"state","label_en":"State","numeric":False,"id_field":False,"is_name":False},
    ]
  },
  "pm_vishwakarma": {
    "id":"pm_vishwakarma","icon":"🔨","color":"#4e342e","color_light":"#6d4c41",
    "name":"PM Vishwakarma","full_name":"PM Vishwakarma Yojana",
    "ministry":"Ministry of MSME, Govt of India",
    "tagline":"Skill training, toolkit & credit support for traditional artisans and craftspeople",
    "benefit_pill":"₹15K toolkit + ₹3L loan + skill training","category":"Employment & Skills",
    "description":"PM Vishwakarma provides holistic support to artisans: recognition via PM Vishwakarma certificate, skill training with stipend, ₹15,000 toolkit incentive, and collateral-free credit up to ₹3 lakh at 5% interest.",
    "eligibility":"Artisans/craftspeople working in one of 18 traditional trades: carpenter, blacksmith, goldsmith, potter, cobbler, etc.",
    "documents":["Aadhaar Card","Mobile (linked to Aadhaar)","Bank Passbook","Trade certificate","Ration Card"],
    "external_url":"https://pmvishwakarma.gov.in",
    "fields":[
      {"id":"name","label_en":"Artisan Full Name","numeric":False,"id_field":False,"is_name":True},
      {"id":"aadhaar","label_en":"Aadhaar Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"mobile","label_en":"Mobile Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"trade_type","label_en":"Trade / Craft (e.g. Carpenter, Blacksmith)","numeric":False,"id_field":False,"is_name":False},
      {"id":"account","label_en":"Bank Account Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"ifsc","label_en":"Bank IFSC Code","numeric":False,"id_field":True,"is_name":False},
      {"id":"district","label_en":"District","numeric":False,"id_field":False,"is_name":False},
      {"id":"state","label_en":"State","numeric":False,"id_field":False,"is_name":False},
    ]
  },
  "pm_surya_ghar": {
    "id":"pm_surya_ghar","icon":"☀️","color":"#f57f17","color_light":"#f9a825",
    "name":"PM Surya Ghar","full_name":"PM Surya Ghar: Muft Bijli Yojana",
    "ministry":"Ministry of New & Renewable Energy, Govt of India",
    "tagline":"Up to 300 units free electricity/month with rooftop solar subsidy up to ₹78,000",
    "benefit_pill":"300 units free electricity + ₹78K subsidy","category":"Energy",
    "description":"Launched in 2024, PM Surya Ghar provides subsidies for rooftop solar installation: ₹30,000 for 1 kW, ₹60,000 for 2 kW, ₹78,000 for 3+ kW systems. Households can earn from surplus generation.",
    "eligibility":"Residential electricity consumers with own house/roof. Priority to households with <150 units/month consumption.",
    "documents":["Aadhaar Card","Electricity Bill (consumer number)","Bank Passbook","Mobile Number","Property ownership proof"],
    "external_url":"https://pmsuryaghar.gov.in",
    "fields":[
      {"id":"name","label_en":"Applicant Full Name","numeric":False,"id_field":False,"is_name":True},
      {"id":"aadhaar","label_en":"Aadhaar Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"mobile","label_en":"Mobile Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"consumer_id","label_en":"Electricity Consumer ID","numeric":False,"id_field":True,"is_name":False},
      {"id":"account","label_en":"Bank Account Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"ifsc","label_en":"Bank IFSC Code","numeric":False,"id_field":True,"is_name":False},
      {"id":"district","label_en":"District","numeric":False,"id_field":False,"is_name":False},
      {"id":"state","label_en":"State","numeric":False,"id_field":False,"is_name":False},
    ]
  },
  "pmgsy": {
    "id":"pmgsy","icon":"🛣️","color":"#263238","color_light":"#37474f",
    "name":"PMGSY","full_name":"Pradhan Mantri Gram Sadak Yojana",
    "ministry":"Ministry of Rural Development, Govt of India",
    "tagline":"All-weather road connectivity to unconnected rural habitations across India",
    "benefit_pill":"All-weather road for unconnected villages","category":"Infrastructure",
    "description":"PMGSY aims to provide all-weather road connectivity to eligible unconnected habitations with population 500+ (250+ in special areas). Also covers upgradation of existing rural roads.",
    "eligibility":"Gram Panchayat applications for unconnected habitations.",
    "documents":["Village/Gram Panchayat resolution","Census population data","Existing road details","GPS coordinates"],
    "external_url":"https://pmgsy.nic.in",
    "fields":[
      {"id":"name","label_en":"Gram Pradhan / Applicant Name","numeric":False,"id_field":False,"is_name":True},
      {"id":"mobile","label_en":"Mobile Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"village","label_en":"Village / Habitation Name","numeric":False,"id_field":False,"is_name":False},
      {"id":"population","label_en":"Habitation Population","numeric":True,"id_field":False,"is_name":False},
      {"id":"district","label_en":"District","numeric":False,"id_field":False,"is_name":False},
      {"id":"state","label_en":"State","numeric":False,"id_field":False,"is_name":False},
    ]
  },
  "lakhpati_didi": {
    "id":"lakhpati_didi","icon":"👩‍🌾","color":"#880e4f","color_light":"#c2185b",
    "name":"Lakhpati Didi","full_name":"Lakhpati Didi Scheme",
    "ministry":"Ministry of Rural Development, Govt of India",
    "tagline":"Skill training & credit for rural SHG women to earn ₹1 lakh+ per year",
    "benefit_pill":"₹1 Lakh+/year income for rural women","category":"Women Empowerment",
    "description":"Lakhpati Didi trains women in SHGs in income-generating skills (solar panels, LED bulbs, drones, plumbing), provides credit access and market linkages to achieve sustainable annual household income of ₹1 lakh+.",
    "eligibility":"Rural women who are members of Self-Help Groups (SHGs).",
    "documents":["Aadhaar Card","SHG membership certificate","Bank Passbook","Mobile Number"],
    "external_url":"https://ruraldevelopment.gov.in",
    "fields":[
      {"id":"name","label_en":"Applicant Full Name","numeric":False,"id_field":False,"is_name":True},
      {"id":"aadhaar","label_en":"Aadhaar Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"mobile","label_en":"Mobile Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"shg_name","label_en":"SHG (Self-Help Group) Name","numeric":False,"id_field":False,"is_name":False},
      {"id":"account","label_en":"Bank Account Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"ifsc","label_en":"Bank IFSC Code","numeric":False,"id_field":True,"is_name":False},
      {"id":"district","label_en":"District","numeric":False,"id_field":False,"is_name":False},
      {"id":"state","label_en":"State","numeric":False,"id_field":False,"is_name":False},
    ]
  },
  "sukanya_samriddhi": {
    "id":"sukanya_samriddhi","icon":"👧","color":"#e91e63","color_light":"#f06292",
    "name":"Sukanya Samriddhi Yojana","full_name":"Sukanya Samriddhi Yojana (SSY)",
    "ministry":"Ministry of Finance, Govt of India",
    "tagline":"High-interest tax-free savings scheme for the girl child's future",
    "benefit_pill":"8.2% interest + tax exemption for girl child","category":"Women & Child",
    "description":"Under Beti Bachao Beti Padhao, SSY offers one of the highest interest rates (8.2% p.a.) on small savings. Tax-exempt deposits from ₹250 to ₹1.5 lakh/year. Account matures at girl's age 21.",
    "eligibility":"Girl child under 10 years. One account per girl, max 2 girls per family.",
    "documents":["Girl child's birth certificate","Guardian's Aadhaar","Guardian's PAN","Initial deposit (min ₹250)"],
    "external_url":"https://www.indiapost.gov.in",
    "fields":[
      {"id":"name","label_en":"Guardian / Parent Full Name","numeric":False,"id_field":False,"is_name":True},
      {"id":"aadhaar","label_en":"Guardian Aadhaar Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"mobile","label_en":"Mobile Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"girl_name","label_en":"Girl Child's Full Name","numeric":False,"id_field":False,"is_name":False},
      {"id":"account","label_en":"Bank Account Number for SSY","numeric":True,"id_field":False,"is_name":False},
      {"id":"ifsc","label_en":"Bank IFSC Code","numeric":False,"id_field":True,"is_name":False},
      {"id":"district","label_en":"District","numeric":False,"id_field":False,"is_name":False},
      {"id":"state","label_en":"State","numeric":False,"id_field":False,"is_name":False},
    ]
  },
  "poshan_abhiyaan": {
    "id":"poshan_abhiyaan","icon":"🥗","color":"#2e7d32","color_light":"#388e3c",
    "name":"Poshan Abhiyaan","full_name":"Poshan Abhiyaan (National Nutrition Mission)",
    "ministry":"Ministry of Women & Child Development, Govt of India",
    "tagline":"Improving nutrition for children, pregnant women, and lactating mothers",
    "benefit_pill":"Nutrition support + health monitoring","category":"Women & Child",
    "description":"Poshan Abhiyaan aims to reduce stunting, under-nutrition, anemia, and low birth weight. Benefits include supplementary nutrition, iron-folic acid supplementation, health check-ups via Anganwadi centers.",
    "eligibility":"Children 0-6 years, pregnant women, lactating mothers, adolescent girls.",
    "documents":["Aadhaar Card","Child's birth certificate (for children)","Mother's health card","Mobile Number"],
    "external_url":"https://poshanabhiyaan.gov.in",
    "fields":[
      {"id":"name","label_en":"Mother / Guardian Full Name","numeric":False,"id_field":False,"is_name":True},
      {"id":"aadhaar","label_en":"Aadhaar Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"mobile","label_en":"Mobile Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"age","label_en":"Age","numeric":True,"id_field":False,"is_name":False},
      {"id":"village","label_en":"Village / Ward","numeric":False,"id_field":False,"is_name":False},
      {"id":"district","label_en":"District","numeric":False,"id_field":False,"is_name":False},
      {"id":"state","label_en":"State","numeric":False,"id_field":False,"is_name":False},
    ]
  },
  "pmkvy": {
    "id":"pmkvy","icon":"🎓","color":"#0d47a1","color_light":"#1565c0",
    "name":"PMKVY","full_name":"Pradhan Mantri Kaushal Vikas Yojana",
    "ministry":"Ministry of Skill Development & Entrepreneurship, Govt of India",
    "tagline":"Free industry-relevant skill training and certification for Indian youth",
    "benefit_pill":"Free skill training + ₹8,000 reward","category":"Skills & Employment",
    "description":"PMKVY provides free short-term skill training in over 300 job roles across 38 sectors. Successful trainees get government-recognized certification, placement assistance, and ₹8,000 reward (under RPL).",
    "eligibility":"Indian youth seeking employment, school/college dropouts. Age 15-45.",
    "documents":["Aadhaar Card","Educational certificate","Mobile Number","Passport photo"],
    "external_url":"https://pmkvyofficial.org",
    "fields":[
      {"id":"name","label_en":"Applicant Full Name","numeric":False,"id_field":False,"is_name":True},
      {"id":"aadhaar","label_en":"Aadhaar Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"mobile","label_en":"Mobile Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"age","label_en":"Age","numeric":True,"id_field":False,"is_name":False},
      {"id":"education","label_en":"Highest Education Qualification","numeric":False,"id_field":False,"is_name":False},
      {"id":"trade_type","label_en":"Preferred Skill / Trade","numeric":False,"id_field":False,"is_name":False},
      {"id":"district","label_en":"District","numeric":False,"id_field":False,"is_name":False},
      {"id":"state","label_en":"State","numeric":False,"id_field":False,"is_name":False},
    ]
  },
  "standup_india": {
    "id":"standup_india","icon":"🚀","color":"#1a237e","color_light":"#283593",
    "name":"Stand-Up India","full_name":"Stand-Up India Scheme",
    "ministry":"Ministry of Finance, Govt of India",
    "tagline":"Bank loans ₹10 lakh to ₹1 crore for SC/ST/women entrepreneurs",
    "benefit_pill":"₹10L–₹1Cr greenfield enterprise loans","category":"Entrepreneurship",
    "description":"Stand-Up India facilitates bank loans between ₹10 lakh and ₹1 crore to at least one SC/ST borrower and one woman borrower per bank branch for setting up greenfield enterprises in manufacturing, services, or trading.",
    "eligibility":"SC/ST or woman borrower, 18+ years, no existing default with any bank/NBFC.",
    "documents":["Aadhaar Card","PAN Card","Business Plan","Bank Statement","Category Certificate (SC/ST)"],
    "external_url":"https://www.standupmitra.in",
    "fields":[
      {"id":"name","label_en":"Applicant Full Name","numeric":False,"id_field":False,"is_name":True},
      {"id":"aadhaar","label_en":"Aadhaar Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"mobile","label_en":"Mobile Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"pan","label_en":"PAN Number","numeric":False,"id_field":True,"is_name":False},
      {"id":"category","label_en":"Category (SC / ST / Woman)","numeric":False,"id_field":False,"is_name":False},
      {"id":"business_type","label_en":"Proposed Business Type","numeric":False,"id_field":False,"is_name":False},
      {"id":"loan_amount","label_en":"Loan Amount Required (₹)","numeric":True,"id_field":False,"is_name":False},
      {"id":"account","label_en":"Bank Account Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"ifsc","label_en":"Bank IFSC Code","numeric":False,"id_field":True,"is_name":False},
      {"id":"state","label_en":"State","numeric":False,"id_field":False,"is_name":False},
    ]
  },
  "pm_sym": {
    "id":"pm_sym","icon":"👷","color":"#37474f","color_light":"#546e7a",
    "name":"PM-SYM","full_name":"Pradhan Mantri Shram Yogi Maan-dhan",
    "ministry":"Ministry of Labour & Employment, Govt of India",
    "tagline":"₹3,000/month guaranteed pension for unorganized sector workers after age 60",
    "benefit_pill":"₹3,000/month pension for workers","category":"Pension",
    "description":"PM-SYM is a voluntary pension scheme for unorganized sector workers earning less than ₹15,000/month. Government matches the subscriber's monthly contribution. ₹3,000/month pension guaranteed after 60.",
    "eligibility":"Unorganized workers, age 18-40, monthly income ≤₹15,000, not EPFO/ESIC/NPS member.",
    "documents":["Aadhaar Card","Savings Bank Account with IFSC","Mobile Number (linked to Aadhaar)"],
    "external_url":"https://maandhan.in",
    "fields":[
      {"id":"name","label_en":"Worker Full Name","numeric":False,"id_field":False,"is_name":True},
      {"id":"aadhaar","label_en":"Aadhaar Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"mobile","label_en":"Mobile Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"age","label_en":"Age (18-40)","numeric":True,"id_field":False,"is_name":False},
      {"id":"account","label_en":"Savings Bank Account Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"ifsc","label_en":"Bank IFSC Code","numeric":False,"id_field":True,"is_name":False},
      {"id":"income","label_en":"Monthly Income (must be ≤₹15,000)","numeric":True,"id_field":False,"is_name":False},
      {"id":"occupation","label_en":"Occupation / Work Type","numeric":False,"id_field":False,"is_name":False},
      {"id":"state","label_en":"State","numeric":False,"id_field":False,"is_name":False},
    ]
  },
  "pmbjp": {
    "id":"pmbjp","icon":"💊","color":"#1b5e20","color_light":"#2e7d32",
    "name":"PM Janaushadhi","full_name":"Pradhan Mantri Bhartiya Janaushadhi Pariyojana",
    "ministry":"Ministry of Chemicals & Fertilizers, Govt of India",
    "tagline":"Quality generic medicines at 50-90% lower prices at Janaushadhi Kendras",
    "benefit_pill":"Medicines 50-90% cheaper than branded","category":"Healthcare",
    "description":"PMBJP provides quality generic medicines at highly affordable prices (50-90% cheaper than branded) through dedicated Janaushadhi Kendras. Over 2,000+ drugs and 300 surgical devices available.",
    "eligibility":"Open to all citizens. Application is for registering a Janaushadhi Kendra outlet.",
    "documents":["Aadhaar Card","Pharmacy license / Educational certificate","Property/rental agreement","Bank Passbook","Mobile Number"],
    "external_url":"https://janaushadhi.gov.in",
    "fields":[
      {"id":"name","label_en":"Applicant Full Name","numeric":False,"id_field":False,"is_name":True},
      {"id":"aadhaar","label_en":"Aadhaar Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"mobile","label_en":"Mobile Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"account","label_en":"Bank Account Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"ifsc","label_en":"Bank IFSC Code","numeric":False,"id_field":True,"is_name":False},
      {"id":"district","label_en":"District","numeric":False,"id_field":False,"is_name":False},
      {"id":"state","label_en":"State","numeric":False,"id_field":False,"is_name":False},
    ]
  },
  "pmmsy": {
    "id":"pmmsy","icon":"🐟","color":"#006064","color_light":"#00838f",
    "name":"PMMSY","full_name":"Pradhan Mantri Matsya Sampada Yojana",
    "ministry":"Ministry of Fisheries, Animal Husbandry & Dairying, Govt of India",
    "tagline":"Blue Revolution — modernizing fisheries for doubling fisher incomes",
    "benefit_pill":"Subsidized fishing infrastructure & loans","category":"Agriculture & Fisheries",
    "description":"PMMSY aims to double fishers' income by modernizing fisheries. Benefits include subsidized fishing boats, nets, aquaculture infrastructure, cold chain, and processing units. SC/ST/women get higher subsidy of 60%.",
    "eligibility":"Fishers, fish farmers, fish workers, SHGs, FPOs involved in fisheries sector.",
    "documents":["Aadhaar Card","Fishing license / Registration","Bank Passbook","Mobile Number","Caste certificate if applicable"],
    "external_url":"https://pmmsy.dof.gov.in",
    "fields":[
      {"id":"name","label_en":"Fisher / Applicant Full Name","numeric":False,"id_field":False,"is_name":True},
      {"id":"aadhaar","label_en":"Aadhaar Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"mobile","label_en":"Mobile Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"account","label_en":"Bank Account Number","numeric":True,"id_field":False,"is_name":False},
      {"id":"ifsc","label_en":"Bank IFSC Code","numeric":False,"id_field":True,"is_name":False},
      {"id":"fishing_type","label_en":"Type of Fishing Activity","numeric":False,"id_field":False,"is_name":False},
      {"id":"district","label_en":"District","numeric":False,"id_field":False,"is_name":False},
      {"id":"state","label_en":"State","numeric":False,"id_field":False,"is_name":False},
    ]
  },
}

def _parse_gemini_json(response_text: str) -> dict:
    text = response_text.strip()
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())

def gemini_conversation(scheme_name, conv_history, fields_collected, fields_remaining,
                        user_input, lang="en-IN",
                        user_id=None, session_id=None):
    """Run one turn of the scheme-application conversation.

    New in v8.1: If user_id and session_id are provided:
      - Load agent memory (persistent user profile) to skip re-asking known facts
      - Persist both the user turn and the assistant reply to MongoDB
      - Extract and upsert any newly learned facts into agent_memory
    """
    if not USE_GEMINI:
        return {"next_question": None, "extracted_fields": {}, "error": "Gemini not configured"}
    try:
        lang_name = {"te-IN":"Telugu","hi-IN":"Hindi","en-IN":"English",
                     "ta-IN":"Tamil","kn-IN":"Kannada","ml-IN":"Malayalam",
                     "bn-IN":"Bengali","mr-IN":"Marathi","gu-IN":"Gujarati",
                     "pa-IN":"Punjabi","ur-IN":"Urdu","or-IN":"Odia",
                     "as-IN":"Assamese","ne-IN":"Nepali"}.get(lang,"English")

        # ── Load persistent agent memory ──────────────────────────────────
        memory_ctx = ""
        if user_id:
            try:
                mem = db_get_agent_memory(str(user_id))
                known = mem.get("memory", {})
                if known:
                    mem_lines = ", ".join(f"{k}={v}" for k, v in known.items())
                    memory_ctx = f"[Agent memory from previous sessions: {mem_lines}]"
            except Exception:
                pass

        history_str = "\n".join([f"{h['role']}: {h['text']}" for h in conv_history[-6:]])
        prompt = f"""You are Sarathi, an AI assistant helping Indian citizens apply for government schemes.
Current scheme: {scheme_name}
Language to use: {lang_name} (BCP-47: {lang})
{memory_ctx}
Fields already collected: {json.dumps(fields_collected, ensure_ascii=False)}
Fields still needed: {', '.join(fields_remaining)}
Recent conversation:\n{history_str}
User's latest input: "{user_input}"

Tasks:
1. Extract any field values from user input (fix speech errors, normalize digits)
2. Validate extracted values (Aadhaar=12 digits, mobile=10 digits)
3. If a field value is already known from Agent memory, skip asking it again — just include it in extracted_fields
4. Generate next question in {lang_name} for next missing field
5. If all fields collected, set next_question to null

Respond ONLY with valid JSON, no markdown:
{{"extracted_fields":{{"field_id":"value"}},"next_question":"question or null","validation_errors":{{"field_id":"error"}}}}"""
        response = genai_client.models.generate_content(
            model="gemini-3.5-flash",
            contents=prompt
        )
        result = _parse_gemini_json(response.text)

        # ── Persist conversation turns and update memory ───────────────────
        if user_id and session_id:
            try:
                db_save_conversation(user_id, session_id, "user", user_input,
                                     lang=lang, scheme_name=scheme_name)
                reply_text = result.get("next_question") or "Thank you."
                db_save_conversation(user_id, session_id, "assistant", reply_text,
                                     lang=lang, scheme_name=scheme_name)
                # Upsert newly extracted facts into agent memory
                new_facts = result.get("extracted_fields", {})
                if new_facts:
                    db_update_agent_memory(str(user_id), new_facts)
            except Exception as mem_err:
                print(f"Memory persist warning: {mem_err}")

        return result
    except Exception as e:
        print(f"Gemini conversation error: {e}")
        return {"next_question": None, "extracted_fields": {}, "error": str(e)}

def gemini_eligibility_check(scheme_name, answers):
    if not USE_GEMINI:
        return {"eligible": True, "reason": "AI eligibility check not available", "also_qualifies": []}
    try:
        prompt = f"""{SCHEMES_CONTEXT}
User wants to apply for: {scheme_name}
User's answers: {json.dumps(answers, ensure_ascii=False, indent=2)}
Check eligibility. Respond ONLY with valid JSON:
{{"eligible":true/false,"reason":"1-2 sentence explanation","also_qualifies":["scheme1"],"does_not_qualify":["scheme2"]}}"""
        response = genai_client.models.generate_content(
            model="gemini-3.5-flash",
            contents=prompt
        )
        return _parse_gemini_json(response.text)
    except Exception as e:
        print(f"Gemini eligibility error: {e}")
        return {"eligible": True, "reason": "Could not verify eligibility automatically.", "also_qualifies": []}

def gemini_scheme_recommend(situation, lang="en-IN", user_id=None):
    """Recommend schemes using hybrid Atlas + Vector Search, enriched with agent memory."""
    if not USE_GEMINI:
        return {"schemes": [], "message": "AI recommendations not available"}
    try:
        lang_name = {"te-IN":"Telugu","hi-IN":"Hindi","en-IN":"English"}.get(lang,"English")

        # ── Load agent memory for personalised context ─────────────────────
        memory_ctx = ""
        if user_id:
            try:
                mem = db_get_agent_memory(str(user_id))
                known = mem.get("memory", {})
                if known:
                    mem_lines = ", ".join(f"{k}: {v}" for k, v in known.items())
                    memory_ctx = f"\n[Remembered context: {mem_lines}]"
            except Exception:
                pass

        # ── Hybrid search: Atlas text + vector search ──────────────────────
        hybrid_matches: list = []
        try:
            # Get Gemini embedding of the situation text (Vertex AI / API key)
            _emb_resp = genai_client.models.embed_content(
                model="text-embedding-005",
                contents=situation + memory_ctx,
            ) if genai_client else None
            query_embedding = (
                list(_emb_resp.embeddings[0].values)
                if _emb_resp and _emb_resp.embeddings else []
            )
            hybrid_matches = db_hybrid_search_schemes(
                situation, query_embedding, limit=10
            )
        except Exception as hs_err:
            print(f"Hybrid search warning: {hs_err}")

        hybrid_json = json.dumps(
            [{"name": s.get("scheme_name",""), "source": s.get("source",""),
              "eligibility": s.get("eligibility","")[:200],
              "benefits": s.get("benefits","")[:200]} for s in hybrid_matches],
            ensure_ascii=False
        )

        prompt = f"""{SCHEMES_CONTEXT}
Citizen situation: "{situation}"{memory_ctx}

Top schemes found via Hybrid Atlas + Vector Search:
{hybrid_json}

Recommend the most suitable schemes for this citizen. Respond in {lang_name}. Respond ONLY with valid JSON:
{{"schemes":[{{"name":"scheme","url":"/service/file.html","reason":"why","benefit":"what they get"}}],"message":"encouraging message"}}"""
        response = genai_client.models.generate_content(
            model="gemini-3.5-flash",
            contents=prompt
        )
        return _parse_gemini_json(response.text)
    except Exception as e:
        print(f"Gemini recommend error: {e}")
        return {"schemes": [], "message": "Could not generate recommendations. Please try again."}

def gemini_extract_entity(field_type, transcript):
    if not USE_GEMINI: return transcript
    try:
        prompt = f"""Extract the {field_type} from this spoken text: "{transcript}"
Rules: aadhaar_number=12 digits, mobile_number=10 digits, pincode=6 digits.
Convert spoken number words to digits. Respond ONLY with the extracted value."""
        response = genai_client.models.generate_content(
            model="gemini-3.5-flash",
            contents=prompt
        )
        return response.text.strip()
    except Exception as e:
        print(f"Gemini extract error: {e}"); return transcript

# ════════════════════════════════════════════════════════════════════════
# SECURITY HELPERS
# ════════════════════════════════════════════════════════════════════════
def get_client_ip():
    return request.headers.get("X-Forwarded-For", request.remote_addr)

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect("/login")
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect("/admin/login")
        return f(*args, **kwargs)
    return decorated

# ════════════════════════════════════════════════════════════════════════
# QR CODE
# ════════════════════════════════════════════════════════════════════════
def generate_qr_base64(data: str) -> str | None:
    if not QR_AVAILABLE: return None
    try:
        qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=8, border=2)
        qr.add_data(data)
        qr.make(fit=True)
        img = qr.make_image(fill_color="#1a237e", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode()
    except Exception as e:
        print(f"QR error: {e}"); return None

# ════════════════════════════════════════════════════════════════════════
# WEBSOCKET — GCP Speech-to-Text
# ════════════════════════════════════════════════════════════════════════

@socketio.on("connect")
def on_connect():
    print(f"[WS] Client connected: {request.sid}")
    # Immediately inform client of real STT capability
    emit("stt_capability", {"use_gcp": USE_GOOGLE_STT})

@socketio.on("disconnect")
def on_disconnect():
    print(f"[WS] Client disconnected: {request.sid}")

@socketio.on("start_recognition")
def on_start_recognition(data):
    lang = data.get("lang", "en-IN")
    join_room(request.sid)
    emit("recognition_ready", {"lang": lang, "use_gcp": USE_GOOGLE_STT})

@socketio.on("audio_chunk")
def on_audio_chunk(data):
    if not USE_GOOGLE_STT:
        emit("use_browser_stt", {}); return

    lang = data.get("lang", "en-IN")
    audio_raw = data.get("audio")
    if not audio_raw:
        return

    try:
        # FIX: handle both str (base64) and bytes
        if isinstance(audio_raw, str):
            audio_bytes = base64.b64decode(audio_raw)
        else:
            audio_bytes = bytes(audio_raw)

        client = gcp_speech.SpeechClient()
        config = gcp_speech.RecognitionConfig(
            encoding=gcp_speech.RecognitionConfig.AudioEncoding.WEBM_OPUS,
            sample_rate_hertz=48000,
            language_code=lang,
            alternative_language_codes=["en-IN"],
            model="latest_long",
            use_enhanced=True,
            enable_automatic_punctuation=True,
            max_alternatives=3,
        )
        response = client.recognize(
            config=config,
            audio=gcp_speech.RecognitionAudio(content=audio_bytes)
        )
        transcripts = [
            {"transcript": r.alternatives[0].transcript,
             "confidence": r.alternatives[0].confidence}
            for r in response.results
        ]
        if transcripts:
            emit("transcript_result", {"transcripts": transcripts, "lang": lang, "final": True})
        else:
            emit("no_speech", {"lang": lang})
    except Exception as e:
        print(f"GCP STT error: {e}")
        emit("stt_error", {"error": str(e), "fallback": True})

# ════════════════════════════════════════════════════════════════════════
# REST API ROUTES
# ════════════════════════════════════════════════════════════════════════

@app.route("/api/tts", methods=["POST"])
@login_required
def api_tts():
    body = request.get_json(silent=True) or {}
    text = body.get("text", "").strip()
    lang = body.get("lang", "en-IN")
    if not text:
        return jsonify({"error": "No text provided"}), 400
    audio_bytes = synthesize_speech_gcp(text, lang)
    if audio_bytes:
        return jsonify({"audio_base64": base64.b64encode(audio_bytes).decode(),
                        "format": "mp3", "source": "google_wavenet"})
    return jsonify({"audio_base64": None, "source": "browser_fallback"})

@app.route("/api/translate", methods=["POST"])
@login_required
def api_translate():
    body = request.get_json(silent=True) or {}
    translated = translate_text(body.get("text",""), body.get("target_lang","en-IN"))
    return jsonify({"translated": translated, "target_lang": body.get("target_lang","en-IN")})

@app.route("/api/detect_language", methods=["POST"])
@login_required
def api_detect_language():
    body = request.get_json(silent=True) or {}
    return jsonify({"detected_lang": detect_language(body.get("text",""))})

@app.route("/api/gemini_chat", methods=["POST"])
@login_required
def api_gemini_chat():
    body = request.get_json(silent=True) or {}
    result = gemini_conversation(
        body.get("scheme_name",""), body.get("conversation",[]),
        body.get("fields_collected",{}), body.get("fields_remaining",[]),
        body.get("user_input",""), body.get("lang","en-IN"),
        user_id=session.get("user_id"),
        session_id=body.get("session_id"),
    )
    return jsonify(result)

@app.route("/api/captcha")
def get_captcha():
    """Generate and return a base64 image CAPTCHA, storing answer in session."""
    try:
        from captcha.image import ImageCaptcha
        import random as _rand, string as _str, base64 as _b64
        scope = request.args.get("scope", "default")
        text  = ''.join(_rand.choices(_str.ascii_uppercase + _str.digits, k=6))
        img   = ImageCaptcha(width=200, height=70)
        data  = img.generate(text)
        b64   = _b64.b64encode(data.read()).decode()
        session[f"captcha_{scope}"] = text.upper()
        return jsonify({"image": f"data:image/png;base64,{b64}", "success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/captcha_speak")
def api_captcha_speak():
    """Return the captcha text from session for audio reading.
    Transforms digits to word-form and separates characters for clarity."""
    scope = request.args.get("scope", "default")
    text  = session.get(f"captcha_{scope}", "")
    if not text:
        return jsonify({"phrase": "Captcha not loaded. Please refresh.", "success": False})
    # Build a slow, clear spoken phrase — each character separated by comma pause
    DIGIT_WORDS = {"0":"zero","1":"one","2":"two","3":"three","4":"four",
                   "5":"five","6":"six","7":"seven","8":"eight","9":"nine"}
    parts = []
    for ch in text.upper():
        parts.append(DIGIT_WORDS.get(ch, ch))   # digits as words, letters as-is
    phrase = "The captcha code is: " + ", ".join(parts)
    return jsonify({"phrase": phrase, "length": len(text), "success": True})


@app.route("/api/register/send_otp", methods=["POST"])
def register_send_otp():
    """Send email OTP during registration after captcha verification."""
    import random as _rand, hashlib as _hl, time as _t
    body    = request.get_json(silent=True) or {}
    email   = body.get("email", "").strip().lower()
    captcha = body.get("captcha", "").strip().upper()

    if captcha != session.get("captcha_register", "").upper():
        return jsonify({"success": False, "error": "Incorrect CAPTCHA. Please refresh and try again."})

    if not email or "@" not in email:
        return jsonify({"success": False, "error": "Invalid email address."})

    otp = str(_rand.SystemRandom().randint(100000, 999999))
    session["reg_otp_hash"]     = _hl.sha256(otp.encode()).hexdigest()
    session["reg_otp_expires"]  = _t.time() + 600
    session["reg_otp_email"]    = email
    session["reg_otp_attempts"] = 0

    from sms_service import _gmail_otp
    sent = _gmail_otp(email, otp)
    if not sent:
        return jsonify({"success": True, "demo": True, "demo_otp": otp})
    return jsonify({"success": True, "demo": False})


@app.route("/api/register/verify_otp", methods=["POST"])
def register_verify_otp():
    """Verify registration email OTP."""
    import hashlib as _hl, time as _t
    body     = request.get_json(silent=True) or {}
    otp      = body.get("otp", "").strip()
    stored   = session.get("reg_otp_hash", "")
    expires  = session.get("reg_otp_expires", 0)
    attempts = session.get("reg_otp_attempts", 0)

    if not stored:
        return jsonify({"success": False, "error": "OTP not found. Request a new one."})
    if attempts >= 3:
        return jsonify({"success": False, "error": "Too many attempts. Request a new OTP."})
    if _t.time() > expires:
        return jsonify({"success": False, "error": "OTP expired. Request a new one."})

    session["reg_otp_attempts"] = attempts + 1
    if _hl.sha256(otp.encode()).hexdigest() != stored:
        return jsonify({"success": False, "error": "Incorrect OTP. Please try again."})

    session["reg_email_verified"] = session.get("reg_otp_email", "")
    session.pop("reg_otp_hash", None)
    return jsonify({"success": True})


@app.route("/forgot_password", methods=["GET"])
def forgot_password():
    return render_template("forgot_password.html")


@app.route("/forgot_password/request_otp", methods=["POST"])
def forgot_password_request_otp():
    import random as _rand
    from sms_service import _gmail_otp
    mobile  = request.form.get("mobile", "").strip()
    email   = request.form.get("email", "").strip().lower()
    captcha = request.form.get("captcha", "").strip().upper()
    ip      = get_client_ip()

    if captcha != session.get("captcha_forgot", "").upper():
        flash("Incorrect CAPTCHA. Please try again.")
        return redirect("/forgot_password")

    try:
        user = db_get_user_by_mobile(mobile)
    except DBNotAvailable:
        flash("⚠️ MongoDB connection failed. If URI is set, check Atlas Network Access: allow IP 0.0.0.0/0")
        return redirect("/forgot_password")

    if not user or user.get("email", "").lower() != email:
        flash("If the details are correct, an OTP has been sent to your mobile and email.")
        session["fp_mobile"] = mobile
        return redirect("/forgot_password/verify")

    otp = str(_rand.SystemRandom().randint(100000, 999999))
    try:
        db_create_otp(mobile, otp)
    except DBNotAvailable:
        flash("⚠️ Database not connected. Please set MONGODB_URI in your .env file.")
        return redirect("/forgot_password")

    from sms_service import send_otp_sms as _send_otp
    channel = _send_otp(mobile, otp, email=email)
    if channel == "both":
        flash("Password reset OTP sent to your registered mobile and email.")
    elif channel == "sms":
        flash("Password reset OTP sent to your registered mobile number.")
    elif channel == "email":
        flash("Password reset OTP sent to your registered email address.")
    else:
        flash(f"[DEMO MODE — set FAST2SMS_API_KEY / MAIL_EMAIL in .env] Reset OTP: {otp}")

    session["fp_mobile"] = mobile
    try:
        db_log_audit(user["id"], "FORGOT_PASSWORD_OTP", ip=ip)
    except DBNotAvailable:
        pass
    return redirect("/forgot_password/verify")


@app.route("/forgot_password/verify", methods=["GET", "POST"])
def forgot_password_verify():
    if request.method == "POST":
        mobile = session.get("fp_mobile", "")
        otp    = request.form.get("otp", "").strip()
        ip     = get_client_ip()
        if not mobile:
            flash("Session expired. Please start again.")
            return redirect("/forgot_password")
        success, error_msg = db_verify_otp(mobile, otp)
        if not success:
            flash(error_msg)
            return redirect("/forgot_password/verify")
        session["fp_verified"]        = True
        session["fp_verified_mobile"] = mobile
        db_log_audit(None, "FP_OTP_VERIFIED", details=f"Mobile:{mobile}", ip=ip)
        return redirect("/forgot_password/reset")
    return render_template("forgot_password_verify.html",
                           mobile=session.get("fp_mobile", ""))


@app.route("/forgot_password/reset", methods=["GET", "POST"])
def forgot_password_reset():
    if not session.get("fp_verified"):
        flash("Please verify your OTP first.")
        return redirect("/forgot_password")
    if request.method == "POST":
        password = request.form.get("password", "")
        confirm  = request.form.get("confirm_password", "")
        mobile   = session.get("fp_verified_mobile", "")
        ip       = get_client_ip()
        if password != confirm:
            flash("Passwords do not match.")
            return render_template("forgot_password_reset.html")
        if len(password) < 8:
            flash("Password must be at least 8 characters.")
            return render_template("forgot_password_reset.html")
        new_hash = generate_password_hash(password, method="pbkdf2:sha256:600000")
        db_update_password(mobile, new_hash)
        user = db_get_user_by_mobile(mobile)
        if user:
            db_log_audit(user["id"], "PASSWORD_RESET", ip=ip)
        session.pop("fp_mobile", None)
        session.pop("fp_verified", None)
        session.pop("fp_verified_mobile", None)
        flash("Password updated successfully! Please login with your new password.")
        return redirect("/login")
    return render_template("forgot_password_reset.html")

@app.route("/api/eligibility_check", methods=["POST"])
@login_required
def api_eligibility_check():
    body         = request.get_json(silent=True) or {}
    user_id      = session["user_id"]
    user_profile = db_get_user_by_id(user_id) or {}

    # Merge stored profile with agent memory (persistent facts across sessions)
    try:
        mem = db_get_agent_memory(str(user_id))
        user_profile.update(mem.get("memory", {}))
    except Exception:
        pass

    # Merge with any answers submitted from the form
    user_profile.update(body.get("answers", {}))

    from eligibility_engine import check_eligibility_india
    result = check_eligibility_india(
        user_profile,
        scheme_name=body.get("scheme_name", "")
    )

    # Persist eligibility result for memory retrieval
    try:
        db_save_eligibility_result(user_id, result,
                                    scheme_name=body.get("scheme_name", ""))
    except Exception:
        pass

    db_log_audit(user_id, "ELIGIBILITY_CHECK",
                 details=f"Schemes found: {len(result.get('qualifying_schemes', []))}",
                 ip=get_client_ip())
    return jsonify(result)

@app.route("/api/scheme_recommend", methods=["POST"])
@login_required
def api_scheme_recommend():
    body = request.get_json(silent=True) or {}
    return jsonify(gemini_scheme_recommend(
        body.get("situation",""),
        body.get("lang","en-IN"),
        user_id=session.get("user_id"),
    ))

@app.route("/api/extract_entity", methods=["POST"])
@login_required
def api_extract_entity():
    body = request.get_json(silent=True) or {}
    return jsonify({"value": gemini_extract_entity(body.get("field_type","text"), body.get("transcript",""))})

@app.route("/api/validate_field", methods=["POST"])
@login_required
def api_validate_field():
    body = request.get_json(silent=True) or {}
    valid, msg = validate_field(body.get("field_id",""), body.get("value",""))
    return jsonify({"valid": valid, "message": msg})

@app.route("/api/qr_code", methods=["POST"])
@login_required
def api_qr_code():
    body = request.get_json(silent=True) or {}
    ref_id = body.get("ref_id","")
    if not ref_id:
        return jsonify({"error": "No ref_id"}), 400
    return jsonify({"qr_base64": generate_qr_base64(ref_id)})

@app.route("/api/submit_form", methods=["POST"])
@login_required
def api_submit_form():
    user_id = session["user_id"]
    ip = get_client_ip()
    if not db_check_rate_limit(user_id, "submit", max_count=20, window_seconds=3600):
        db_log_audit(user_id, "RATE_LIMIT", ip=ip)
        return jsonify({"success": False, "error": "Too many submissions. Please wait."}), 429

    body = request.get_json(silent=True)
    if not body:
        return jsonify({"success": False, "error": "Invalid request"}), 400

    service_name = body.get("service","").strip()
    lang         = body.get("lang","en-IN")
    data         = body.get("data",{})

    VALID_SERVICES = set(SCHEME_DATA.keys()) | {
        "ration_card","aarogyasri","rythu_bandhu","crop_insurance",
        "dharani","kalyana_lakshmi","pension","eseva"
    }
    if service_name not in VALID_SERVICES or not data:
        return jsonify({"success": False, "error": "Invalid service or missing data"}), 400

    errors = {}
    for fid, val in data.items():
        ok, msg = validate_field(fid, str(val))
        if not ok and msg:
            errors[fid] = msg
    if errors:
        return jsonify({"success": False, "errors": errors,
                        "error": "Validation failed: " + "; ".join(errors.values())}), 422

    plain_json = json.dumps({"service": service_name,"lang": lang,"fields": data,
                              "submitted_at": datetime.now().isoformat(),"user_id": user_id},
                             ensure_ascii=False)
    encrypted = encrypt_data(plain_json)
    salt      = uuid.uuid4().hex
    integrity = compute_integrity_hash(plain_json, salt)
    ref_id    = f"SRTH-{service_name[:3].upper()}-{uuid.uuid4().hex[:8].upper()}"

    try:
        db_save_submission(ref_id, user_id, service_name, encrypted, f"{salt}:{integrity}", lang, ip)
    except Exception as e:
        print(f"DB Error: {e}")
        return jsonify({"success": False, "error": "Database error. Please try again."}), 500

    db_log_audit(user_id, "FORM_SUBMIT", ref_id=ref_id,
                 details=f"Service:{service_name}|Lang:{lang}", ip=ip)

    # Feature 2: Send confirmation via Fast2SMS + email fallback (non-blocking)
    try:
        from sms_service import send_submission_sms
        mobile = session.get("user_mobile", "")
        email  = session.get("user_email", "")
        if not mobile or not email:
            _u = db_get_user_by_id(user_id)
            if _u:
                mobile = mobile or _u.get("mobile", "")
                email  = email  or _u.get("email",  "")
        if mobile:
            send_submission_sms(mobile, ref_id, service_name, lang, email=email)
    except Exception as _notify_err:
        print(f"Notification send failed (non-critical): {_notify_err}")

    return jsonify({
        "success": True, "ref_id": ref_id, "service": service_name,
        "submitted_at": datetime.now().isoformat(),
        "qr_code": generate_qr_base64(ref_id)
    })

# ─────────────────────────────────────────────────────────────────────────────
# LANGUAGE CONFIRMATION & SELECTION 
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/confirm_language", methods=["POST"])
@login_required
def api_confirm_language():
    """
    Called when user confirms or changes their detected language.
    Persists both the language choice and confirmation flag in the session.
    """
    data = request.get_json(silent=True) or {}
    lang = data.get("lang", session.get("user_lang", "en-IN"))
    # Validate — only accept known BCP-47 codes
    if lang not in LANG_DISPLAY:
        lang = "en-IN"
    session["user_lang"]      = lang
    session["lang_confirmed"] = True
    session.modified          = True
    return jsonify({"success": True, "lang": lang,
                    "display": LANG_DISPLAY.get(lang, "English")})


@app.route("/set_language", methods=["POST"])
@login_required
def set_language():
    """
    Called when a user clicks the manual language toggle switch on a scheme page.
    Updates the session and allows the frontend to reload with the new translation.
    """
    data = request.get_json(silent=True) or {}
    new_lang = data.get("language")
    
    if new_lang:
        # Update the session with the new language chosen by the user
        session['user_lang'] = new_lang
        session.modified = True
        return jsonify({"status": "success", "language": new_lang}), 200
        
    return jsonify({"error": "No language provided"}), 400


@app.route("/api/ocr_upload", methods=["POST"])
@login_required
def api_ocr_upload():
    file = request.files.get("file")
    if not file:
        return jsonify({"error": "No file provided"}), 400

    if not OCR_AVAILABLE:
        # Fallback: return empty fields — client handles gracefully
        return jsonify({"fields": {}, "raw_text": "", "error": "OCR not available on server"})

    try:
        img = PILImage.open(file.stream).convert("RGB")
        # Enhance for OCR
        img = img.resize((img.width * 2, img.height * 2), PILImage.LANCZOS)

        raw_text = pytesseract.image_to_string(img, lang="eng+tel+hin",
                                               config="--oem 3 --psm 3")

        extracted = {}
        for field, patterns in OCR_FIELD_PATTERNS.items():
            for pattern in patterns:
                match = _re.search(pattern, raw_text, _re.IGNORECASE | _re.MULTILINE)
                if match:
                    val = match.group(1) if match.lastindex else match.group(0)
                    val = val.strip().replace("\n", " ")
                    if val:
                        extracted[field] = val
                        break

        # Clean aadhaar
        if "aadhaar" in extracted:
            extracted["aadhaar"] = extracted["aadhaar"].replace(" ", "")

        db_log_audit(session["user_id"], "OCR_UPLOAD",
                     details=f"Fields extracted: {list(extracted.keys())}",
                     ip=get_client_ip())

        return jsonify({"fields": extracted, "raw_text": raw_text[:500]})
    except Exception as e:
        print(f"OCR error: {e}")
        return jsonify({"fields": {}, "error": str(e)})

# ════════════════════════════════════════════════════════════════════════
# PAGE ROUTES
# ════════════════════════════════════════════════════════════════════════

@app.route("/register", methods=["GET","POST"])
def register():
    if request.method == "POST":
        first    = request.form.get("first",    "").strip()
        last     = request.form.get("last",     "").strip()
        email    = request.form.get("email",    "").strip().lower()
        mobile   = request.form.get("mobile",   "").strip()
        pw       = request.form.get("password", "")
        dob      = request.form.get("dob",      "").strip()
        age      = request.form.get("age",      "").strip()
        gender   = request.form.get("gender",   "").strip()
        caste    = request.form.get("caste",     "").strip()
        village  = request.form.get("village",  "").strip()
        mandal   = request.form.get("mandal",   "").strip()
        district = request.form.get("district", "").strip()
        state    = request.form.get("state",    "").strip()
        pincode  = request.form.get("pincode",  "").strip()
        aadhaar  = request.form.get("aadhaar",  "").strip()
        ip       = get_client_ip()

        # Verify email OTP was confirmed in this session
        verified_email = session.get("reg_email_verified", "")
        if not verified_email or verified_email.lower() != email.lower():
            flash("Email not verified. Please complete OTP verification.")
            return redirect("/register")

        if not all([first, last, mobile, pw, dob, village, mandal, district, state, pincode]):
            flash("All required fields must be filled."); return redirect("/register")
        if not re.match(r"^\d{10}$", mobile):
            flash("Mobile must be exactly 10 digits."); return redirect("/register")
        if aadhaar and not re.match(r"^\d{12}$", aadhaar):
            flash("Aadhaar must be exactly 12 digits."); return redirect("/register")
        if not re.match(r"^\d{6}$", pincode):
            flash("Pincode must be exactly 6 digits."); return redirect("/register")
        if len(pw) < 8:
            flash("Password must be at least 8 characters."); return redirect("/register")
        try:
            uid = db_create_user(
                first, last, email, mobile,
                generate_password_hash(pw, method="pbkdf2:sha256:600000"),
                dob=dob, age=age, gender=gender, caste=caste,
                village=village, mandal=mandal,
                district=district, state=state, pincode=pincode,
                aadhaar=aadhaar
            )
            session.pop("reg_email_verified", None)
            # Pre-set language for this user based on their state
            session["user_lang"] = get_user_language(state)
            db_log_audit(uid, "REGISTER",
                         details=f"New citizen: {first} {last} | {district}, {state}",
                         ip=ip)
            flash("Registration successful! Please login.")
            return redirect("/login")
        except Exception as e:
            flash("Mobile or Aadhaar already registered."); return redirect("/register")
    return render_template("register.html")

@app.route("/admin_login")
@app.route("/admin_login/")
def admin_login_redirect():
    """Friendly redirect for users who type /admin_login instead of /admin/login."""
    return redirect("/admin/login")


@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        return _login_request_otp()
    return render_template("login.html")


@app.route("/login/request_otp", methods=["POST"])
def login_request_otp():
    return _login_request_otp()


def _login_request_otp():
    """Verify first+last+mobile+email+password+captcha then send OTP to email only."""
    import random as _random

    first    = request.form.get("first",    "").strip().lower()
    last     = request.form.get("last",     "").strip().lower()
    mobile   = request.form.get("mobile",   "").strip()
    email    = request.form.get("email",    "").strip().lower()
    password = request.form.get("password", "")
    captcha  = request.form.get("captcha",  "").strip().upper()
    ip       = get_client_ip()

    # 1. Verify image captcha
    if captcha != session.get("captcha_login", "").upper():
        flash("Incorrect CAPTCHA. Please refresh and try again.")
        return redirect("/login")
    session.pop("captcha_login", None)

    # 2. Basic mobile format check
    if not re.match(r"^\d{10}$", mobile):
        flash("Enter a valid 10-digit mobile number.")
        return redirect("/login")

    # 3. Rate limit
    if not db_check_login_attempts(mobile, ip):
        flash("Too many login attempts. Please try again in 15 minutes.")
        db_log_audit(None, "LOGIN_RATE_LIMIT", details=f"Mobile:{mobile}", ip=ip)
        return redirect("/login")

    # 4. Find user
    user = db_get_user_by_mobile(mobile)
    if not user:
        flash("Invalid credentials. Please check all fields.")
        return redirect("/login")

    # 5. Verify all credentials together (timing-safe — same branch for all failures)
    name_ok  = (user.get("first_name","").lower() == first and
                user.get("last_name", "").lower() == last)
    email_ok = (user.get("email","").lower() == email)
    pass_ok  = check_password_hash(user.get("password",""), password)

    if not (name_ok and email_ok and pass_ok):
        db_log_audit(None, "LOGIN_FAIL", details=f"Mobile:{mobile}", ip=ip)
        flash("Invalid credentials. Please check all fields.")
        return redirect("/login")

    # 6. All valid → send OTP to registered email ONLY
    otp     = str(_random.SystemRandom().randint(100000, 999999))
    db_create_otp(mobile, otp)

    from sms_service import send_otp_sms
    channel = send_otp_sms(mobile, otp, email=user.get("email", ""))

    if channel == "both":
        flash("OTP sent to your registered mobile number and email address.")
    elif channel == "sms":
        flash("OTP sent to your registered mobile number via SMS.")
    elif channel == "email":
        flash("OTP sent to your registered email address.")
    else:
        flash(f"[DEMO MODE — set FAST2SMS_API_KEY and MAIL_EMAIL in .env] OTP: {otp}")

    session["otp_mobile"] = mobile
    db_log_audit(user["id"], "OTP_REQUESTED", ip=ip)
    return redirect("/login/verify")


@app.route("/login/verify", methods=["GET", "POST"])
def login_verify():
    if request.method == "POST":
        mobile = session.get("otp_mobile", "")
        otp    = request.form.get("otp", "").strip()
        ip     = get_client_ip()

        if not mobile:
            flash("Session expired. Please start again.")
            return redirect("/login")

        success, error_msg = db_verify_otp(mobile, otp)
        if not success:
            flash(error_msg)
            db_log_audit(None, "OTP_FAIL", details=f"Mobile:{mobile}", ip=ip)
            return redirect("/login/verify")

        user = db_get_user_by_mobile(mobile)
        if not user:
            flash("Account not found.")
            return redirect("/login")

        user_state   = user.get("state", "")
        user_lang    = get_user_language(user_state)
        session.clear()
        session.permanent = True
        session["user_id"]     = user["id"]
        session["user_name"]   = user["first_name"]
        session["user_mobile"] = mobile
        session["user_email"]  = user.get("email", "")
        session["user_lang"]   = user_lang
        session["logged_in_at"]= datetime.now().isoformat()

        db_log_audit(user["id"], "LOGIN_SUCCESS", ip=ip)
        return redirect("/")

    return render_template("login_verify.html",
                           mobile=session.get("otp_mobile", ""))


@app.route("/login/resend_otp", methods=["POST"])
def login_resend_otp():
    """Re-send OTP for the mobile stored in session."""
    import random as _random
    from sms_service import send_otp_sms

    mobile = session.get("otp_mobile", "")
    ip     = get_client_ip()

    if not mobile:
        flash("Session expired. Please start again.")
        return redirect("/login")

    if not db_check_login_attempts(mobile, ip):
        flash("Too many attempts. Please try again in 15 minutes.")
        return redirect("/login/verify")

    user = db_get_user_by_mobile(mobile)
    if not user:
        flash("OTP resent.")
        return redirect("/login/verify")

    otp     = str(_random.SystemRandom().randint(100000, 999999))
    db_create_otp(mobile, otp)
    channel = send_otp_sms(mobile, otp, email=user.get("email", ""))

    if channel == "sms":
        flash("New OTP sent to your mobile number.")
    elif channel == "email":
        flash("New OTP sent to your registered email address.")
    else:
        flash(f"[DEMO MODE] New OTP: {otp}")

    db_log_audit(user["id"], "OTP_RESENT", ip=ip)
    return redirect("/login/verify")

@app.route("/logout")
def logout():
    db_log_audit(session.get("user_id"), "LOGOUT", ip=get_client_ip())
    session.clear()
    return redirect("/login")


# ════════════════════════════════════════════════════════════════════════════
# HEALTH CHECK  (Cloud Run / Docker HEALTHCHECK probe)
# ════════════════════════════════════════════════════════════════════════════

@app.route("/health")
def health_check():
    """Lightweight liveness probe for Cloud Run and Docker HEALTHCHECK.

    Returns 200 if the server is up. Optionally checks MongoDB connectivity
    when ?deep=1 is passed (use for startup probes only — not readiness).
    """
    status = {"status": "ok", "service": "sarathi-ai", "version": "8.1"}
    if request.args.get("deep") == "1":
        try:
            from mongo_db import _get_db
            _get_db().command("ping")
            status["mongodb"] = "connected"
        except Exception as e:
            status["mongodb"] = f"error: {e}"
            return jsonify(status), 503
    return jsonify(status), 200


@app.route("/")
@login_required
def home():
    try:
        user = db_get_user_by_id(session["user_id"]) or {}
    except DBNotAvailable:
        user = {}
    full_name = f"{user.get('first_name','')} {user.get('last_name','')}".strip() or session.get("user_name","Citizen")
    user_lang = session.get("user_lang", get_user_language(user.get("state","")))
    session["user_lang"] = user_lang
    try:
        submissions = db_get_submissions_for_user(session["user_id"])
    except DBNotAvailable:
        submissions = []
    return render_template("dashboard.html",
        user_name=full_name,
        user_lang=user_lang,
        lang_display=LANG_DISPLAY.get(user_lang, "English"),
        user_state=user.get("state",""),
        lang_confirmed=session.get("lang_confirmed", False),
        schemes=SCHEME_DATA,
        submissions=submissions,
        use_gemini=USE_GEMINI,
        use_gcp_stt=USE_GOOGLE_STT,
        use_gcp_tts=USE_GOOGLE_TTS)

@app.route("/service/<scheme_id>")
@login_required
def serve_service(scheme_id):
    # Remove .html suffix if present for backward-compat
    clean_id = scheme_id.replace(".html", "")
    # Map old scheme IDs to new ones
    ID_MAP = {"crop_insurance":"pmfby","aarogyasri":"ayushman_bharat",
              "pension":"atal_pension","ration":"jan_dhan","eseva":"pmbjp",
              "rythu_bandhu":"pm_kisan","dharani":"pm_kisan","kalyana_lakshmi":"sukanya_samriddhi"}
    clean_id = ID_MAP.get(clean_id, clean_id)

    if clean_id not in SCHEME_DATA:
        abort(404)

    scheme    = SCHEME_DATA[clean_id]
    user_id   = session.get("user_id")
    user_lang = session.get("user_lang", "en-IN")

    # Field question templates per language
    FIELD_Q = {
        "en-IN": lambda lbl: f"Please tell me your {lbl}.",
        "te-IN": lambda lbl: f"మీ {lbl} చెప్పండి.",
        "hi-IN": lambda lbl: f"कृपया अपना {lbl} बताएं।",
        "ta-IN": lambda lbl: f"உங்கள் {lbl} சொல்லுங்கள்.",
        "kn-IN": lambda lbl: f"ನಿಮ್ಮ {lbl} ಹೇಳಿ.",
        "ml-IN": lambda lbl: f"നിങ്ങളുടെ {lbl} പറയൂ.",
        "bn-IN": lambda lbl: f"আপনার {lbl} বলুন।",
        "mr-IN": lambda lbl: f"तुमचे {lbl} सांगा.",
        "gu-IN": lambda lbl: f"તમારો {lbl} કહો.",
        "pa-IN": lambda lbl: f"ਆਪਣਾ {lbl} ਦੱਸੋ।",
    }
    q_fn = FIELD_Q.get(user_lang, FIELD_Q["en-IN"])

    # Build SARATHI_FIELDS JSON for the voice engine
    fields_json = []
    for f in scheme["fields"]:
        fid = f["id"]
        fields_json.append({
            "id":        fid,
            "label":     f["label_en"],
            "isNumeric": f["numeric"],
            "isId":      f["id_field"],
            "isName":    f["is_name"],
            "question":  {
                "en-IN":   FIELD_Q["en-IN"](f["label_en"]),
                user_lang: q_fn(f["label_en"]),
            },
            "matchKeywords": [fid.replace("_"," "), f["label_en"].lower()],
        })

    import json as _json
    return render_template("scheme_page.html",
        scheme=scheme,
        fields_json=_json.dumps(fields_json, ensure_ascii=False),
        user_name=session.get("user_name","Citizen"),
        user_lang=user_lang,
        lang_confirmed=session.get("lang_confirmed", False),
        lang_display=LANG_DISPLAY.get(user_lang, "English"),
        use_gemini=USE_GEMINI,
        use_gcp_stt=USE_GOOGLE_STT,
        use_gcp_tts=USE_GOOGLE_TTS)

@app.route("/my_submissions")
@login_required
def my_submissions():
    rows = db_get_submissions_for_user(session["user_id"])
    return render_template("my_submissions.html", submissions=rows,
                           user_name=session.get("user_name","Citizen"))

@app.route("/google-form", methods=["GET","POST"])
@login_required
def google_form():
    form_url = request.form.get("form_url") if request.method == "POST" else None
    return render_template("google_form.html", form_url=form_url)

@app.route("/admin/login", methods=["GET","POST"])
def admin_login():
    if request.method == "POST":
        if request.form.get("password","") == ADMIN_PASSWORD:
            session["is_admin"] = True
            return redirect("/admin")
        flash("Wrong admin password.")
    return render_template("admin_login.html")

@app.route("/admin")
@admin_required
def admin_dashboard():
    try:
        stats   = db_get_admin_stats()
        submissions = db_get_admin_submissions(limit=300)
    except DBNotAvailable:
        flash("⚠️ Database not connected — showing empty data. Set MONGODB_URI in .env.")
        stats = {"total":0,"users":0,"by_scheme":{},"by_lang":{}}
        submissions = []
    health  = {"version":"4.0","db":"mongodb","gcp_creds":_GCP_CREDS_OK,
               "features":{"google_stt":USE_GOOGLE_STT,"google_tts":USE_GOOGLE_TTS,
                            "gemini":USE_GEMINI,"translate":USE_TRANSLATE,"qr_code":QR_AVAILABLE}}
    return render_template("admin.html", stats=stats, submissions=submissions, health=health)


# ════════════════════════════════════════════════════════════════════════════
# AGENT MEMORY API  (MCP document §2 — persistent memory layer)
# ════════════════════════════════════════════════════════════════════════════

@app.route("/api/memory", methods=["GET"])
@login_required
def api_get_memory():
    """Return the agent memory for the current user.
    Used by the dashboard to show what Sarathi remembers about you."""
    try:
        mem = db_get_agent_memory(session["user_id"])
        last_result = db_get_last_eligibility_result(session["user_id"])
        return jsonify({
            "success":      True,
            "memory":       mem.get("memory", {}),
            "updated_at":   mem.get("updated_at", None),
            "last_eligibility": last_result,
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/memory", methods=["POST"])
@login_required
def api_update_memory():
    """Manually update agent memory facts for the current user.
    Accepts JSON body: {"name": "Ramesh", "state": "Telangana", ...}"""
    body = request.get_json(silent=True) or {}
    allowed_keys = {
        "name", "state", "district", "village", "mandal", "pincode",
        "age", "gender", "caste", "occupation", "income", "land_acres",
        "ration_card_type", "is_disabled", "is_widow", "mobile",
    }
    facts = {k: v for k, v in body.items() if k in allowed_keys}
    if not facts:
        return jsonify({"success": False, "error": "No valid fields provided."})
    try:
        db_update_agent_memory(session["user_id"], facts)
        db_log_audit(session["user_id"], "MEMORY_UPDATE",
                     details=f"Updated keys: {list(facts.keys())}",
                     ip=get_client_ip())
        return jsonify({"success": True, "updated": list(facts.keys())})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/memory/clear", methods=["POST"])
@login_required
def api_clear_memory():
    """Clear agent memory for the current user (GDPR / privacy)."""
    try:
        from mongo_db import _get_db
        _get_db().agent_memory.delete_one({"user_id": str(session["user_id"])})
        db_log_audit(session["user_id"], "MEMORY_CLEAR", ip=get_client_ip())
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/conversation_history", methods=["GET"])
@login_required
def api_conversation_history():
    """Return recent conversation turns for the current user."""
    try:
        limit   = min(int(request.args.get("limit", 20)), 100)
        session_id = request.args.get("session_id")
        turns   = db_get_conversation_history(
            session["user_id"], session_id=session_id, limit=limit
        )
        return jsonify({"success": True, "turns": turns, "count": len(turns)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/hybrid_search", methods=["POST"])
@login_required
def api_hybrid_search():
    """Hybrid Atlas text + vector search for schemes.
    Body: {"query": "widow with two children", "state": "Telangana", "limit": 10}
    """
    body  = request.get_json(silent=True) or {}
    query = body.get("query", "").strip()
    state = body.get("state", "")
    limit = min(int(body.get("limit", 10)), 25)
    if not query:
        return jsonify({"success": False, "error": "query is required"})
    try:
        _q_emb_resp = genai_client.models.embed_content(
            model="text-embedding-005",
            contents=query,
        ) if genai_client else None
        emb = (
            list(_q_emb_resp.embeddings[0].values)
            if _q_emb_resp and _q_emb_resp.embeddings else []
        )
        results = db_hybrid_search_schemes(query, emb, state=state or None, limit=limit)
        return jsonify({"success": True, "results": results, "count": len(results)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/admin/update_status", methods=["POST"])
@admin_required
def api_admin_update_status():
    body      = request.get_json(silent=True) or {}
    ref_id    = body.get("ref_id","").strip()
    status    = body.get("status","").strip()
    note      = body.get("note","").strip()
    if not ref_id or status not in ("approved","rejected"):
        return jsonify({"success":False,"error":"Invalid request"}), 400
    ok = db_update_submission_status(ref_id, status, note)
    db_log_audit(None,"ADMIN_"+status.upper(), ref_id=ref_id,
                 details=f"Note:{note}", ip=get_client_ip())
    return jsonify({"success":ok})

@app.route("/admin/logout")
def admin_logout():
    session.pop("is_admin", None); return redirect("/admin/login")

@app.route("/manifest.json")
def pwa_manifest():
    return app.send_static_file("manifest.json")

@app.route("/sw.js")
def service_worker():
    r = app.send_static_file("sw.js")
    r.headers["Content-Type"] = "application/javascript"
    r.headers["Cache-Control"] = "no-cache"
    return r

@app.after_request
def add_security_headers(response):
    # FIX: microphone=(self) is valid; * is not allowed in Permissions-Policy
    response.headers["Permissions-Policy"] = "microphone=(self), camera=()"
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"]        = "SAMEORIGIN"
    response.headers["Referrer-Policy"]        = "strict-origin-when-cross-origin"
    return response

# ════════════════════════════════════════════════════════════════════════
# ── Graceful DB-unavailable error handler ────────────────────────────────────
@app.errorhandler(DBNotAvailable)
def handle_db_unavailable(e):
    """Shows a user-friendly error page when MongoDB is unreachable."""
    return render_template("db_error.html", error=str(e)), 503


@app.errorhandler(500)
def handle_500(e):
    """Catch unhandled 500s and check if they're DB-related."""
    err = str(e)
    if "ServerSelectionTimeout" in err or "localhost:27017" in err or "MongoDB" in err:
        return render_template("db_error.html",
            error="MongoDB connection failed. Please set MONGODB_URI in .env"), 503
    return render_template("db_error.html", error=f"Internal server error: {e}"), 500


if __name__ == "__main__":
    # ── Start MongoDB MCP server (non-blocking, best-effort) ─────────────────
    try:
        from mcp_client import init_mcp
        _mcp = init_mcp()
        _mcp_status = f"✅  {len(_mcp.list_tools())} tools" if _mcp else "⚠️  npx/Node.js not found (pymongo fallback active)"
    except Exception as _e:
        _mcp_status = f"⚠️  {_e}"

    _gemini_backend = (
        f"Vertex AI ({os.environ.get('GOOGLE_CLOUD_PROJECT','?')}/{os.environ.get('GOOGLE_CLOUD_LOCATION','us-central1')})"
        if os.environ.get("GOOGLE_CLOUD_PROJECT") else "API key (dev mode)"
    )

    print("🚀 Project Sarathi v10 — Gemini 3.5 Flash + Vertex AI + MongoDB MCP")
    print(f"   Model       : gemini-3.5-flash ({_gemini_backend})")
    print(f"   MongoDB MCP : {_mcp_status}")
    print(f"   Encryption  : {'✅ Fernet AES-128' if CRYPTO_AVAILABLE else '⚠️  DISABLED'}")
    print(f"   GCP Creds   : {'✅' if _GCP_CREDS_OK else '⚠️  Not configured — browser fallback active'}")
    print(f"   GCP STT     : {'✅' if USE_GOOGLE_STT else '⚠️  Browser STT active'}")
    print(f"   GCP TTS     : {'✅ WaveNet' if USE_GOOGLE_TTS else '⚠️  Browser TTS active'}")
    print(f"   Gemini AI   : {'✅' if USE_GEMINI else '⚠️  Set GOOGLE_CLOUD_PROJECT or GEMINI_API_KEY'}")
    print(f"   Translate   : {'✅' if USE_TRANSLATE else '⚠️  Disabled'}")
    print(f"   QR Codes    : {'✅' if QR_AVAILABLE else '⚠️  pip install qrcode pillow'}")
    print(f"   Async mode  : GEVENT")
    print("   Open: http://127.0.0.1:5000/login")
    import webbrowser
    if os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        webbrowser.open("http://127.0.0.1:5000/login")
    # debug=False + use_reloader=False fixes WinError 10038 on Python 3.13 Windows
    socketio.run(app, debug=False, port=5000, host="0.0.0.0",
                 use_reloader=False, allow_unsafe_werkzeug=True)


# ════════════════════════════════════════════════════════════════════════════
# PDF GENERATION  (v4.0)
# ════════════════════════════════════════════════════════════════════════════

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
    from reportlab.lib.units import cm
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    PDF_AVAILABLE = True
except ImportError:
    PDF_AVAILABLE = False
    print("⚠️  pip install reportlab for PDF generation")

try:
    from PIL import Image as PILImage
    import pytesseract
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False
    print("⚠️  pip install pytesseract pillow for OCR support")

SCHEME_DISPLAY_NAMES = {sid: f"{sd['name']} — {sd['full_name']}" for sid, sd in SCHEME_DATA.items()}
SCHEME_DISPLAY_NAMES.update({
    "rythu_bandhu":"PM-KISAN — Farmer Income Support",
    "aarogyasri":"Ayushman Bharat — PMJAY",
    "crop_insurance":"PMFBY — Crop Insurance",
    "pension":"Atal Pension Yojana","eseva":"PM Janaushadhi",
})

@app.route("/api/generate_pdf", methods=["POST"])
@login_required
def api_generate_pdf():
    if not PDF_AVAILABLE:
        return jsonify({"error": "PDF generation not available. pip install reportlab"}), 500

    body     = request.get_json(silent=True) or {}
    ref_id   = body.get("ref_id", f"SRTH-{uuid.uuid4().hex[:8].upper()}")
    data     = body.get("data", {})
    service  = body.get("service", "scheme")
    lang     = body.get("lang", "en-IN")
    user_id  = session["user_id"]
    username = session.get("user_name", "Citizen")

    scheme_name = SCHEME_DISPLAY_NAMES.get(service, service.replace("_"," ").title())
    timestamp   = datetime.now().strftime("%d %B %Y, %I:%M %p")

    buffer = io.BytesIO()
    doc    = SimpleDocTemplate(buffer, pagesize=A4,
                                leftMargin=2*cm, rightMargin=2*cm,
                                topMargin=2*cm, bottomMargin=2*cm)

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("title", parent=styles["Title"],
                                 fontSize=18, fontName="Helvetica-Bold",
                                 textColor=colors.HexColor("#1a237e"),
                                 alignment=TA_CENTER, spaceAfter=6)
    subtitle_style = ParagraphStyle("subtitle", parent=styles["Normal"],
                                     fontSize=10, alignment=TA_CENTER,
                                     textColor=colors.HexColor("#4b5563"), spaceAfter=4)
    ref_style = ParagraphStyle("ref", parent=styles["Normal"],
                                fontSize=14, fontName="Helvetica-Bold",
                                textColor=colors.HexColor("#2e7d32"),
                                alignment=TA_CENTER, spaceAfter=8)
    label_style = ParagraphStyle("lbl", parent=styles["Normal"],
                                  fontSize=9, textColor=colors.HexColor("#6b7280"),
                                  fontName="Helvetica-Bold", spaceAfter=2)
    value_style = ParagraphStyle("val", parent=styles["Normal"],
                                  fontSize=11, textColor=colors.HexColor("#1a1a2e"),
                                  fontName="Helvetica", spaceAfter=8)
    footer_style = ParagraphStyle("footer", parent=styles["Normal"],
                                   fontSize=8, alignment=TA_CENTER,
                                   textColor=colors.HexColor("#9ca3af"))

    story = []
    # Header
    story.append(Paragraph("🇮🇳 SARATHI AI — Government Services Portal", title_style))
    story.append(Paragraph(scheme_name, subtitle_style))
    story.append(HRFlowable(width="100%", thickness=2, color=colors.HexColor("#1a237e")))
    story.append(Spacer(1, 0.4*cm))
    story.append(Paragraph(f"Reference ID: {ref_id}", ref_style))
    story.append(Paragraph(f"Submitted: {timestamp}  |  Applicant: {username}", subtitle_style))
    story.append(Spacer(1, 0.6*cm))

    # Fields table
    story.append(Paragraph("APPLICATION DETAILS", ParagraphStyle("sec",
        parent=styles["Normal"], fontSize=10, fontName="Helvetica-Bold",
        textColor=colors.HexColor("#1a237e"), spaceAfter=8)))

    table_data = [["Field", "Value"]]
    for key, val in data.items():
        label = key.replace("_"," ").title()
        display_val = val
        # Mask sensitive fields in PDF
        if "aadhaar" in key.lower() and len(str(val)) == 12:
            display_val = f"XXXX XXXX {str(val)[8:]}"
        elif "account" in key.lower() and len(str(val)) > 4:
            display_val = f"XXXX XXXX {str(val)[-4:]}"
        table_data.append([label, display_val])

    table = Table(table_data, colWidths=[6*cm, 10*cm])
    table.setStyle(TableStyle([
        ("BACKGROUND",  (0,0), (-1,0),  colors.HexColor("#1a237e")),
        ("TEXTCOLOR",   (0,0), (-1,0),  colors.white),
        ("FONTNAME",    (0,0), (-1,0),  "Helvetica-Bold"),
        ("FONTSIZE",    (0,0), (-1,0),  10),
        ("ALIGN",       (0,0), (-1,-1), "LEFT"),
        ("VALIGN",      (0,0), (-1,-1), "MIDDLE"),
        ("FONTNAME",    (0,1), (-1,-1), "Helvetica"),
        ("FONTSIZE",    (0,1), (-1,-1), 10),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.HexColor("#f8fafc"), colors.white]),
        ("GRID",        (0,0), (-1,-1), 0.5, colors.HexColor("#e2e8f0")),
        ("PADDING",     (0,0), (-1,-1), 8),
        ("TOPPADDING",  (0,0), (-1,-1), 8),
        ("BOTTOMPADDING",(0,0),(-1,-1), 8),
    ]))
    story.append(table)
    story.append(Spacer(1, 0.6*cm))

    # Status box
    status_data = [["Status", "Submitted ✓"],
                   ["Reference ID", ref_id],
                   ["Language", lang],
                   ["Portal", "Sarathi AI — sarathi.gov.in"]]
    status_table = Table(status_data, colWidths=[6*cm, 10*cm])
    status_table.setStyle(TableStyle([
        ("BACKGROUND",  (0,0), (-1,-1), colors.HexColor("#e8f5e9")),
        ("TEXTCOLOR",   (0,0), (0,-1),  colors.HexColor("#1b5e20")),
        ("FONTNAME",    (0,0), (0,-1),  "Helvetica-Bold"),
        ("FONTSIZE",    (0,0), (-1,-1), 10),
        ("ALIGN",       (0,0), (-1,-1), "LEFT"),
        ("GRID",        (0,0), (-1,-1), 0.5, colors.HexColor("#a5d6a7")),
        ("PADDING",     (0,0), (-1,-1), 8),
    ]))
    story.append(status_table)
    story.append(Spacer(1, 1*cm))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#e2e8f0")))
    story.append(Spacer(1, 0.3*cm))

    # Footer
    story.append(Paragraph(
        "This is an AI-assisted application submitted via Project Sarathi. "
        "All data is AES-128 encrypted and HMAC-SHA256 verified. "
        f"Powered by Sarathi AI | Reference: {ref_id}",
        footer_style
    ))

    doc.build(story)
    buffer.seek(0)

    db_log_audit(user_id, "PDF_GENERATED", ref_id=ref_id, ip=get_client_ip())

    return send_file(
        buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"Sarathi_{service}_{ref_id}.pdf"
    )

# ─────────────────────────────────────────────────────────────────────────────
# PASSPORT PHOTO VALIDATION  (Gemini Vision)
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/validate_photo", methods=["POST"])
@login_required
def api_validate_photo():
    """
    Validate an uploaded passport-size photo using Gemini Vision.
    Returns JSON: {valid: bool, reason: str}
    Falls back to accepting any image if Gemini is unavailable.
    """
    file = request.files.get("photo")
    if not file:
        return jsonify({"valid": False, "reason": "No photo uploaded"}), 400

    img_bytes = file.read()
    if not img_bytes:
        return jsonify({"valid": False, "reason": "Empty file"}), 400

    mime_type = file.content_type or "image/jpeg"
    if not mime_type.startswith("image/"):
        return jsonify({"valid": False, "reason": "File is not an image"}), 400

    # If AI is not configured, accept any image silently
    if not USE_GEMINI or not genai_client:
        return jsonify({"valid": True, "reason": "Photo accepted"})

    try:
        prompt = (
            "Look at this image carefully. "
            "Is this a valid passport-size photograph showing a single human face "
            "clearly, looking forward, with a plain background? "
            "Check: (1) face is clearly visible, (2) single person, (3) not blurry, "
            "(4) not a group photo or landscape. "
            "Reply ONLY with valid JSON, no markdown: "
            "{\"valid\": true, \"reason\": \"brief explanation\"}"
        )
        response = genai_client.models.generate_content(
            model="gemini-3.5-flash",
            contents=[
                _genai_types.Part(
                    inline_data=_genai_types.Blob(
                        mime_type=mime_type,
                        data=img_bytes,
                    )
                ),
                _genai_types.Part(text=prompt),
            ],
            config=_genai_types.GenerateContentConfig(temperature=0.0),
        )
        raw = (response.text or "").strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        return jsonify(json.loads(raw))
    except Exception as e:
        print(f"[Photo Validate] Gemini error: {e} — accepting photo anyway")
        return jsonify({"valid": True, "reason": "Photo accepted"})