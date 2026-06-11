#!/bin/bash
# ══════════════════════════════════════════════════════════════════════
# deploy.sh — One-command Cloud Run deployment for Sarathi v10
# ══════════════════════════════════════════════════════════════════════

set -e

# ── Config ─────────────────────────────────────────────────────────────
PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${GOOGLE_CLOUD_LOCATION:-asia-south1}"
SERVICE_NAME="sarathi-v10"
IMAGE="asia-south1-docker.pkg.dev/${PROJECT_ID}/sarathi-repo/${SERVICE_NAME}"

MIN_INSTANCES=1
MAX_INSTANCES=10
MEMORY="2Gi"
CPU="2"
CONCURRENCY=80

# ── Validate ───────────────────────────────────────────────────────────
if [ -z "$PROJECT_ID" ]; then
  echo "❌  PROJECT_ID is not set."
  exit 1
fi

echo ""
echo "🚀 Sarathi v10 — Cloud Run Deployment"
echo "   Project  : $PROJECT_ID"
echo "   Region   : $REGION"
echo "   Image    : $IMAGE"
echo ""

# ── Enable required APIs ───────────────────────────────────────────────
echo "📡 Enabling required Google Cloud APIs..."
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  aiplatform.googleapis.com \
  speech.googleapis.com \
  texttospeech.googleapis.com \
  translate.googleapis.com \
  secretmanager.googleapis.com \
  --project="$PROJECT_ID" \
  --quiet

# ── Auto-Create Repository (THE FIX) ───────────────────────────────────
echo ""
echo "📁 Checking Artifact Registry..."
if ! gcloud artifacts repositories describe sarathi-repo --location="$REGION" --project="$PROJECT_ID" >/dev/null 2>&1; then
  echo "   Repository not found. Creating 'sarathi-repo' now..."
  gcloud artifacts repositories create sarathi-repo \
    --repository-format=docker \
    --location="$REGION" \
    --description="Docker repository for Sarathi backend" \
    --project="$PROJECT_ID"
else
  echo "   Repository 'sarathi-repo' is ready!"
fi

# ── Build Docker image using Cloud Build ──────────────────────────────
echo ""
echo "🔨 Building Docker image with Cloud Build..."
gcloud builds submit \
  --tag "$IMAGE" \
  --project="$PROJECT_ID" \
  --machine-type=E2_HIGHCPU_8 \
  .

# ── Read secrets from .env for Cloud Run env vars ─────────────────────
echo ""
echo "🔐 Reading secrets from .env..."

read_env() { grep "^$1=" .env 2>/dev/null | cut -d= -f2- | tr -d "'" | tr -d '"' | tr -d '\r'; }

SARATHI_SECRET=$(read_env SARATHI_SECRET)
ADMIN_PASSWORD=$(read_env ADMIN_PASSWORD)
MONGODB_URI=$(read_env MONGODB_URI)
GEMINI_API_KEY=$(read_env GEMINI_API_KEY)
FAST2SMS_API_KEY=$(read_env FAST2SMS_API_KEY)
FAST2SMS_OTP_ID=$(read_env FAST2SMS_OTP_ID)
MAIL_EMAIL=$(read_env MAIL_EMAIL)
MAIL_APP_PASSWORD=$(read_env MAIL_APP_PASSWORD)
DATA_GOV_API_KEY=$(read_env DATA_GOV_API_KEY)

# ── Deploy to Cloud Run ────────────────────────────────────────────────
echo ""
echo "☁️  Deploying to Cloud Run..."

gcloud run deploy "$SERVICE_NAME" \
  --image="$IMAGE" \
  --platform=managed \
  --region="$REGION" \
  --project="$PROJECT_ID" \
  --allow-unauthenticated \
  --port=8080 \
  --memory="$MEMORY" \
  --cpu="$CPU" \
  --concurrency="$CONCURRENCY" \
  --min-instances="$MIN_INSTANCES" \
  --max-instances="$MAX_INSTANCES" \
  --timeout=300 \
  --set-env-vars="FLASK_ENV=production" \
  --set-env-vars="DB_BACKEND=mongodb" \
  --set-env-vars="USE_GEMINI=true" \
  --set-env-vars="USE_GOOGLE_STT=true" \
  --set-env-vars="USE_GOOGLE_TTS=true" \
  --set-env-vars="USE_TRANSLATE=true" \
  --set-env-vars="GOOGLE_CLOUD_PROJECT=${PROJECT_ID}" \
  --set-env-vars="GOOGLE_CLOUD_LOCATION=${REGION}" \
  --set-env-vars="SARATHI_SECRET=${SARATHI_SECRET}" \
  --set-env-vars="ADMIN_PASSWORD=${ADMIN_PASSWORD}" \
  --set-env-vars="MONGODB_URI=${MONGODB_URI}" \
  --set-env-vars="MONGODB_DB=sarathi_db" \
  --set-env-vars="GEMINI_API_KEY=${GEMINI_API_KEY}" \
  --set-env-vars="FAST2SMS_API_KEY=${FAST2SMS_API_KEY}" \
  --set-env-vars="FAST2SMS_OTP_ID=${FAST2SMS_OTP_ID}" \
  --set-env-vars="MAIL_EMAIL=${MAIL_EMAIL}" \
  --set-env-vars="MAIL_APP_PASSWORD=${MAIL_APP_PASSWORD}" \
  --set-env-vars="DATA_GOV_API_KEY=${DATA_GOV_API_KEY}"

# ── Get service URL ────────────────────────────────────────────────────
SERVICE_URL=$(gcloud run services describe "$SERVICE_NAME" \
  --platform=managed \
  --region="$REGION" \
  --project="$PROJECT_ID" \
  --format='value(status.url)' 2>/dev/null)

echo ""
echo "════════════════════════════════════════════════════════"
echo "✅  Sarathi v10 deployed successfully!"
echo "   🌐 URL     : ${SERVICE_URL}"
echo "════════════════════════════════════════════════════════"