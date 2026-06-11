# Project Sarathi v8.1 — AI Government Scheme Assistant
### MongoDB MCP Edition | Agent Memory | Hybrid Search | Google Cloud Ready

---

## ⚡ Quick Start — Localhost

```bash
# 1. Clone / unzip the project
cd sarathi_v8

# 2. Create virtual environment (recommended)
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set up environment
cp .env.example .env
# Edit .env and add:  MONGODB_URI, GEMINI_API_KEY, SARATHI_SECRET

# 5. Run (auto-checks everything)
python run_local.py
```

Open: **http://127.0.0.1:5000/login**

---

## 🔧 .env Required Keys

| Key | Where to get it |
|-----|----------------|
| `MONGODB_URI` | Atlas Dashboard → Connect → Drivers → Python |
| `GEMINI_API_KEY` | https://aistudio.google.com/app/apikey |
| `SARATHI_SECRET` | Any random 64-char string |
| `ADMIN_PASSWORD` | Your choice (default: SarathiAdmin@2026!) |

Optional (for OTP):
| `FAST2SMS_API_KEY` | https://www.fast2sms.com (free ₹50 credits) |
| `MAIL_EMAIL` + `MAIL_APP_PASSWORD` | Gmail App Password |

---

## ☁️ Google Cloud Run Deployment

```bash
# Prerequisites:
#   gcloud CLI installed + authenticated (gcloud auth login)
#   .env filled in (same file as localhost)

bash deploy.sh
```

`deploy.sh` automatically:
- Enables all required GCP APIs
- Stores all secrets in Secret Manager (never baked into image)
- Builds and deploys to Cloud Run (asia-south1 / Mumbai)
- Injects MongoDB URI, Gemini key etc. securely at runtime

**⚠️ Important — MongoDB Atlas IP Whitelist:**
Cloud Run uses dynamic IPs. In Atlas → Network Access → Add IP: `0.0.0.0/0`

---

## 📦 One-time Setup — Vector Search Index

After first deploy (or first local run), populate the schemes database:

```bash
pip install datasets          # one-time
python build_scheme_index.py  # downloads 1000+ schemes, creates embeddings
```

Then in **MongoDB Atlas UI → Search → Create Index**:

| Setting | Value |
|---------|-------|
| Index Name | `scheme_vector_index` |
| Collection | `sarathi_db.india_schemes` |
| Field | `embedding` |
| Dimensions | `768` |
| Similarity | `cosine` |

And create a text index:

| Setting | Value |
|---------|-------|
| Index Name | `scheme_text_index` |
| Fields | `scheme_name, eligibility, benefits, category` |

---

## 🏗️ Architecture

```
User (Voice/Text)
     ↓
Flask-SocketIO Server (server.py)
     ↓
┌────────────────────────────────┐
│  Gemini 2.0 Flash              │
│  ├─ Conversation (memory-aware)│
│  ├─ Eligibility Check          │
│  └─ Hybrid Scheme Search       │
└────────────────────────────────┘
     ↓
MongoDB Atlas
├── users                  (citizen profiles)
├── conversations          (chat history — TTL 90 days)
├── agent_memory           (persistent user facts)
├── eligibility_results    (check history — TTL 30 days)
├── form_submissions       (scheme applications)
├── india_schemes          (1000+ schemes + embeddings)
├── audit_log
├── otps                   (TTL — auto-deleted)
├── rate_limits
└── translation_cache
```

### Hybrid Search Flow (§4)
```
User Query
    ↓
Atlas Text Search (exact keywords)    Vector Search (semantic embedding)
    ↓                                      ↓
    └──────────── Merge & Deduplicate ─────┘
                        ↓
                    Gemini Response
```

### Agent Memory Flow (§2)
```
First conversation:
  User: "I am Ramesh, farmer in Telangana, income 1.5 lakh"
  → Stored in agent_memory collection

Later conversation:
  User: "Suggest schemes"
  → Sarathi automatically knows: farmer, Telangana, 1.5L income
  → Never asks again ✓
```

---

## 🛠️ MCP Tools (§3)

| Tool | Purpose |
|------|---------|
| `search_schemes()` | Hybrid Atlas + Vector search |
| `get_user_profile()` | Profile + persistent memory merged |
| `save_conversation()` | Persist chat turns to MongoDB |
| `update_memory()` | Upsert learned facts into agent_memory |
| `mongodb_query_submissions` | Query past applications |
| `mongodb_save_submission` | Save new application |
| `mongodb_get_admin_stats` | Admin dashboard aggregations |

---

## 📡 API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Cloud Run health check |
| `/api/memory` | GET | Get agent memory for current user |
| `/api/memory` | POST | Update memory facts |
| `/api/memory/clear` | POST | Clear memory (privacy) |
| `/api/conversation_history` | GET | Get conversation history |
| `/api/hybrid_search` | POST | Hybrid scheme search |
| `/api/eligibility_check` | POST | Full eligibility engine |
| `/api/scheme_recommend` | POST | AI scheme recommendations |
| `/api/gemini_chat` | POST | Scheme application conversation |
| `/api/submit_form` | POST | Submit scheme application |

---

## 🐍 Python Compatibility

| Environment | Python | Async Mode |
|------------|--------|-----------|
| Localhost | 3.10–3.12 | eventlet |
| Localhost | 3.13+ | threading |
| Cloud Run (gunicorn) | 3.12 | gevent |

Auto-detected — no configuration needed.
