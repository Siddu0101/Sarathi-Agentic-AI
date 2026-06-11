"""
sms_service.py — Fast2SMS + Gmail Email OTP for Sarathi AI v5.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

FAST2SMS API CHANGES (v5 — fixes authentication failures):
  ❌ OLD: GET /dev/bulkV2?route=otp&authorization=KEY  (auth in query param for GET)
  ✅ NEW: POST /dev/otp/send   — dedicated OTP endpoint (auth in header)
  ✅ NEW: POST /dev/bulkV2    — Quick SMS (auth in header, JSON body)

  Fast2SMS authorization rules:
    GET  requests  → authorization in QUERY PARAMS (?authorization=KEY)
    POST requests  → authorization in HEADER       (authorization: KEY)
  The old code used GET + header = 401 every time.

Required .env keys:
  FAST2SMS_API_KEY   — from https://www.fast2sms.com/dashboard/dev-api
  FAST2SMS_OTP_ID    — from Fast2SMS Dashboard → Dev API → OTP Templates
                       Leave blank to auto-fallback to Quick SMS route (₹5/SMS)

Optional .env keys:
  MAIL_EMAIL         — Gmail address for email OTP fallback
  MAIL_APP_PASSWORD  — Gmail App Password (not main password)

⚠️  IP WHITELIST NOTE:
  If your Fast2SMS account has IP Security enabled (Dashboard → Dev API → Security),
  add your server's public IP to the whitelist, or disable IP restriction for testing.
  This is the #1 cause of "Invalid Authentication" even with a valid key.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import requests as _requests

# ── env helpers ───────────────────────────────────────────────────────────────
_FAST2SMS_KEY    = lambda: os.environ.get("FAST2SMS_API_KEY", "").strip()
_FAST2SMS_OTP_ID = lambda: os.environ.get("FAST2SMS_OTP_ID", "").strip()
_MAIL_EMAIL      = lambda: os.environ.get("MAIL_EMAIL", "").strip()
_MAIL_PASSWORD   = lambda: os.environ.get("MAIL_APP_PASSWORD", "").strip()

# ── Fast2SMS API endpoints (POST only — auth in header) ───────────────────────
F2S_OTP_URL   = "https://www.fast2sms.com/dev/otp/send"   # Dedicated OTP route
F2S_QUICK_URL = "https://www.fast2sms.com/dev/bulkV2"     # Quick SMS / custom text


def _f2s_headers() -> dict:
    """Auth header for all Fast2SMS POST requests."""
    return {
        "authorization": _FAST2SMS_KEY(),
        "Content-Type":  "application/json",
        "cache-control": "no-cache",
    }


def _normalize_mobile(mobile: str) -> str | None:
    """Strip country code, return 10-digit number or None if invalid."""
    m = str(mobile).strip().lstrip("+")
    if m.startswith("91") and len(m) == 12:
        m = m[2:]
    m = m[-10:]
    if len(m) == 10 and m.isdigit():
        return m
    return None


# ══════════════════════════════════════════════════════════════════════════════
# CHANNEL 1 — Fast2SMS
# ══════════════════════════════════════════════════════════════════════════════

def _fast2sms_otp(mobile: str, otp: str) -> bool:
    """
    Send OTP via Fast2SMS.
    Tries the dedicated OTP endpoint first (requires FAST2SMS_OTP_ID in .env).
    Falls back to Quick SMS route if OTP_ID is not configured.
    All requests use POST with JSON body and authorization in header.
    """
    api_key = _FAST2SMS_KEY()
    if not api_key:
        print("[SMS] Fast2SMS skipped — FAST2SMS_API_KEY not set in .env")
        return False

    mobile = _normalize_mobile(mobile)
    if not mobile:
        print(f"[SMS] Fast2SMS skipped — invalid mobile number")
        return False

    otp_id = _FAST2SMS_OTP_ID()

    # ── Route A: Dedicated OTP endpoint (cheapest, requires template ID) ──────
    if otp_id:
        try:
            resp = _requests.post(
                F2S_OTP_URL,
                json={
                    "mobile":      mobile,
                    "otp_id":      otp_id,
                    "otp":         str(otp),
                    "otp_expiry":  10,   # 10 minutes
                    "otp_length":  6,
                },
                headers=_f2s_headers(),
                timeout=15,
            )
            data = {}
            try:
                data = resp.json()
            except Exception:
                pass

            if data.get("return") is True:
                print(f"[SMS] Fast2SMS OTP sent to xxxxxx{mobile[-4:]}")
                return True

            err = data.get("message", resp.text[:200])
            print(f"[SMS] Fast2SMS OTP endpoint failed (status {resp.status_code}): {err}")

            # If it's an auth error, surface the IP whitelist tip and stop
            if resp.status_code == 401 or "auth" in str(err).lower() or "invalid" in str(err).lower():
                print("[SMS] ⚠️  Auth failure — check: (1) correct API key in .env, "
                      "(2) IP whitelist in Fast2SMS Dashboard → Dev API → Security tab")
                return False

        except _requests.exceptions.Timeout:
            print("[SMS] Fast2SMS OTP endpoint timed out")
        except Exception as e:
            print(f"[SMS] Fast2SMS OTP endpoint error: {e}")

    else:
        print("[SMS] FAST2SMS_OTP_ID not set — skipping dedicated OTP route, "
              "falling back to Quick SMS")

    # ── Route B: Quick SMS fallback (no template needed, ₹5/SMS) ─────────────
    return _fast2sms_quick(
        mobile,
        f"Your Sarathi login OTP is {otp}. Valid 10 min. Do NOT share. -Sarathi AI",
    )


def _fast2sms_quick(mobile: str, message: str) -> bool:
    """
    Send a custom text message via Fast2SMS Quick SMS (POST, no DLT needed).
    Costs ₹5/SMS. Works on all Indian numbers including DND.
    """
    api_key = _FAST2SMS_KEY()
    if not api_key:
        return False

    mobile = _normalize_mobile(mobile) or mobile  # use raw if normalize fails
    if not mobile:
        return False

    try:
        resp = _requests.post(
            F2S_QUICK_URL,
            json={
                "message":  message,
                "route":    "q",
                "numbers":  mobile,
            },
            headers=_f2s_headers(),
            timeout=15,
        )
        data = {}
        try:
            data = resp.json()
        except Exception:
            pass

        if data.get("return") is True:
            print(f"[SMS] Fast2SMS Quick SMS sent to xxxxxx{mobile[-4:]}")
            return True

        err = data.get("message", resp.text[:200])
        print(f"[SMS] Fast2SMS Quick SMS failed (status {resp.status_code}): {err}")

        if resp.status_code == 401 or "auth" in str(err).lower() or "invalid" in str(err).lower():
            print("[SMS] ⚠️  Auth failure — check: (1) correct API key in .env, "
                  "(2) IP whitelist in Fast2SMS Dashboard → Dev API → Security tab")
        return False

    except _requests.exceptions.Timeout:
        print("[SMS] Fast2SMS Quick SMS timed out")
        return False
    except Exception as e:
        print(f"[SMS] Fast2SMS Quick SMS error: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# CHANNEL 2 — Gmail SMTP
# ══════════════════════════════════════════════════════════════════════════════

def _gmail_otp(email: str, otp: str) -> bool:
    """Send OTP via Gmail SMTP (SSL port 465). Free for ~500 emails/day."""
    sender   = _MAIL_EMAIL()
    password = _MAIL_PASSWORD()

    if not sender or not password:
        print("[Email] Gmail skipped — MAIL_EMAIL or MAIL_APP_PASSWORD not set")
        return False
    if not email:
        print("[Email] Gmail OTP skipped — no email address provided")
        return False

    try:
        msg            = MIMEMultipart("alternative")
        msg["From"]    = f"Sarathi AI <{sender}>"
        msg["To"]      = email
        msg["Subject"] = f"Your Sarathi Login OTP: {otp}"

        plain = (
            f"Your Sarathi login OTP is: {otp}\n\n"
            f"Valid for 10 minutes. Do not share with anyone.\n\n"
            f"— Sarathi AI | Government Citizen Services Portal"
        )

        html = f"""
<div style="font-family:Arial,sans-serif;max-width:480px;margin:0 auto;
            padding:24px;border:1px solid #dce3f0;border-radius:12px;">
  <div style="background:#1a237e;padding:16px;border-radius:8px;
              text-align:center;margin-bottom:20px;">
    <h2 style="color:#fff;margin:0;font-size:20px;">&#127470;&#127475; Project Sarathi</h2>
    <p style="color:rgba(255,255,255,0.8);margin:4px 0 0;font-size:12px;">
      Government Citizen Services Portal
    </p>
  </div>
  <p style="color:#333;font-size:15px;margin-bottom:8px;">
    Your one-time login OTP is:
  </p>
  <div style="background:#f0f3fa;border:2px dashed #1a237e;border-radius:8px;
              padding:20px;text-align:center;margin:16px 0;">
    <span style="font-size:40px;font-weight:700;letter-spacing:12px;color:#1a237e;">
      {otp}
    </span>
  </div>
  <p style="color:#666;font-size:13px;">
    &#9203; Valid for <strong>10 minutes</strong>.
    Do not share this OTP with anyone.<br>
    This OTP has also been sent to your registered mobile number via SMS.
  </p>
  <hr style="border:none;border-top:1px solid #eee;margin:16px 0;">
  <p style="color:#aaa;font-size:11px;text-align:center;">
    &copy; 2026 Sarathi AI &nbsp;&middot;&nbsp;
    Ministry of Electronics &amp; IT, Govt of India
  </p>
</div>
"""
        msg.attach(MIMEText(plain, "plain"))
        msg.attach(MIMEText(html,  "html"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(sender, password)
            smtp.sendmail(sender, email, msg.as_string())

        masked = f"{email[:3]}***@{email.split('@')[-1]}"
        print(f"[Email] Gmail OTP sent to {masked}")
        return True

    except smtplib.SMTPAuthenticationError:
        print("[Email] Gmail OTP failed — use an App Password, not your main Gmail password. "
              "Enable 2FA then go to: Google Account → Security → App Passwords")
        return False
    except Exception as e:
        print(f"[Email] Gmail OTP failed: {e}")
        return False


def _gmail_submission(email: str, ref_id: str, scheme_name: str) -> bool:
    """Send a styled submission confirmation email."""
    sender   = _MAIL_EMAIL()
    password = _MAIL_PASSWORD()

    if not sender or not password or not email:
        return False

    try:
        msg            = MIMEMultipart("alternative")
        msg["From"]    = f"Sarathi AI <{sender}>"
        msg["To"]      = email
        msg["Subject"] = f"Sarathi: {scheme_name} Application Submitted — Ref {ref_id}"

        html = f"""
<div style="font-family:Arial,sans-serif;max-width:480px;margin:0 auto;
            padding:24px;border:1px solid #dce3f0;border-radius:12px;">
  <div style="background:#1a237e;padding:16px;border-radius:8px;
              text-align:center;margin-bottom:20px;">
    <h2 style="color:#fff;margin:0;font-size:20px;">&#127470;&#127475; Project Sarathi</h2>
  </div>
  <h3 style="color:#2e7d32;">&#9989; Application Submitted Successfully</h3>
  <p style="color:#333;">
    Your <strong>{scheme_name}</strong> application has been submitted.
  </p>
  <div style="background:#f0f3fa;border-left:4px solid #1a237e;
              border-radius:6px;padding:16px;margin:16px 0;">
    <p style="margin:0;font-size:12px;color:#666;text-transform:uppercase;
              letter-spacing:1px;">Reference ID</p>
    <p style="margin:6px 0 0;font-size:22px;font-weight:700;
              color:#1a237e;letter-spacing:2px;">{ref_id}</p>
  </div>
  <p style="color:#666;font-size:13px;">
    Please save this Reference ID for tracking your application status.
  </p>
  <hr style="border:none;border-top:1px solid #eee;margin:16px 0;">
  <p style="color:#aaa;font-size:11px;text-align:center;">
    &copy; 2026 Sarathi AI &nbsp;&middot;&nbsp;
    Ministry of Electronics &amp; IT, Govt of India
  </p>
</div>
"""
        msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(sender, password)
            smtp.sendmail(sender, email, msg.as_string())

        print(f"[Email] Submission confirmation sent to {email[:3]}***")
        return True

    except Exception as e:
        print(f"[Email] Submission confirmation failed: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC API — called by server.py
# ══════════════════════════════════════════════════════════════════════════════

def send_otp_sms(mobile: str, otp: str, email: str = None) -> str:
    """
    Send login OTP via Fast2SMS (SMS) and optionally Gmail (email).
    Both channels are attempted for maximum delivery reliability.

    Returns:
        "both"  — delivered via SMS + email
        "sms"   — delivered via Fast2SMS only
        "email" — delivered via Gmail only
        ""      — both failed (server shows DEMO MODE flash)
    """
    sms_sent   = _fast2sms_otp(mobile, otp)
    email_sent = _gmail_otp(email, otp) if email else False

    if sms_sent and email_sent:
        return "both"
    if sms_sent:
        return "sms"
    if email_sent:
        return "email"
    return ""


def send_submission_sms(mobile: str, ref_id: str, scheme_name: str,
                        lang: str = "en-IN", email: str = None) -> bool:
    """
    Send submission confirmation. Tries Fast2SMS first, falls back to Gmail.
    Returns True if delivered by any channel.
    """
    msg = (f"Sarathi: Your {scheme_name} application submitted! "
           f"Ref ID: {ref_id}. Save this for tracking. -Sarathi AI")

    sms_ok   = _fast2sms_quick(mobile, msg)
    email_ok = _gmail_submission(email, ref_id, scheme_name) if email else False
    return sms_ok or email_ok
