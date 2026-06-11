"""
eligibility_engine.py — All-India Eligibility Engine for Sarathi AI
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Layer 3: data.gov.in live API calls (PM-KISAN stats, PMFBY crop data)
Layer 4: Master engine — Gemini reasons over Vector Search + live data

Flow for each eligibility check:
  1. Build user profile text → get Gemini embedding
  2. MongoDB Atlas Vector Search → top 15 matching schemes
  3. data.gov.in API calls for live beneficiary stats
  4. Gemini 2.0 Flash reasons over everything
  5. Return ranked qualifying schemes with sources
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import os, json, re as _re
from datetime import datetime

import requests

DATA_GOV_API_KEY = os.environ.get("DATA_GOV_API_KEY", "")
DATA_GOV_BASE    = "https://api.data.gov.in/resource"

# Resource IDs for live government datasets
PMKISAN_RESOURCE_ID = "35985678-0d79-46b4-9ed6-6f13308a1d24"
PMFBY_RESOURCE_ID   = "9ef84268-d588-465a-a308-a864a43d0070"


# ── Layer 3: data.gov.in Live API Calls ──────────────────────────────────────

def fetch_pmkisan_stats(state: str) -> dict:
    """Fetches live PM-KISAN beneficiary counts for a state from data.gov.in."""
    if not DATA_GOV_API_KEY:
        return {}
    try:
        resp = requests.get(
            f"{DATA_GOV_BASE}/{PMKISAN_RESOURCE_ID}",
            params={
                "api-key":              DATA_GOV_API_KEY,
                "format":               "json",
                "limit":                5,
                "filters[State_Name]":  state.upper(),
            },
            timeout=8,
        )
        if resp.status_code == 200:
            data    = resp.json()
            records = data.get("records", [])
            if records:
                return {
                    "source":        "data.gov.in (live)",
                    "state":         state,
                    "beneficiaries": records[0].get("Total_No_of_Beneficiaries", "N/A"),
                    "fetched_at":    datetime.now().isoformat(),
                }
    except Exception as e:
        print(f"data.gov.in PM-KISAN API error: {e}")
    return {}


def fetch_crop_insurance_stats(state: str) -> dict:
    """Fetches live PMFBY crop insurance stats for a state from data.gov.in."""
    if not DATA_GOV_API_KEY:
        return {}
    try:
        resp = requests.get(
            f"{DATA_GOV_BASE}/{PMFBY_RESOURCE_ID}",
            params={
                "api-key":               DATA_GOV_API_KEY,
                "format":                "json",
                "limit":                 3,
                "filters[state_name]":   state,
            },
            timeout=8,
        )
        if resp.status_code == 200:
            return resp.json().get("records", [{}])[0]
    except Exception as e:
        print(f"data.gov.in PMFBY API error: {e}")
    return {}


# ── Layer 4: Master Eligibility Engine ───────────────────────────────────────

def check_eligibility_india(user_profile: dict, scheme_name: str = None) -> dict:
    """
    All-India eligibility engine — 4 layers working together.

    Args:
        user_profile: dict with keys like state, district, age, gender,
                      occupation, income, land_acres, ration_card_type, etc.
        scheme_name:  optional specific scheme to check (or None for all)

    Returns:
        dict with qualifying_schemes, not_qualifying, total_annual_benefit,
        data_sources, message, vector_matches_count, live_data_fetched
    """
    try:
        from google import genai as _genai_sdk
        from google.genai import types as _gtypes_ee
        # Vertex AI preferred, API key fallback
        _gcp_proj = os.environ.get("GOOGLE_CLOUD_PROJECT","")
        _ee_key   = os.environ.get("GEMINI_API_KEY","")
        if _gcp_proj:
            _ee_client = _genai_sdk.Client(vertexai=True, project=_gcp_proj, location=os.environ.get("GOOGLE_CLOUD_LOCATION","us-central1"))
        elif _ee_key:
            _ee_client = _genai_sdk.Client(api_key=_ee_key)
        else:
            return _fallback_result([], "Gemini not configured.")
    except ImportError:
        return _fallback_result([], "Gemini not available.")

    state    = user_profile.get("state", "")
    district = user_profile.get("district", "")

    # ── Step 1: Build natural-language profile text ───────────────────────────
    user_query = f"""
Citizen profile:
State: {state}, District: {district}
Age: {user_profile.get('age', 'unknown')}
Gender: {user_profile.get('gender', 'unknown')}
Occupation: {user_profile.get('occupation', 'farmer')}
Annual income: ₹{user_profile.get('income', 'unknown')}
Caste: {user_profile.get('caste', 'unknown')}
Land owned: {user_profile.get('land_acres', 0)} acres
Ration card: {user_profile.get('ration_card_type', 'none')}
Disability: {user_profile.get('is_disabled', False)}
Widow: {user_profile.get('is_widow', False)}
Specific scheme interested in: {scheme_name or 'any matching scheme'}
""".strip()

    # ── Step 2: Gemini embedding ──────────────────────────────────────────────
    query_embedding = []
    try:
        result = _ee_client.models.embed_content(
            model="text-embedding-005",
            contents=user_query,
        )
        query_embedding = list(result.embeddings[0].values) if result and result.embeddings else []
    except Exception as e:
        print(f"Embedding error: {e}")

    # ── Step 3: Atlas Vector Search ───────────────────────────────────────────
    vector_matches = []
    if query_embedding:
        try:
            from mongo_db import db_find_matching_schemes
            vector_matches = db_find_matching_schemes(
                query_embedding, state=state, limit=15
            )
        except Exception as e:
            print(f"Vector search error: {e}")

    # ── Step 4: Live data.gov.in API calls ───────────────────────────────────
    live_data = {}
    if state:
        live_data["pmkisan_stats"]  = fetch_pmkisan_stats(state)
        live_data["crop_insurance"] = fetch_crop_insurance_stats(state)

    # ── Step 5: Gemini reasons over everything ────────────────────────────────
    _ee_model = "gemini-3.5-flash"

    top_matches_json = json.dumps(
        [
            {
                "name":        s["scheme_name"],
                "state":       s.get("state", "All India"),
                "eligibility": s.get("eligibility", "")[:300],
                "benefits":    s.get("benefits", "")[:200],
                "score":       round(s.get("score", 0), 3),
                "link":        s.get("official_link", ""),
            }
            for s in vector_matches[:10]
        ],
        ensure_ascii=False,
        indent=2,
    )

    prompt = f"""
You are an expert in Indian government welfare schemes covering all 36 states/UTs.

Citizen profile:
{user_query}

Top matching schemes found via MongoDB Atlas Vector Search from myscheme.gov.in data:
{top_matches_json}

Live government data (from data.gov.in API):
{json.dumps(live_data, ensure_ascii=False, indent=2)}

Tasks:
1. From the vector search results, identify which schemes this citizen ACTUALLY qualifies for
2. Rank them by relevance and benefit amount
3. For each qualifying scheme, state clearly WHY they qualify based on their specific profile
4. List schemes they do NOT qualify for and why
5. Calculate total annual benefit if they apply for all qualifying schemes

Reply ONLY with valid JSON — no markdown, no code fences:
{{
  "qualifying_schemes": [
    {{
      "name": "scheme name",
      "benefit": "₹ amount or description",
      "reason": "why they qualify — specific to their profile",
      "apply_url": "official URL",
      "state_specific": true,
      "priority": "high"
    }}
  ],
  "not_qualifying": [
    {{"name": "scheme", "reason": "why not eligible"}}
  ],
  "total_annual_benefit": "₹X,XX,XXX",
  "data_sources": ["myscheme.gov.in via Atlas Vector Search", "data.gov.in live API"],
  "message": "personalised summary for the citizen"
}}
"""

    try:
        response = _ee_client.models.generate_content(
            model=_ee_model,
            contents=prompt,
        )
        text   = (response.text or "").strip()
        text   = _re.sub(r"```json|```", "", text).strip()
        result = json.loads(text)
        result["vector_matches_count"] = len(vector_matches)
        result["live_data_fetched"]    = bool(live_data)
        return result

    except Exception as e:
        print(f"Gemini eligibility reasoning error: {e}")
        # Graceful fallback: return raw vector results
        return _fallback_result(vector_matches, f"AI reasoning failed: {e}")


def _fallback_result(vector_matches: list, reason: str) -> dict:
    """Returns a basic result from vector matches when Gemini reasoning fails."""
    return {
        "qualifying_schemes": [
            {
                "name":      s["scheme_name"],
                "benefit":   s.get("benefits", ""),
                "reason":    f"Semantic match score: {s.get('score', 0):.2f}",
                "apply_url": s.get("official_link", ""),
                "state_specific": s.get("state", "All India") != "All India",
                "priority":  "medium",
            }
            for s in vector_matches[:5]
        ],
        "not_qualifying":        [],
        "total_annual_benefit":  "Calculating...",
        "data_sources":          ["myscheme.gov.in via Atlas Vector Search"],
        "message":               f"Found matching schemes based on your profile. ({reason})",
        "vector_matches_count":  len(vector_matches),
        "live_data_fetched":     False,
    }


# ── National Scheme ID Aliases ────────────────────────────────────────────────
NATIONAL_SCHEME_IDS = {
    "pm_kisan","pmfby","ayushman_bharat","swachh_bharat","jal_jeevan",
    "jan_dhan","mudra_yojana","pmjjby","pmsby","atal_pension",
    "pmay","ujjwala","mgnrega","pm_svanidhi","pm_vishwakarma",
    "pm_surya_ghar","pmgsy","lakhpati_didi","sukanya_samriddhi",
    "poshan_abhiyaan","pmkvy","standup_india","pm_sym","pmbjp","pmmsy"
}
