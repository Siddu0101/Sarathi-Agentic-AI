#!/usr/bin/env python3
"""
run_local.py — One-command localhost starter for Project Sarathi v8.1
─────────────────────────────────────────────────────────────────────
Usage:
    python run_local.py

What it does:
  1. Checks .env exists and has the required keys
  2. Checks all required pip packages are installed
  3. Prints a quick checklist
  4. Starts the Flask-SocketIO server on http://localhost:5000
─────────────────────────────────────────────────────────────────────
"""
import sys, os, subprocess

# ── Minimum Python version ────────────────────────────────────────────
if sys.version_info < (3, 10):
    print("❌  Python 3.10+ required. You have", sys.version)
    sys.exit(1)

# ── Check .env ────────────────────────────────────────────────────────
if not os.path.exists(".env"):
    print("❌  .env file not found!")
    print("   Copy .env.example to .env and fill in your values:")
    print("   cp .env.example .env")
    sys.exit(1)

from dotenv import load_dotenv
load_dotenv()

REQUIRED = {
    "MONGODB_URI":    "MongoDB Atlas connection string",
    "GEMINI_API_KEY": "Gemini API key (get from aistudio.google.com)",
    "SARATHI_SECRET": "Random secret key (any long string)",
}

missing = []
for key, desc in REQUIRED.items():
    val = os.environ.get(key, "").strip()
    if not val or val.startswith("your_") or val.startswith("change_me"):
        missing.append(f"   {key:<20}  ← {desc}")

if missing:
    print("❌  Missing or placeholder values in .env:")
    for m in missing:
        print(m)
    sys.exit(1)

# ── Check pip packages ────────────────────────────────────────────────
REQUIRED_PKGS = [
    ("flask",           "flask"),
    ("flask_socketio",  "flask-socketio"),
    ("flask_cors",      "flask-cors"),
    ("pymongo",         "pymongo[srv]"),
    ("dotenv",          "python-dotenv"),
    ("cryptography",    "cryptography"),
    ("google.genai",    "google-genai"),
    ("google.cloud.aiplatform", "google-cloud-aiplatform"),
    ("werkzeug",        "werkzeug"),
    ("captcha",         "captcha"),
    ("qrcode",          "qrcode[pil]"),
    ("PIL",             "Pillow"),
    ("reportlab",       "reportlab"),
    ("requests",        "requests"),
]

not_installed = []
for module, pkg in REQUIRED_PKGS:
    try:
        __import__(module)
    except ImportError:
        not_installed.append(pkg)

if not_installed:
    print("⚠️  Missing packages. Installing...")
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet"] + not_installed,
        check=True
    )
    print("✅  Packages installed.")

# ── Optional packages (warn but don't block) ──────────────────────────
OPTIONAL_PKGS = [
    ("eventlet", "eventlet", "faster WebSocket support on Python < 3.13"),
    ("gevent",   "gevent",   "needed for gunicorn production mode"),
]
for module, pkg, reason in OPTIONAL_PKGS:
    try:
        __import__(module)
    except ImportError:
        print(f"ℹ️   Optional: pip install {pkg}  ({reason})")

# ── Print checklist ───────────────────────────────────────────────────
print()
print("═" * 55)
print("  Project Sarathi v8.1 — Localhost Startup")
print("═" * 55)
print(f"  Python      : {sys.version.split()[0]}")
print(f"  MongoDB     : {os.environ.get('MONGODB_URI','')[:40]}...")
print(f"  Gemini      : {'✅ configured' if os.environ.get('GEMINI_API_KEY') else '❌ missing'}")
print(f"  SMS (OTP)   : {'✅ configured' if os.environ.get('FAST2SMS_API_KEY') else 'ℹ️  not set (email OTP will be used)'}")
print(f"  Email (OTP) : {'✅ configured' if os.environ.get('MAIL_EMAIL') else 'ℹ️  not set'}")
print()
print("  ⚡ Starting server at: http://127.0.0.1:5000/login")
print("  ⚡ Admin panel at   : http://127.0.0.1:5000/admin")
print("  ⚡ Health check     : http://127.0.0.1:5000/health")
print("═" * 55)
print()

# ── Launch ────────────────────────────────────────────────────────────
# Import and run server.py as __main__ doesn't work cleanly due to SocketIO
# Instead, exec server.py directly so __name__ == "__main__" triggers
os.execv(sys.executable, [sys.executable, "server.py"])
