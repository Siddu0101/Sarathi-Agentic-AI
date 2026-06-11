# ══════════════════════════════════════════════════════════════════════
# Sarathi v11 — Production Dockerfile for Google Cloud Run
# ══════════════════════════════════════════════════════════════════════
# Stack:
#   • Python 3.12-slim  — Flask + SocketIO + Vertex AI SDK
#   • Node.js 20 LTS    — @mongodb-js/mongodb-mcp-server (via npx)
#   • Gunicorn + Gevent — async worker for SocketIO on Cloud Run
# ══════════════════════════════════════════════════════════════════════

FROM python:3.12-slim

WORKDIR /app

# ── System packages ────────────────────────────────────────────────────
# Node.js 20 LTS includes npx by default; manually reinstalling it causes build collisions.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    curl \
    gnupg \
    tesseract-ocr \
    tesseract-ocr-tel \
    tesseract-ocr-hin \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# ── Python dependencies ────────────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# ── App code ──────────────────────────────────────────────────────────
COPY . .

# ── Pre-install MCP server (faster cold starts — avoids npx download) ─
RUN npx --yes @mongodb-js/mongodb-mcp-server --version || true

# ── Runtime environment ────────────────────────────────────────────────
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    DB_BACKEND=mongodb \
    FLASK_ENV=production \
    # Gevent monkey-patching needs this for correct SSL on Cloud Run
    GEVENT_RESOLVER=ares

# ── Non-root user (Cloud Run security best practice) ──────────────────
RUN adduser --disabled-password --gecos '' sarathi \
    && chown -R sarathi:sarathi /app \
    # Allow npx cache to be written by the sarathi user
    && mkdir -p /home/sarathi/.npm \
    && chown -R sarathi:sarathi /home/sarathi/.npm
USER sarathi

# ── Health check endpoint ──────────────────────────────────────────────
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD curl -sf http://localhost:${PORT}/health || exit 1

# ── Expose Cloud Run port ──────────────────────────────────────────────
EXPOSE 8080

# ── Start command ──────────────────────────────────────────────────────
# gevent worker: handles Flask-SocketIO WebSockets correctly on Cloud Run
# 1 worker: Cloud Run scales horizontally (new containers), not vertically
# --timeout 120: allows slow Vertex AI calls to complete
# --keep-alive 75: must be > Cloud Run's 60s idle timeout
CMD ["sh", "-c", \
     "gunicorn \
      --worker-class gevent \
      --workers 1 \
      --bind 0.0.0.0:${PORT} \
      --timeout 120 \
      --keep-alive 75 \
      --log-level info \
      --access-logfile - \
      --error-logfile - \
      server:app"]