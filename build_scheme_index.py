"""
build_scheme_index.py — Run ONCE to populate MongoDB Atlas with India scheme data
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Downloads 1000+ Indian government schemes from HuggingFace (shrijayan/gov_myscheme),
creates Gemini embeddings, and stores them in MongoDB Atlas for Vector Search.

Usage:
  python build_scheme_index.py

After running, create a Vector Search index in Atlas UI:
  Collection  : sarathi_db.india_schemes
  Field       : embedding
  Dimensions  : 768
  Similarity  : cosine
  Index Name  : scheme_vector_index
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import os, json
from dotenv import load_dotenv

load_dotenv()

MONGODB_URI  = os.environ.get("MONGODB_URI")
GEMINI_KEY   = os.environ.get("GEMINI_API_KEY")
BATCH_SIZE   = 50


def build_atlas_scheme_index():
    """
    Downloads 1000+ India schemes from HuggingFace,
    creates embeddings, stores in MongoDB Atlas for Vector Search.
    Run this once before starting the server.
    """
    if not MONGODB_URI:
        print("❌  MONGODB_URI not set in .env — aborting.")
        return
    if not GEMINI_KEY:
        print("❌  GEMINI_API_KEY not set in .env — aborting.")
        return

    print("📥  Loading myScheme dataset from HuggingFace (shrijayan/gov_myscheme)...")
    try:
        from datasets import load_dataset
    except ImportError:
        print("❌  datasets not installed. Run: pip install datasets")
        return

    try:
        from google import genai as _genai_bsi
    except ImportError:
        print("❌  google-genai not installed. Run: pip install google-genai")
        return

    try:
        from pymongo import MongoClient
    except ImportError:
        print("❌  pymongo not installed.")
        return

    dataset = load_dataset("shrijayan/gov_myscheme", split="train")
    print(f"✅  Loaded {len(dataset)} schemes.")

    client = MongoClient(MONGODB_URI)
    db     = client["sarathi_db"]
    col    = db["india_schemes"]

    # Fresh build — drop existing
    existing = col.count_documents({})
    if existing > 0:
        confirm = input(f"⚠️  Collection already has {existing} docs. Rebuild? [y/N]: ").strip().lower()
        if confirm != "y":
            print("Aborted.")
            return
    col.drop()
    print("🗑️   Old collection dropped. Building fresh index...")

    _gcp_proj = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
    _gcp_loc  = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
    if _gcp_proj:
        _bsi_client = _genai_bsi.Client(vertexai=True, project=_gcp_proj, location=_gcp_loc)
        print(f"[Index] Using Vertex AI ({_gcp_proj}/{_gcp_loc})")
    elif GEMINI_KEY:
        _bsi_client = _genai_bsi.Client(api_key=GEMINI_KEY)
        print("[Index] Using Gemini API key (dev mode)")
    else:
        print("❌  No Gemini credentials — set GOOGLE_CLOUD_PROJECT or GEMINI_API_KEY")
        return

    batch   = []
    skipped = 0

    for i, row in enumerate(dataset):
        # Rich text blob for embedding
        text_for_embedding = f"""
Scheme: {row.get('scheme_name', '')}
State: {row.get('state', 'All India')}
Eligibility: {row.get('eligibility_criteria', '')}
Benefits: {row.get('benefits', '')}
Category: {row.get('category', '')}
""".strip()

        # Get embedding from Gemini
        try:
            result    = _bsi_client.models.embed_content(
                model="text-embedding-005",
                contents=text_for_embedding,
            )
            embedding = list(result.embeddings[0].values) if result and result.embeddings else []
        except Exception as e:
            print(f"  ⚠️  Embedding failed for '{row.get('scheme_name')}': {e}")
            embedding = []
            skipped  += 1

        doc = {
            "scheme_name":         row.get("scheme_name", ""),
            "state":               row.get("state", "All India"),
            "category":            row.get("category", ""),
            "eligibility":         row.get("eligibility_criteria", ""),
            "benefits":            row.get("benefits", ""),
            "application_process": row.get("application_process", ""),
            "official_link":       row.get("official_link", ""),
            "embedding":           embedding,
            "source":              "myscheme.gov.in",
        }
        batch.append(doc)

        if len(batch) >= BATCH_SIZE:
            col.insert_many(batch)
            print(f"  ✅  Inserted {i + 1}/{len(dataset)} schemes...")
            batch = []

    if batch:
        col.insert_many(batch)

    total = col.count_documents({})
    print(f"\n✅  Done! {total} schemes indexed in Atlas.  (skipped embeddings: {skipped})")
    print("\n👉  Next step — create Vector Search index in Atlas UI:")
    print("   Collection : sarathi_db.india_schemes")
    print("   Field      : embedding")
    print("   Dimensions : 768")
    print("   Similarity : cosine")
    print("   Index name : scheme_vector_index")


if __name__ == "__main__":
    build_atlas_scheme_index()
