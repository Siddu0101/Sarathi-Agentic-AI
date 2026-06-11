"""
Generate all 9 scheme HTML pages from a single template
Each scheme gets: voice AI (11 langs), Gemini brain, GCP STT/TTS,
eligibility check, progress bar, waveform, QR code on success.
"""

SCHEME_STYLES = """
    *, *::before, *::after { margin:0; padding:0; box-sizing:border-box; }
    body { background:#f0f2f5; font-family:'Segoe UI',Roboto,-apple-system,sans-serif; color:#1a1a2e; }
    .tricolor { height:5px; background:linear-gradient(90deg,#ff9933 33.33%,#fff 33.33% 66.66%,#138808 66.66%); }
    .top-nav { background:white; padding:12px 5%; display:flex; justify-content:space-between; align-items:center;
               box-shadow:0 2px 10px rgba(0,0,0,.06); position:sticky; top:0; z-index:50; }
    .top-nav a { text-decoration:none; color:var(--color); font-weight:700; font-size:14px; }
    .top-nav-right { display:flex; align-items:center; gap:12px; font-size:12px; color:#6b7280; }
    .outer { max-width:860px; margin:28px auto 60px; padding:0 16px; }
    .scheme-card { background:white; border-radius:14px; border-top:6px solid var(--color);
                   box-shadow:0 4px 24px rgba(0,0,0,.09); overflow:hidden; }
    .scheme-header { background:linear-gradient(135deg,var(--color),var(--color-light)); color:white; padding:24px 28px; }
    .scheme-header h2 { font-size:22px; font-weight:800; margin-bottom:4px; }
    .scheme-header p  { opacity:.88; font-size:14px; }
    .scheme-header .ministry { font-size:12px; opacity:.7; margin-top:6px; }
    .lang-row { display:flex; flex-wrap:wrap; gap:6px; padding:14px 28px; background:#f8f9ff;
                border-bottom:1px solid #eef0f8; align-items:center; }
    .lang-row .lang-label { font-size:12px; font-weight:600; color:#6b7280; margin-right:4px; }
    .lang-pill { display:inline-block; background:white; border:1.5px solid #e0e0e0;
                 border-radius:20px; padding:4px 12px; font-size:12px; font-weight:700; cursor:pointer; transition:.2s; }
    .lang-pill:hover, .lang-pill.active { background:var(--color); color:white; border-color:var(--color); }
    .form-body { padding:24px 28px; }
    .elig-banner { display:none; padding:14px 18px; border-radius:10px; font-size:14px; line-height:1.6; margin-bottom:16px; }
    .elig-banner.eligible { background:#e8f5e9; border:1.5px solid #66bb6a; color:#1b5e20; }
    .elig-banner.not-eligible { background:#fff3e0; border:1.5px solid #ffa726; color:#bf360c; }
    #progress-bar { display:none; margin-bottom:14px; }
    .progress-track { height:6px; background:#e0e0e0; border-radius:6px; overflow:hidden; margin-bottom:6px; }
    .progress-fill { height:100%; background:linear-gradient(90deg,var(--color),var(--color-light));
                     border-radius:6px; transition:width .4s ease; width:0%; }
    #progress-text { font-size:12px; color:#6b7280; font-weight:600; }
    #success-banner { display:none; background:#e8f5e9; border:2px solid #43a047; color:#1b5e20;
                      padding:14px 18px; border-radius:10px; font-weight:600; font-size:15px;
                      margin-bottom:16px; text-align:center; }
    .mic-btn { background:linear-gradient(135deg,var(--color),var(--color-light)); color:white; border:none;
               padding:18px; width:100%; font-size:17px; font-weight:800; border-radius:12px; cursor:pointer;
               margin-bottom:12px; display:flex; align-items:center; justify-content:center; gap:10px;
               transition:.25s; min-height:72px; letter-spacing:.3px; }
    .mic-btn:hover   { opacity:.92; transform:translateY(-2px); box-shadow:0 6px 20px rgba(0,0,0,.18); }
    .mic-listening   { background:linear-gradient(135deg,#c62828,#b71c1c) !important; animation:pulse 1.5s infinite; }
    .mic-processing  { background:linear-gradient(135deg,#f57c00,#e65100) !important; }
    .mic-speaking    { background:linear-gradient(135deg,#1565c0,#0d47a1) !important; }
    @keyframes pulse { 0%{box-shadow:0 0 0 0 rgba(198,40,40,.65)} 70%{box-shadow:0 0 0 14px rgba(198,40,40,0)} 100%{box-shadow:0 0 0 0 rgba(198,40,40,0)} }
    #waveform { display:none; justify-content:center; align-items:flex-end; gap:4px; height:32px; margin-bottom:10px; }
    .wave-bar { width:5px; background:var(--color); border-radius:4px; animation:wave-anim 1.2s ease-in-out infinite; }
    .wave-bar:nth-child(2){animation-delay:.15s} .wave-bar:nth-child(3){animation-delay:.30s}
    .wave-bar:nth-child(4){animation-delay:.45s} .wave-bar:nth-child(5){animation-delay:.60s}
    @keyframes wave-anim { 0%,100%{height:8px;opacity:.4} 50%{height:28px;opacity:1} }
    .live-text-box { padding:12px 16px; margin-bottom:16px; border-radius:10px; font-size:14px;
                     line-height:1.7; display:none; border-left:4px solid #ccc; }
    .live-text-box.info    { background:#e3f2fd; border-color:#1976d2; color:#0d47a1; }
    .live-text-box.speaking{ background:#fff8e1; border-color:#f9a825; color:#5d4037; }
    .live-text-box.success { background:#e8f5e9; border-color:#43a047; color:#1b5e20; }
    .live-text-box.error   { background:#fce4ec; border-color:#c62828; color:#b71c1c; }
    .live-text-box.warn    { background:#fff3e0; border-color:#ef6c00; color:#bf360c; }
    .field-group { margin-bottom:18px; }
    label { display:block; font-weight:700; color:#374151; margin-bottom:7px; font-size:14px; }
    input[type=text],input[type=number],input[type=email],select {
      width:100%; padding:13px 16px; border:1.5px solid #d1d5db; border-radius:9px;
      font-size:15px; color:#1f2937; background:#f9fafb; transition:.2s; -webkit-appearance:none; }
    input:focus,select:focus { outline:none; border-color:var(--color); background:white;
                               box-shadow:0 0 0 3px rgba(0,0,0,.06); }
    .submit-btn { background:var(--color); color:white; padding:14px; border:none; border-radius:10px;
                  font-weight:800; cursor:pointer; font-size:15px; width:100%; margin-top:8px;
                  transition:.25s; min-height:52px; }
    .submit-btn:hover { opacity:.9; transform:translateY(-1px); }
    #qr-container { display:none; margin-top:16px; }
    .qr-card { background:linear-gradient(135deg,#f0f4ff,#e8f5e9); border:1.5px solid #c8e6c9;
               border-radius:12px; padding:20px; text-align:center; }
    .qr-card h4 { font-size:14px; color:#1a237e; margin-bottom:12px; }
    .qr-img { width:160px; height:160px; border-radius:8px; border:2px solid white; box-shadow:0 4px 12px rgba(0,0,0,.12); }
    .qr-ref { font-size:15px; font-weight:800; color:#1a237e; margin-top:12px; letter-spacing:1px; }
    .qr-note { font-size:12px; color:#6b7280; margin-top:4px; }
    .security-note { text-align:center; margin-top:16px; font-size:12px; color:#9ca3af; }
    .security-note strong { color:#2e7d32; }
    @media(max-width:600px) {
      .form-body,.scheme-header,.lang-row { padding-left:18px; padding-right:18px; }
      .outer { margin-top:16px; }
      .mic-btn { font-size:15px; }
    }
"""

LANG_PILLS = """
    <div class="lang-row">
      <span class="lang-label">🌐 Speak in:</span>
      <span class="lang-pill">తెలుగు</span>
      <span class="lang-pill">हिन्दी</span>
      <span class="lang-pill">English</span>
      <span class="lang-pill">தமிழ்</span>
      <span class="lang-pill">ಕನ್ನಡ</span>
      <span class="lang-pill">বাংলা</span>
      <span class="lang-pill">मराठी</span>
      <span class="lang-pill">ગુજ.</span>
      <span class="lang-pill">ਪੰਜ.</span>
    </div>
"""

VOICE_SECTION = """
    <div id="eligibility-banner" class="elig-banner"></div>

    <div id="progress-bar">
      <div class="progress-track"><div class="progress-fill"></div></div>
      <div id="progress-text"></div>
    </div>

    <div id="success-banner"></div>

    <button id="micBtn" class="mic-btn" onclick="startSpeech()">
      🎙️ Tap to Speak — AI Guides You Step by Step
    </button>

    <div id="waveform">
      <div class="wave-bar"></div><div class="wave-bar"></div><div class="wave-bar"></div>
      <div class="wave-bar"></div><div class="wave-bar"></div>
    </div>

    <div id="live-text" class="live-text-box"></div>
"""

QR_AND_SECURITY = """
    <div id="qr-container"></div>
    <div class="security-note">
      🔒 <strong>AES-128 Encrypted</strong> · HMAC-SHA256 Integrity · Government IT Act 2000 Compliant
    </div>
"""

SCRIPT_FOOTER = """
<script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.5/socket.io.min.js"></script>
<script>
window.SARATHI_USE_GCP_STT = {{ use_gcp_stt | tojson }};
window.SARATHI_USE_GCP_TTS = {{ use_gcp_tts | tojson }};
window.SARATHI_USE_GEMINI  = {{ use_gemini  | tojson }};
window.SARATHI_CHECK_ELIGIBILITY = true;
</script>
"""


def build_scheme_page(service_id, title, subtitle, emoji, ministry, color, color_light, fields_js, form_fields_html):
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
  <meta name="theme-color" content="{color}">
  <title>{emoji} {title} | Sarathi AI</title>
  <link rel="manifest" href="/manifest.json">
  <link rel="stylesheet" href="/static/mobile.css">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
  <meta name="format-detection" content="telephone=no">
  <style>
    :root {{ --color:{color}; --color-light:{color_light}; }}
{SCHEME_STYLES}
  </style>
</head>
<body>
<div id="ios-banner" style="display:none;background:#fff3cd;border-bottom:2px solid #ffc107;
  padding:10px 16px;text-align:center;font-size:13px;font-weight:600;color:#664d03;">
  📱 iPhone/iPad: Please use <strong>Chrome</strong> for voice input — Safari is not supported.
  <button onclick="this.parentElement.style.display='none'"
    style="margin-left:8px;background:none;border:none;font-size:16px;cursor:pointer;color:#664d03;">✕</button>
</div>
<script>
if(/iPad|iPhone|iPod/.test(navigator.userAgent)&&!/chrome/i.test(navigator.userAgent))
  document.getElementById("ios-banner").style.display="block";
</script>
<div class="tricolor"></div>
<div class="top-nav">
  <a href="/">⬅ Dashboard</a>
  <div class="top-nav-right">
    <span>👤 {{{{ user_name }}}}</span>
    <span>🔒 Encrypted</span>
  </div>
</div>

<div class="outer">
  <div class="scheme-card">

    <div class="scheme-header">
      <h2>{emoji} {title}</h2>
      <p>{subtitle}</p>
      <div class="ministry">{ministry}</div>
    </div>

{LANG_PILLS}

    <div class="form-body">
{VOICE_SECTION}

      <form id="govt-form" onsubmit="event.preventDefault();">
{form_fields_html}
        <button type="button" class="submit-btn" onclick="startSpeech()">
          🎙️ Use Voice to Fill & Submit
        </button>
      </form>

{QR_AND_SECURITY}
    </div>
  </div>
</div>

{SCRIPT_FOOTER}
<script>
window.SARATHI_SERVICE_ID = "{service_id}";
window.SARATHI_FIELDS = {fields_js};
</script>
<script src="/static/sarathi_voice.js"></script>
</body>
</html>
"""


# ── Scheme definitions ─────────────────────────────────────────────────────

SCHEMES = [
    {
        "service_id": "pm_kisan",
        "filename":   "pm_kisan.html",
        "title":      "PM Kisan Samman Nidhi",
        "subtitle":   "₹6,000 per year income support for all farmer families",
        "emoji":      "🚜",
        "ministry":   "Ministry of Agriculture & Farmers Welfare — Government of India",
        "color":      "#e65100",
        "color_light":"#ff8f00",
        "form_fields": [
            ("name",     "Farmer Name (రైతు పేరు / किसान का नाम)", "text",   False, False, True),
            ("aadhaar",  "Aadhaar Number (ఆధార్ / आधार)",           "text",   True,  False, False),
            ("state",    "State (రాష్ట్రం / राज्य)",                 "text",   False, False, False),
            ("district", "District (జిల్లా / जिला)",                 "text",   False, False, False),
            ("mobile",   "Mobile Number (మొబైల్ / मोबाइल)",          "text",   True,  False, False),
            ("bank_acc", "Bank Account Number",                      "text",   True,  False, False),
            ("ifsc",     "IFSC Code",                               "text",   False, True,  False),
        ],
        "fields_js": """[
  {"id":"name",    "isNumeric":false,"isId":false,"isName":true,
   "labels":{"en-IN":"Farmer Name","te-IN":"రైతు పేరు","hi-IN":"किसान का नाम","ta-IN":"விவசாயி பெயர்"},
   "questions":{"en-IN":"Please tell your full name clearly.","te-IN":"మీ పూర్తి పేరు స్పష్టంగా చెప్పండి.","hi-IN":"कृपया अपना पूरा नाम स्पष्ट रूप से बताएं।"},
   "matchKeywords":["name","పేరు","नाम"]},
  {"id":"aadhaar", "isNumeric":true,"isId":false,"isName":false,
   "labels":{"en-IN":"Aadhaar Number","te-IN":"ఆధార్ నెంబర్","hi-IN":"आधार नंबर"},
   "questions":{"en-IN":"Please say your 12-digit Aadhaar number.","te-IN":"మీ 12 అంకెల ఆధార్ నెంబర్ చెప్పండి.","hi-IN":"अपना 12 अंकों का आधार नंबर बताएं।"},
   "matchKeywords":["aadhaar","aadhar","ఆధార్","आधार"]},
  {"id":"state",   "isNumeric":false,"isId":false,"isName":false,
   "labels":{"en-IN":"State","te-IN":"రాష్ట్రం","hi-IN":"राज्य"},
   "questions":{"en-IN":"Please tell your state. For example: Telangana, Maharashtra.","te-IN":"మీ రాష్ట్రం పేరు చెప్పండి.","hi-IN":"अपने राज्य का नाम बताएं।"},
   "matchKeywords":["state","రాష్ట్రం","राज्य"]},
  {"id":"district","isNumeric":false,"isId":false,"isName":false,
   "labels":{"en-IN":"District","te-IN":"జిల్లా","hi-IN":"जिला"},
   "questions":{"en-IN":"Please tell your district name.","te-IN":"మీ జిల్లా పేరు చెప్పండి.","hi-IN":"अपने जिले का नाम बताएं।"},
   "matchKeywords":["district","జిల్లా","जिला"]},
  {"id":"mobile",  "isNumeric":true,"isId":false,"isName":false,
   "labels":{"en-IN":"Mobile Number","te-IN":"మొబైల్ నెంబర్","hi-IN":"मोबाइल नंबर"},
   "questions":{"en-IN":"Please say your 10-digit mobile number.","te-IN":"మీ 10 అంకెల మొబైల్ నెంబర్ చెప్పండి.","hi-IN":"अपना 10 अंकों का मोबाइल नंबर बताएं।"},
   "matchKeywords":["mobile","phone","మొబైల్","मोबाइल"]},
  {"id":"bank_acc","isNumeric":true,"isId":false,"isName":false,
   "labels":{"en-IN":"Bank Account Number","te-IN":"బ్యాంక్ అకౌంట్ నెంబర్","hi-IN":"बैंक खाता नंबर"},
   "questions":{"en-IN":"Please say your bank account number.","te-IN":"మీ బ్యాంక్ అకౌంట్ నెంబర్ చెప్పండి.","hi-IN":"अपना बैंक खाता नंबर बताएं।"},
   "matchKeywords":["bank","account","బ్యాంక్","बैंक"]},
  {"id":"ifsc",    "isNumeric":false,"isId":true,"isName":false,
   "labels":{"en-IN":"IFSC Code","te-IN":"IFSC కోడ్","hi-IN":"IFSC कोड"},
   "questions":{"en-IN":"Please say your bank IFSC code. For example: S B I N zero zero one two three.","te-IN":"మీ బ్యాంక్ IFSC కోడ్ చెప్పండి.","hi-IN":"अपना IFSC कोड बताएं।"},
   "matchKeywords":["ifsc","IFSC"]}
]"""
    },
    {
        "service_id": "rythu_bandhu",
        "filename":   "rythu_bandhu.html",
        "title":      "Rythu Bandhu",
        "subtitle":   "₹5,000/acre per season investment support for Telangana farmers",
        "emoji":      "🌾",
        "ministry":   "Agriculture Department — Government of Telangana",
        "color":      "#2e7d32",
        "color_light":"#43a047",
        "form_fields": [
            ("name",      "Farmer Name (రైతు పేరు)",     "text", False, False, True),
            ("aadhaar",   "Aadhaar Number (ఆధార్)",       "text", True,  False, False),
            ("pattadar_passbook", "Pattadar Passbook No.", "text", True,  False, False),
            ("land_acres","Land in Acres (ఎకరాలు)",       "text", True,  False, False),
            ("village",   "Village (గ్రామం)",             "text", False, False, False),
            ("mandal",    "Mandal (మండలం)",               "text", False, False, False),
            ("mobile",    "Mobile Number (మొబైల్)",       "text", True,  False, False),
        ],
        "fields_js": """[
  {"id":"name","isNumeric":false,"isId":false,"isName":true,
   "labels":{"en-IN":"Farmer Name","te-IN":"రైతు పేరు","hi-IN":"किसान का नाम"},
   "questions":{"en-IN":"Please tell your full name.","te-IN":"మీ పూర్తి పేరు చెప్పండి.","hi-IN":"अपना पूरा नाम बताएं।"},
   "matchKeywords":["name","పేరు","नाम"]},
  {"id":"aadhaar","isNumeric":true,"isId":false,"isName":false,
   "labels":{"en-IN":"Aadhaar Number","te-IN":"ఆధార్ నెంబర్","hi-IN":"आधार नंबर"},
   "questions":{"en-IN":"Say your 12-digit Aadhaar number.","te-IN":"మీ 12 అంకెల ఆధార్ నెంబర్ చెప్పండి.","hi-IN":"12 अंकों का आधार नंबर बताएं।"},
   "matchKeywords":["aadhaar","aadhar","ఆధార్"]},
  {"id":"pattadar_passbook","isNumeric":true,"isId":false,"isName":false,
   "labels":{"en-IN":"Pattadar Passbook Number","te-IN":"పట్టదార్ పాస్ బుక్ నెంబర్","hi-IN":"पट्टेदार पासबुक नंबर"},
   "questions":{"en-IN":"Please say your Pattadar Passbook number.","te-IN":"మీ పట్టదార్ పాస్ బుక్ నెంబర్ చెప్పండి.","hi-IN":"अपना पट्टेदार पासबुक नंबर बताएं।"},
   "matchKeywords":["pattadar","passbook","పట్టదార్"]},
  {"id":"land_acres","isNumeric":true,"isId":false,"isName":false,
   "labels":{"en-IN":"Land in Acres","te-IN":"భూమి ఎకరాలు","hi-IN":"भूमि एकड़ में"},
   "questions":{"en-IN":"How many acres of land do you own? Say the number.","te-IN":"మీకు ఎంత భూమి ఉంది? ఎకరాల్లో చెప్పండి.","hi-IN":"आपके पास कितने एकड़ जमीन है?"},
   "matchKeywords":["acres","land","ఎకరాలు","భూమి"]},
  {"id":"village","isNumeric":false,"isId":false,"isName":false,
   "labels":{"en-IN":"Village","te-IN":"గ్రామం","hi-IN":"गाँव"},
   "questions":{"en-IN":"Please tell your village name.","te-IN":"మీ గ్రామం పేరు చెప్పండి.","hi-IN":"अपने गाँव का नाम बताएं।"},
   "matchKeywords":["village","గ్రామం","गाँव"]},
  {"id":"mandal","isNumeric":false,"isId":false,"isName":false,
   "labels":{"en-IN":"Mandal","te-IN":"మండలం","hi-IN":"मंडल"},
   "questions":{"en-IN":"Please tell your mandal name.","te-IN":"మీ మండలం పేరు చెప్పండి.","hi-IN":"अपने मंडल का नाम बताएं।"},
   "matchKeywords":["mandal","మండలం"]},
  {"id":"mobile","isNumeric":true,"isId":false,"isName":false,
   "labels":{"en-IN":"Mobile Number","te-IN":"మొబైల్ నెంబర్","hi-IN":"मोबाइल नंबर"},
   "questions":{"en-IN":"Say your 10-digit mobile number.","te-IN":"మీ 10 అంకెల మొబైల్ నెంబర్ చెప్పండి.","hi-IN":"10 अंकों का मोबाइल नंबर बताएं।"},
   "matchKeywords":["mobile","phone","మొబైల్"]}
]"""
    },
    {
        "service_id": "aarogyasri",
        "filename":   "aarogyasri.html",
        "title":      "Aarogyasri Health Scheme",
        "subtitle":   "Cashless medical treatment up to ₹5 lakh for BPL families",
        "emoji":      "🏥",
        "ministry":   "Aarogyasri Health Care Trust — Government of Telangana",
        "color":      "#00695c",
        "color_light":"#009688",
        "form_fields": [
            ("name",         "Patient Name (రోగి పేరు)",       "text", False, False, True),
            ("aadhaar",      "Aadhaar Number",                  "text", True,  False, False),
            ("ration_card",  "Ration Card Number (రేషన్ కార్డ్)","text", False, False, False),
            ("hospital",     "Preferred Hospital",              "text", False, False, False),
            ("diagnosis",    "Medical Condition/Diagnosis",     "text", False, False, False),
            ("mobile",       "Mobile Number",                   "text", True,  False, False),
        ],
        "fields_js": """[
  {"id":"name","isNumeric":false,"isId":false,"isName":true,
   "labels":{"en-IN":"Patient Name","te-IN":"రోగి పేరు","hi-IN":"रोगी का नाम"},
   "questions":{"en-IN":"Please tell the patient's full name.","te-IN":"రోగి పూర్తి పేరు చెప్పండి.","hi-IN":"रोगी का पूरा नाम बताएं।"},
   "matchKeywords":["name","patient","పేరు","नाम"]},
  {"id":"aadhaar","isNumeric":true,"isId":false,"isName":false,
   "labels":{"en-IN":"Aadhaar Number","te-IN":"ఆధార్ నెంబర్","hi-IN":"आधार नंबर"},
   "questions":{"en-IN":"Say your 12-digit Aadhaar number.","te-IN":"12 అంకెల ఆధార్ నెంబర్ చెప్పండి.","hi-IN":"12 अंकों का आधार नंबर बताएं।"},
   "matchKeywords":["aadhaar","ఆధార్","आधार"]},
  {"id":"ration_card","isNumeric":false,"isId":false,"isName":false,
   "labels":{"en-IN":"Ration Card Number","te-IN":"రేషన్ కార్డ్ నెంబర్","hi-IN":"राशन कार्ड नंबर"},
   "questions":{"en-IN":"Please say your ration card number.","te-IN":"మీ రేషన్ కార్డ్ నెంబర్ చెప్పండి.","hi-IN":"अपना राशन कार्ड नंबर बताएं।"},
   "matchKeywords":["ration","రేషన్","राशन"]},
  {"id":"hospital","isNumeric":false,"isId":false,"isName":false,
   "labels":{"en-IN":"Preferred Hospital","te-IN":"ఆసుపత్రి పేరు","hi-IN":"अस्पताल"},
   "questions":{"en-IN":"Which hospital do you prefer for treatment?","te-IN":"మీకు ఏ ఆసుపత్రి కావాలి?","hi-IN":"आप किस अस्पताल में इलाज चाहते हैं?"},
   "matchKeywords":["hospital","ఆసుపత్రి","अस्पताल"]},
  {"id":"diagnosis","isNumeric":false,"isId":false,"isName":false,
   "labels":{"en-IN":"Medical Condition","te-IN":"వ్యాధి వివరాలు","hi-IN":"बीमारी का विवरण"},
   "questions":{"en-IN":"Please describe the medical condition or illness briefly.","te-IN":"వ్యాధి లేదా అనారోగ్య వివరాలు సంక్షిప్తంగా చెప్పండి.","hi-IN":"बीमारी का संक्षिप्त विवरण बताएं।"},
   "matchKeywords":["diagnosis","disease","illness","వ్యాధి","बीमारी"]},
  {"id":"mobile","isNumeric":true,"isId":false,"isName":false,
   "labels":{"en-IN":"Mobile Number","te-IN":"మొబైల్ నెంబర్","hi-IN":"मोबाइल नंबर"},
   "questions":{"en-IN":"Say your 10-digit mobile number.","te-IN":"మీ మొబైల్ నెంబర్ చెప్పండి.","hi-IN":"मोबाइल नंबर बताएं।"},
   "matchKeywords":["mobile","మొబైల్","मोबाइल"]}
]"""
    },
    {
        "service_id": "crop_insurance",
        "filename":   "crop_insurance.html",
        "title":      "PM Fasal Bima Yojana",
        "subtitle":   "Crop insurance against loss due to natural calamities",
        "emoji":      "🛡️",
        "ministry":   "Ministry of Agriculture & Farmers Welfare — Government of India",
        "color":      "#1565c0",
        "color_light":"#1976d2",
        "form_fields": [
            ("name",       "Farmer Name",              "text", False, False, True),
            ("aadhaar",    "Aadhaar Number",            "text", True,  False, False),
            ("crop_type",  "Crop Type (పంట రకం)",      "text", False, False, False),
            ("land_acres", "Land Area (Acres)",         "text", True,  False, False),
            ("state",      "State",                    "text", False, False, False),
            ("season",     "Season (Kharif/Rabi)",     "text", False, False, False),
            ("mobile",     "Mobile Number",             "text", True,  False, False),
        ],
        "fields_js": """[
  {"id":"name","isNumeric":false,"isId":false,"isName":true,
   "labels":{"en-IN":"Farmer Name","te-IN":"రైతు పేరు","hi-IN":"किसान का नाम"},
   "questions":{"en-IN":"Please tell your full name.","te-IN":"మీ పూర్తి పేరు చెప్పండి.","hi-IN":"अपना पूरा नाम बताएं।"},
   "matchKeywords":["name","పేరు","नाम"]},
  {"id":"aadhaar","isNumeric":true,"isId":false,"isName":false,
   "labels":{"en-IN":"Aadhaar Number","te-IN":"ఆధార్","hi-IN":"आधार"},
   "questions":{"en-IN":"Say your 12-digit Aadhaar number.","te-IN":"12 అంకెల ఆధార్ నెంబర్ చెప్పండి.","hi-IN":"12 अंकों का आधार नंबर बताएं।"},
   "matchKeywords":["aadhaar","ఆధార్","आधार"]},
  {"id":"crop_type","isNumeric":false,"isId":false,"isName":false,
   "labels":{"en-IN":"Crop Type","te-IN":"పంట రకం","hi-IN":"फसल प्रकार"},
   "questions":{"en-IN":"What crop do you want to insure? For example: rice, wheat, cotton.","te-IN":"మీరు ఏ పంటకు బీమా కావాలి? ఉదా: వరి, పత్తి.","hi-IN":"किस फसल का बीमा चाहते हैं? उदाहरण: चावल, गेहूं, कपास।"},
   "matchKeywords":["crop","పంట","फसल"]},
  {"id":"land_acres","isNumeric":true,"isId":false,"isName":false,
   "labels":{"en-IN":"Land Area (Acres)","te-IN":"భూమి (ఎకరాలు)","hi-IN":"जमीन (एकड़)"},
   "questions":{"en-IN":"How many acres of land are you insuring?","te-IN":"ఎన్ని ఎకరాలకు బీమా కావాలి?","hi-IN":"कितने एकड़ का बीमा चाहते हैं?"},
   "matchKeywords":["acres","land","ఎకరాలు","जमीन"]},
  {"id":"state","isNumeric":false,"isId":false,"isName":false,
   "labels":{"en-IN":"State","te-IN":"రాష్ట్రం","hi-IN":"राज्य"},
   "questions":{"en-IN":"Please tell your state name.","te-IN":"మీ రాష్ట్రం పేరు చెప్పండి.","hi-IN":"अपना राज्य बताएं।"},
   "matchKeywords":["state","రాష్ట్రం","राज्य"]},
  {"id":"season","isNumeric":false,"isId":false,"isName":false,
   "labels":{"en-IN":"Season","te-IN":"సీజన్","hi-IN":"मौसम"},
   "questions":{"en-IN":"Which season? Say Kharif or Rabi.","te-IN":"ఏ సీజన్? ఖరీఫ్ లేదా రబీ అనండి.","hi-IN":"कौनसा मौसम? खरीफ या रबी बताएं।"},
   "matchKeywords":["season","kharif","rabi","సీజన్"]},
  {"id":"mobile","isNumeric":true,"isId":false,"isName":false,
   "labels":{"en-IN":"Mobile Number","te-IN":"మొబైల్","hi-IN":"मोबाइल"},
   "questions":{"en-IN":"Say your 10-digit mobile number.","te-IN":"10 అంకెల మొబైల్ నెంబర్ చెప్పండి.","hi-IN":"10 अंकों का मोबाइल नंबर बताएं।"},
   "matchKeywords":["mobile","మొబైల్","मोबाइल"]}
]"""
    },
    {
        "service_id": "dharani",
        "filename":   "dharani.html",
        "title":      "Dharani Land Portal",
        "subtitle":   "Register, verify and manage agricultural land records",
        "emoji":      "🗺️",
        "ministry":   "Revenue Department — Government of Telangana",
        "color":      "#5e35b1",
        "color_light":"#7e57c2",
        "form_fields": [
            ("name",        "Owner Name (యజమాని పేరు)",         "text", False, False, True),
            ("aadhaar",     "Aadhaar Number",                    "text", True,  False, False),
            ("survey_no",   "Survey Number (సర్వే నెంబర్)",       "text", False, False, False),
            ("village",     "Village (గ్రామం)",                  "text", False, False, False),
            ("mandal",      "Mandal (మండలం)",                    "text", False, False, False),
            ("district",    "District (జిల్లా)",                  "text", False, False, False),
            ("mobile",      "Mobile Number",                     "text", True,  False, False),
        ],
        "fields_js": """[
  {"id":"name","isNumeric":false,"isId":false,"isName":true,
   "labels":{"en-IN":"Owner Name","te-IN":"యజమాని పేరు","hi-IN":"मालिक का नाम"},
   "questions":{"en-IN":"Please tell the land owner's full name.","te-IN":"భూ యజమాని పూర్తి పేరు చెప్పండి.","hi-IN":"जमीन मालिक का पूरा नाम बताएं।"},
   "matchKeywords":["name","owner","పేరు","यजमानी"]},
  {"id":"aadhaar","isNumeric":true,"isId":false,"isName":false,
   "labels":{"en-IN":"Aadhaar Number","te-IN":"ఆధార్","hi-IN":"आधार"},
   "questions":{"en-IN":"Say your 12-digit Aadhaar number.","te-IN":"12 అంకెల ఆధార్ నెంబర్ చెప్పండి.","hi-IN":"12 अंकों का आधार बताएं।"},
   "matchKeywords":["aadhaar","ఆధార్"]},
  {"id":"survey_no","isNumeric":false,"isId":false,"isName":false,
   "labels":{"en-IN":"Survey Number","te-IN":"సర్వే నెంబర్","hi-IN":"सर्वे नंबर"},
   "questions":{"en-IN":"Please say the land survey number.","te-IN":"భూమి సర్వే నెంబర్ చెప్పండి.","hi-IN":"जमीन का सर्वे नंबर बताएं।"},
   "matchKeywords":["survey","సర్వే","सर्वे"]},
  {"id":"village","isNumeric":false,"isId":false,"isName":false,
   "labels":{"en-IN":"Village","te-IN":"గ్రామం","hi-IN":"गाँव"},
   "questions":{"en-IN":"Please tell your village name.","te-IN":"మీ గ్రామం పేరు చెప్పండి.","hi-IN":"गाँव का नाम बताएं।"},
   "matchKeywords":["village","గ్రామం","गाँव"]},
  {"id":"mandal","isNumeric":false,"isId":false,"isName":false,
   "labels":{"en-IN":"Mandal","te-IN":"మండలం","hi-IN":"मंडल"},
   "questions":{"en-IN":"Please tell your mandal.","te-IN":"మీ మండలం పేరు చెప్పండి.","hi-IN":"मंडल बताएं।"},
   "matchKeywords":["mandal","మండలం"]},
  {"id":"district","isNumeric":false,"isId":false,"isName":false,
   "labels":{"en-IN":"District","te-IN":"జిల్లా","hi-IN":"जिला"},
   "questions":{"en-IN":"Please tell your district.","te-IN":"మీ జిల్లా పేరు చెప్పండి.","hi-IN":"जिला बताएं।"},
   "matchKeywords":["district","జిల్లా","जिला"]},
  {"id":"mobile","isNumeric":true,"isId":false,"isName":false,
   "labels":{"en-IN":"Mobile Number","te-IN":"మొబైల్","hi-IN":"मोबाइल"},
   "questions":{"en-IN":"Say your 10-digit mobile number.","te-IN":"10 అంకెల మొబైల్ చెప్పండి.","hi-IN":"10 अंकों का मोबाइल बताएं।"},
   "matchKeywords":["mobile","మొబైల్","मोबाइल"]}
]"""
    },
    {
        "service_id": "kalyana_lakshmi",
        "filename":   "kalyana_lakshmi.html",
        "title":      "Kalyana Lakshmi / Shaadi Mubarak",
        "subtitle":   "₹1,00,116 marriage assistance for SC/ST/BC/Minority girls",
        "emoji":      "💍",
        "ministry":   "BC Welfare Department — Government of Telangana",
        "color":      "#c2185b",
        "color_light":"#e91e63",
        "form_fields": [
            ("bride_name",    "Bride's Name (వధువు పేరు)",         "text", False, False, True),
            ("aadhaar",       "Bride's Aadhaar Number",             "text", True,  False, False),
            ("groom_name",    "Groom's Name (వరుడు పేరు)",          "text", False, False, True),
            ("caste_category","Caste Category (SC/ST/BC/Minority)", "text", False, False, False),
            ("wedding_date",  "Marriage Date (DD/MM/YYYY)",         "text", False, False, False),
            ("mobile",        "Mobile Number",                      "text", True,  False, False),
        ],
        "fields_js": """[
  {"id":"bride_name","isNumeric":false,"isId":false,"isName":true,
   "labels":{"en-IN":"Bride's Name","te-IN":"వధువు పేరు","hi-IN":"दुल्हन का नाम"},
   "questions":{"en-IN":"Please tell the bride's full name.","te-IN":"వధువు పూర్తి పేరు చెప్పండి.","hi-IN":"दुल्हन का पूरा नाम बताएं।"},
   "matchKeywords":["bride","వధువు","दुल्हन"]},
  {"id":"aadhaar","isNumeric":true,"isId":false,"isName":false,
   "labels":{"en-IN":"Bride's Aadhaar","te-IN":"వధువు ఆధార్","hi-IN":"दुल्हन का आधार"},
   "questions":{"en-IN":"Say the bride's 12-digit Aadhaar number.","te-IN":"వధువు 12 అంకెల ఆధార్ చెప్పండి.","hi-IN":"दुल्हन का 12 अंकों का आधार नंबर बताएं।"},
   "matchKeywords":["aadhaar","ఆధార్","आधार"]},
  {"id":"groom_name","isNumeric":false,"isId":false,"isName":true,
   "labels":{"en-IN":"Groom's Name","te-IN":"వరుడు పేరు","hi-IN":"दूल्हे का नाम"},
   "questions":{"en-IN":"Please tell the groom's full name.","te-IN":"వరుడు పూర్తి పేరు చెప్పండి.","hi-IN":"दूल्हे का पूरा नाम बताएं।"},
   "matchKeywords":["groom","వరుడు","दूल्हा"]},
  {"id":"caste_category","isNumeric":false,"isId":false,"isName":false,
   "labels":{"en-IN":"Caste Category","te-IN":"కుల వర్గం","hi-IN":"जाति वर्ग"},
   "questions":{"en-IN":"Please say your caste category. Say SC, ST, BC, or Minority.","te-IN":"మీ కుల వర్గం చెప్పండి. SC, ST, BC లేదా Minority అనండి.","hi-IN":"जाति वर्ग बताएं। SC, ST, BC या Minority कहें।"},
   "matchKeywords":["caste","category","కుల","जाति"]},
  {"id":"wedding_date","isNumeric":false,"isId":false,"isName":false,
   "labels":{"en-IN":"Marriage Date","te-IN":"వివాహ తేదీ","hi-IN":"विवाह की तारीख"},
   "questions":{"en-IN":"Please say the marriage date. Day, month, year.","te-IN":"వివాహ తేదీ చెప్పండి. రోజు, నెల, సంవత్సరం.","hi-IN":"विवाह की तारीख बताएं। दिन, महीना, साल।"},
   "matchKeywords":["date","wedding","marriage","తేదీ","तारीख"]},
  {"id":"mobile","isNumeric":true,"isId":false,"isName":false,
   "labels":{"en-IN":"Mobile Number","te-IN":"మొబైల్","hi-IN":"मोबाइल"},
   "questions":{"en-IN":"Say your 10-digit mobile number.","te-IN":"10 అంకెల మొబైల్ చెప్పండి.","hi-IN":"10 अंकों का मोबाइल बताएं।"},
   "matchKeywords":["mobile","మొబైల్","मोबाइल"]}
]"""
    },
    {
        "service_id": "ration_card",
        "filename":   "ration.html",
        "title":      "Ration Card (NFSA)",
        "subtitle":   "National Food Security Act — New card or update existing",
        "emoji":      "🍚",
        "ministry":   "Civil Supplies Department — Government of India",
        "color":      "#d84315",
        "color_light":"#f4511e",
        "form_fields": [
            ("name",         "Head of Family Name",        "text", False, False, True),
            ("aadhaar",      "Aadhaar Number",              "text", True,  False, False),
            ("family_count", "Family Members Count",        "text", True,  False, False),
            ("income",       "Annual Family Income (₹)",    "text", True,  False, False),
            ("address",      "Current Address",             "text", False, False, False),
            ("mobile",       "Mobile Number",               "text", True,  False, False),
        ],
        "fields_js": """[
  {"id":"name","isNumeric":false,"isId":false,"isName":true,
   "labels":{"en-IN":"Head of Family Name","te-IN":"కుటుంబ పెద్ద పేరు","hi-IN":"परिवार के मुखिया का नाम"},
   "questions":{"en-IN":"Please tell the head of family's full name.","te-IN":"కుటుంబ పెద్ద పూర్తి పేరు చెప్పండి.","hi-IN":"परिवार के मुखिया का पूरा नाम बताएं।"},
   "matchKeywords":["name","head","పేరు","नाम"]},
  {"id":"aadhaar","isNumeric":true,"isId":false,"isName":false,
   "labels":{"en-IN":"Aadhaar Number","te-IN":"ఆధార్","hi-IN":"आधार"},
   "questions":{"en-IN":"Say your 12-digit Aadhaar number.","te-IN":"12 అంకెల ఆధార్ చెప్పండి.","hi-IN":"12 अंकों का आधार बताएं।"},
   "matchKeywords":["aadhaar","ఆధార్","आधार"]},
  {"id":"family_count","isNumeric":true,"isId":false,"isName":false,
   "labels":{"en-IN":"Family Members Count","te-IN":"కుటుంబ సభ్యుల సంఖ్య","hi-IN":"परिवार के सदस्यों की संख्या"},
   "questions":{"en-IN":"How many members are in your family?","te-IN":"మీ కుటుంబంలో ఎంత మంది సభ్యులు ఉన్నారు?","hi-IN":"आपके परिवार में कितने सदस्य हैं?"},
   "matchKeywords":["family","members","కుటుంబ","परिवार"]},
  {"id":"income","isNumeric":true,"isId":false,"isName":false,
   "labels":{"en-IN":"Annual Family Income","te-IN":"వార్షిక కుటుంబ ఆదాయం","hi-IN":"वार्षिक पारिवारिक आय"},
   "questions":{"en-IN":"What is your family's annual income in rupees?","te-IN":"మీ కుటుంబ వార్షిక ఆదాయం ఎంత?","hi-IN":"परिवार की सालाना आय कितनी है?"},
   "matchKeywords":["income","ఆదాయం","आय"]},
  {"id":"address","isNumeric":false,"isId":false,"isName":false,
   "labels":{"en-IN":"Current Address","te-IN":"ప్రస్తుత చిరునామా","hi-IN":"वर्तमान पता"},
   "questions":{"en-IN":"Please tell your current full address.","te-IN":"మీ ప్రస్తుత పూర్తి చిరునామా చెప్పండి.","hi-IN":"अपना वर्तमान पूरा पता बताएं।"},
   "matchKeywords":["address","చిరునామా","पता"]},
  {"id":"mobile","isNumeric":true,"isId":false,"isName":false,
   "labels":{"en-IN":"Mobile Number","te-IN":"మొబైల్","hi-IN":"मोबाइल"},
   "questions":{"en-IN":"Say your 10-digit mobile number.","te-IN":"10 అంకెల మొబైల్ చెప్పండి.","hi-IN":"10 अंकों का मोबाइल बताएं।"},
   "matchKeywords":["mobile","మొబైల్","मोबाइल"]}
]"""
    },
    {
        "service_id": "pension",
        "filename":   "pension.html",
        "title":      "Aasara Pension Scheme",
        "subtitle":   "₹2,016/month for elderly, disabled, widows & vulnerable citizens",
        "emoji":      "👵",
        "ministry":   "Women, Children, Disabled & Senior Citizens Dept — Govt of Telangana",
        "color":      "#455a64",
        "color_light":"#607d8b",
        "form_fields": [
            ("name",       "Applicant Name",                "text", False, False, True),
            ("aadhaar",    "Aadhaar Number",                 "text", True,  False, False),
            ("age",        "Age (వయసు / आयु)",               "text", True,  False, False),
            ("category",   "Category (Elderly/Disabled/Widow)", "text", False, False, False),
            ("mobile",     "Mobile Number",                  "text", True,  False, False),
        ],
        "fields_js": """[
  {"id":"name","isNumeric":false,"isId":false,"isName":true,
   "labels":{"en-IN":"Applicant Name","te-IN":"దరఖాస్తుదారు పేరు","hi-IN":"आवेदक का नाम"},
   "questions":{"en-IN":"Please tell your full name.","te-IN":"మీ పూర్తి పేరు చెప్పండి.","hi-IN":"अपना पूरा नाम बताएं।"},
   "matchKeywords":["name","పేరు","नाम"]},
  {"id":"aadhaar","isNumeric":true,"isId":false,"isName":false,
   "labels":{"en-IN":"Aadhaar Number","te-IN":"ఆధార్","hi-IN":"आधार"},
   "questions":{"en-IN":"Say your 12-digit Aadhaar number.","te-IN":"12 అంకెల ఆధార్ చెప్పండి.","hi-IN":"12 अंकों का आधार बताएं।"},
   "matchKeywords":["aadhaar","ఆధార్","आधार"]},
  {"id":"age","isNumeric":true,"isId":false,"isName":false,
   "labels":{"en-IN":"Age","te-IN":"వయసు","hi-IN":"आयु"},
   "questions":{"en-IN":"Please tell your age in years.","te-IN":"మీ వయసు సంవత్సరాల్లో చెప్పండి.","hi-IN":"अपनी आयु वर्षों में बताएं।"},
   "matchKeywords":["age","వయసు","आयु"]},
  {"id":"category","isNumeric":false,"isId":false,"isName":false,
   "labels":{"en-IN":"Pension Category","te-IN":"పెన్షన్ వర్గం","hi-IN":"पेंशन श्रेणी"},
   "questions":{"en-IN":"Which category applies to you? Say elderly, disabled, widow, or single woman.","te-IN":"మీకు ఏ వర్గం వర్తిస్తుంది? వృద్ధులు, వికలాంగులు, వితంతువు అని చెప్పండి.","hi-IN":"आप किस श्रेणी में हैं? बुजुर्ग, विकलांग, विधवा या अकेली महिला।"},
   "matchKeywords":["category","elderly","widow","disabled","వర్గం","श्रेणी"]},
  {"id":"mobile","isNumeric":true,"isId":false,"isName":false,
   "labels":{"en-IN":"Mobile Number","te-IN":"మొబైల్","hi-IN":"मोबाइल"},
   "questions":{"en-IN":"Say your 10-digit mobile number.","te-IN":"10 అంకెల మొబైల్ చెప్పండి.","hi-IN":"10 अंकों का मोबाइल बताएं।"},
   "matchKeywords":["mobile","మొబైల్","मोबाइल"]}
]"""
    },
    {
        "service_id": "eseva",
        "filename":   "eseva.html",
        "title":      "MeeSeva / eSeva Services",
        "subtitle":   "350+ citizen services — certificates, bills, registrations",
        "emoji":      "💡",
        "ministry":   "IT Department — Government of Telangana",
        "color":      "#0277bd",
        "color_light":"#0288d1",
        "form_fields": [
            ("name",        "Applicant Name",                 "text", False, False, True),
            ("aadhaar",     "Aadhaar Number",                  "text", True,  False, False),
            ("service_type","Service Required",               "text", False, False, False),
            ("mobile",      "Mobile Number",                   "text", True,  False, False),
        ],
        "fields_js": """[
  {"id":"name","isNumeric":false,"isId":false,"isName":true,
   "labels":{"en-IN":"Applicant Name","te-IN":"దరఖాస్తుదారు పేరు","hi-IN":"आवेदक का नाम"},
   "questions":{"en-IN":"Please tell your full name.","te-IN":"మీ పూర్తి పేరు చెప్పండి.","hi-IN":"अपना पूरा नाम बताएं।"},
   "matchKeywords":["name","పేరు","नाम"]},
  {"id":"aadhaar","isNumeric":true,"isId":false,"isName":false,
   "labels":{"en-IN":"Aadhaar Number","te-IN":"ఆధార్","hi-IN":"आधार"},
   "questions":{"en-IN":"Say your 12-digit Aadhaar number.","te-IN":"12 అంకెల ఆధార్ చెప్పండి.","hi-IN":"12 अंकों का आधार बताएं।"},
   "matchKeywords":["aadhaar","ఆధార్","आधार"]},
  {"id":"service_type","isNumeric":false,"isId":false,"isName":false,
   "labels":{"en-IN":"Service Required","te-IN":"కావలసిన సేవ","hi-IN":"आवश्यक सेवा"},
   "questions":{"en-IN":"Which service do you need? For example: income certificate, caste certificate, or electricity bill.","te-IN":"మీకు ఏ సేవ కావాలి? ఉదా: ఆదాయ సర్టిఫికెట్, కులం సర్టిఫికెట్.","hi-IN":"आपको कौनसी सेवा चाहिए? जैसे: आय प्रमाण, जाति प्रमाण, बिजली बिल।"},
   "matchKeywords":["service","certificate","సేవ","सेवा"]},
  {"id":"mobile","isNumeric":true,"isId":false,"isName":false,
   "labels":{"en-IN":"Mobile Number","te-IN":"మొబైల్","hi-IN":"मोबाइल"},
   "questions":{"en-IN":"Say your 10-digit mobile number.","te-IN":"10 అంకెల మొబైల్ చెప్పండి.","hi-IN":"10 అంకों का मोबाइल बताएं।"},
   "matchKeywords":["mobile","మొబైల్","मोबाइल"]}
]"""
    },
]


def build_form_fields_html(fields):
    html = ""
    for field_id, label, ftype, is_numeric, is_id, is_name in fields:
        placeholder = label.split("(")[0].strip()
        html += f"""        <div class="field-group">
          <label>{label}</label>
          <input type="{ftype}" id="{field_id}" placeholder="{placeholder}" autocomplete="off">
        </div>\n"""
    return html


def generate_all():
    import os
    out_dir = os.path.join(os.path.dirname(__file__), "templates")
    os.makedirs(out_dir, exist_ok=True)

    for scheme in SCHEMES:
        form_fields_html = build_form_fields_html(scheme["form_fields"])
        html = build_scheme_page(
            service_id=scheme["service_id"],
            title=scheme["title"],
            subtitle=scheme["subtitle"],
            emoji=scheme["emoji"],
            ministry=scheme["ministry"],
            color=scheme["color"],
            color_light=scheme["color_light"],
            fields_js=scheme["fields_js"],
            form_fields_html=form_fields_html,
        )
        path = os.path.join(out_dir, scheme["filename"])
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"  ✅ Generated: {scheme['filename']}")

    print("\n✅ All 9 scheme pages generated!")


if __name__ == "__main__":
    generate_all()
