# Project Sarathi v11 — AI Government Scheme Assistant
### Google Cloud Rapid Agent Hackathon · MongoDB MCP Partner

> **Multilingual voice AI that helps Indian citizens discover and apply for 1000+ government welfare schemes — in their own language, one question at a time.**

---

## 🏗️ Tech Stack

| Layer | Technology |
|-------|-----------|
| **LLM** | Gemini 3.5 Flash via Vertex AI (`google-genai` SDK) |
| **Embeddings** | `text-embedding-005` — 768-dim, matched to Atlas Vector index |
| **Agent** | Gemini function-calling loop (6-round agentic execution) |
| **Database** | MongoDB Atlas — single data store for all collections |
| **MCP** | `@mongodb-js/mongodb-mcp-server` — JSON-RPC 2.0 stdio transport |
| **Voice** | Google Cloud Speech-to-Text + Text-to-Speech (9 Indian languages) |
| **Deployment** | Google Cloud Run (asia-south1 / Mumbai) |
| **Translation** | Google Cloud Translation API |
| **Secrets** | Google Cloud Secret Manager |

---

## ⚡ Quick Start — Localhost

```bash
# 1. Clone / unzip the project
cd sarathi_v11

# 2. Create virtual environment (recommended)
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set up environment
cp .env.example .env
# Edit .env and fill in the required keys below

# 5. Run
python run_local.py
```

Open: **http://127.0.0.1:5000/login**

> **Note:** Node.js 18+ is required for the MongoDB MCP server.
> Install from https://nodejs.org — the MCP server auto-downloads on first run via `npx`.

---

## 🔧 Environment Variables

**Required:**

| Key | Where to get it |
|-----|----------------|
| `MONGODB_URI` | Atlas Dashboard → Connect → Drivers → Python |
| `GEMINI_API_KEY` | https://aistudio.google.com/app/apikey (local dev only) |
| `SARATHI_SECRET` | Any random 64-char string |
| `ADMIN_PASSWORD` | Your choice (default: `SarathiAdmin@2026!`) |

**Optional — OTP login:**

| Key | Where to get it |
|-----|----------------|
| `FAST2SMS_API_KEY` | https://www.fast2sms.com (free ₹50 credits) |
| `MAIL_EMAIL` + `MAIL_APP_PASSWORD` | Gmail App Password |

**Optional — live government data:**

| Key | Where to get it |
|-----|----------------|
| `DATA_GOV_API_KEY` | https://data.gov.in (free registration) |

> On Cloud Run, `GEMINI_API_KEY` is not needed — Vertex AI uses Application Default Credentials automatically.

---

## ☁️ Google Cloud Run Deployment

```bash
# Prerequisites:
#   gcloud CLI installed + authenticated (gcloud auth login)
#   .env filled in (same file as localhost)

bash deploy.sh
```

`deploy.sh` automatically:
- Enables all required GCP APIs (Cloud Run, Vertex AI, Speech, TTS, Translate, Secret Manager)
- Creates Artifact Registry repository if it doesn't exist
- Builds Docker image via Cloud Build
- Deploys to Cloud Run (asia-south1 / Mumbai) with 2Gi RAM, 2 CPU
- Injects all environment variables securely at runtime

**⚠️ MongoDB Atlas IP Whitelist:**
Cloud Run uses dynamic IPs. In Atlas → Network Access → Add IP: `0.0.0.0/0`

---

## 📦 One-time Setup — Scheme Database & Vector Index

After first deploy (or first local run), seed the schemes database:

```bash
pip install datasets          # one-time
python build_scheme_index.py  # downloads 1000+ schemes, creates Gemini embeddings
```

Then in **MongoDB Atlas UI → Search → Create Index:**

**Vector Search index:**

| Setting | Value |
|---------|-------|
| Index Name | `scheme_vector_index` |
| Collection | `sarathi_db.india_schemes` |
| Field | `embedding` |
| Dimensions | `768` |
| Similarity | `cosine` |

**Text Search index:**

| Setting | Value |
|---------|-------|
| Index Name | `scheme_text_index` |
| Fields | `scheme_name, eligibility, benefits, category` |

---

## 🏗️ Architecture

```
User (Voice / Text — 9 Indian languages)
     ↓
Flask-SocketIO Server  (server.py)  ←→  Google Cloud STT / TTS
     ↓
┌─────────────────────────────────────────┐
│  Gemini 3.5 Flash  (Vertex AI)          │
│  ├─ Agentic function-calling loop       │
│  ├─ Conversation (memory-aware)         │
│  ├─ Eligibility check + reasoning       │
│  └─ Hybrid scheme search                │
└─────────────────────────────────────────┘
     ↓
MongoDB MCP Server  (@mongodb-js/mongodb-mcp-server)
     ↓  (JSON-RPC 2.0 stdio · pymongo fallback)
MongoDB Atlas
├── users                  (citizen profiles)
├── conversations          (chat history — TTL 90 days)
├── agent_memory           (persistent user facts)
├── eligibility_results    (check history — TTL 30 days)
├── form_submissions       (Fernet-encrypted applications)
├── india_schemes          (1000+ schemes + 768-dim embeddings)
├── audit_log
├── otps                   (TTL — auto-deleted)
├── rate_limits
└── translation_cache      (TTL 30 days)
```

---

## 🔍 Hybrid Search Flow

```
User Query
    ↓
Atlas Text Search (exact keywords)    Vector Search (semantic embedding)
    ↓                                      ↓
    └──────────── Merge & Deduplicate ─────┘
                        ↓
              Gemini 3.5 Flash response
```

Both branches run in parallel. Results are deduplicated and ranked before Gemini generates the final natural-language response in the user's language.

---

## 🧠 Agent Memory Flow

```
First conversation:
  User: "I am Ramesh, farmer in Telangana, income 1.5 lakh"
  → Facts stored in agent_memory collection (MongoDB)

Later conversation (same user):
  User: "Suggest schemes"
  → Sarathi already knows: farmer · Telangana · income ₹1.5L
  → Never asks again ✓
```

Memory is loaded from MongoDB at the start of every session and injected into the Gemini system prompt. New facts learned during conversation are upserted back.

---

## 🤝 MongoDB MCP Integration

The MongoDB MCP server (`@mongodb-js/mongodb-mcp-server`) is launched as a subprocess via `npx` and communicates over **JSON-RPC 2.0 stdio transport**. `mcp_client.py` handles the full handshake and exposes `call_tool()` to the agent.

All MongoDB operations are routed through MCP first, with a direct `pymongo` fallback if the MCP process is unavailable.

```
sarathi_agent.py
     ↓ execute_tool()
mcp_client.py  (MongoMCPClient)
     ↓ JSON-RPC 2.0 over stdio
@mongodb-js/mongodb-mcp-server
     ↓
MongoDB Atlas
```

---

## 🛠️ MCP Tools

| Tool | Purpose |
|------|---------|
| `sarathi_save_submission` | Save encrypted scheme application via MCP `insertOne` |
| `sarathi_get_submission` | Retrieve submission by ref ID via MCP `find` |
| `sarathi_list_submissions` | List user's past applications via MCP `find` |
| `sarathi_check_eligibility` | Gemini reasons over user answers → eligible / not eligible |
| `sarathi_collect_form` | Ask one field at a time conversationally |

---

## 🎙️ Voice Features

- **Google Cloud STT** → Browser STT fallback (seamless, automatic)
- **Google Cloud TTS (WaveNet)** → Browser TTS fallback
- **9 Indian languages:** Telugu, Hindi, English, Tamil, Kannada, Malayalam, Odia, Punjabi, Marathi
- Real-time Web Audio API **waveform visualizer**
- **Tesseract.js OCR** — scan and auto-fill documents
- **Aadhaar masking** — captured as XXXX-XXXX-XXXX, never stored in full
- Voice gender toggle (male / female)
- iOS audio unlock + offline queuing (PWA with service worker)

---

## 🏛️ Eligibility Engine

Four-layer eligibility check for every citizen:

1. Build user profile text → generate Gemini embedding (768-dim)
2. MongoDB Atlas Vector Search → top 15 semantically matching schemes
3. Live `data.gov.in` API calls — PM-KISAN beneficiary stats, PMFBY crop insurance data
4. Gemini 3.5 Flash reasons over all data → ranked qualifying schemes with sources

---

## 🔐 Security

- **Fernet encryption** on all form submissions (AES-128-CBC + HMAC-SHA256)
- **SHA-256 integrity hash** per submission
- OTP login via SMS (Fast2SMS) and email (Gmail)
- Rate limiting per user (sliding window, MongoDB TTL)
- Session cookies: HttpOnly, SameSite=Lax, Secure in production
- Audit log for every action in the system
- CAPTCHA on registration

---

## 📡 API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Cloud Run health check |
| `/api/memory` | GET | Get agent memory for current user |
| `/api/memory` | POST | Update memory facts |
| `/api/memory/clear` | POST | Clear memory (privacy) |
| `/api/conversation_history` | GET | Paginated conversation history |
| `/api/hybrid_search` | POST | Hybrid Atlas text + vector search |
| `/api/eligibility_check` | POST | Full 4-layer eligibility engine |
| `/api/scheme_recommend` | POST | AI scheme recommendations |
| `/api/gemini_chat` | POST | Conversational scheme application |
| `/api/submit_form` | POST | Submit encrypted scheme application |

---

## 🐍 Python & Runtime

| Environment | Python | Async Mode |
|------------|--------|-----------|
| Localhost | 3.10–3.12 | gevent |
| Cloud Run (gunicorn) | 3.12 | gevent |

> **gevent is required.** `gevent.monkey.patch_all()` runs as the very first line of `server.py`. The Dockerfile uses `--worker-class gevent`.

---

## 📦 Google Cloud Services Used

| Service | Purpose |
|---------|---------|
| Cloud Run | Serverless container hosting |
| Cloud Build | Docker image builds |
| Artifact Registry | Docker image storage |
| Vertex AI | Gemini 3.5 Flash + text-embedding-005 |
| Cloud Speech-to-Text | Voice input in 9 Indian languages |
| Cloud Text-to-Speech | WaveNet voice output |
| Cloud Translation API | Real-time multilingual translation |
| Secret Manager | Secure runtime secret injection |
