/**
 * SARATHI AGENTIC VOICE ENGINE v4.0 — Elite Conversational AI
 * ═══════════════════════════════════════════════════════════
 * Features:
 *  ✅ Conversational chat-bubble UI (one question at a time)
 *  ✅ Real Web Audio API waveform visualizer
 *  ✅ Live transcript preview
 *  ✅ AI Confirmation Layer ("I heard Aadhaar ending in 4532. Correct?")
 *  ✅ Aadhaar / phone number masking
 *  ✅ Tesseract.js OCR document auto-fill
 *  ✅ PDF receipt generation
 *  ✅ Voice gender toggle (Male / Female)
 *  ✅ Smart error recovery ("I couldn't hear clearly...")
 *  ✅ GCP STT → Browser STT seamless fallback
 *  ✅ iOS Audio unlock
 *  ✅ Offline queuing
 */

const SV_VERSION = "4.0";

/* ── Device detection ─────────────────────────────────────── */
const IS_IOS     = /iPad|iPhone|iPod/.test(navigator.userAgent) && !window.MSStream;
const IS_SAFARI  = /^((?!chrome|android).)*safari/i.test(navigator.userAgent);
const IS_ANDROID = /android/i.test(navigator.userAgent);
const IS_MOBILE  = IS_IOS || IS_ANDROID;

/* ── Feature flags (set in HTML) ─────────────────────────── */
let useGcpStt  = window.SARATHI_USE_GCP_STT === true;
let useGcpTts  = window.SARATHI_USE_GCP_TTS === true;
let useGemini  = window.SARATHI_USE_GEMINI  === true;

/* ── State ────────────────────────────────────────────────── */
let selectedLang        = "en-IN";
let currentStep         = 0;
let mode                = "idle";
let collectedData       = {};
let pendingValue        = null;   // value waiting for confirmation
let pendingFieldIdx     = null;
let fixingFieldIndex    = -1;
let conversationHistory = [];
let eligibilityAnswers  = {};
let eligibilityStep     = 0;
let retryCount          = 0;
let voiceGender         = "female";

/* ── Audio/mic state ──────────────────────────────────────── */
let socket        = null;
let mediaRecorder = null;
let audioChunks   = [];
let isRecording   = false;
let recognition   = null;
let isListening   = false;
let _micGuard     = false;
let _silenceTimer = null;
let currentAudio  = null;

/* ── Web Audio API waveform ───────────────────────────────── */
let audioContext   = null;
let analyser       = null;
let waveformSource = null;
let waveframeId    = null;
let micStream      = null;

/* ── MIME type for recording ──────────────────────────────── */
function getBestMimeType() {
  const candidates = ["audio/webm;codecs=opus","audio/webm","audio/mp4;codecs=mp4a","audio/mp4","audio/ogg;codecs=opus",""];
  return candidates.find(t => { try { return !t || MediaRecorder.isTypeSupported(t); } catch(e){ return false; } }) || "";
}

/* ── iOS Audio Unlock ─────────────────────────────────────── */
let _audioUnlocked = false;
function unlockAudio() {
  if (_audioUnlocked) return;
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const buf = ctx.createBuffer(1,1,22050);
    const src = ctx.createBufferSource();
    src.buffer = buf; src.connect(ctx.destination); src.start(0); ctx.resume();
    _audioUnlocked = true;
  } catch(e) {}
}

/* ═══════════════════════════════════════════════════════════
   CHAT UI — Conversational bubbles
═══════════════════════════════════════════════════════════ */

function addChatBubble(role, text, opts = {}) {
  const panel = document.getElementById("chat-panel");
  if (!panel) return;

  const bubble = document.createElement("div");
  bubble.className = `chat-bubble ${role}`;

  const now = new Date().toLocaleTimeString("en-IN",{hour:"2-digit",minute:"2-digit"});
  const avatarEmoji = role === "ai" ? "🤖" : "👤";

  bubble.innerHTML = `
    <div class="chat-avatar">${avatarEmoji}</div>
    <div>
      <div class="chat-text">${text}</div>
      <div class="chat-timestamp">${now}</div>
    </div>`;

  if (opts.isTyping) bubble.classList.add("typing");
  panel.appendChild(bubble);
  setTimeout(() => panel.scrollTop = panel.scrollHeight, 50);
  return bubble;
}

function removeTypingBubble() {
  document.querySelectorAll(".chat-bubble.typing").forEach(b => b.remove());
}

function addAiTyping() {
  return addChatBubble("ai", "…", { isTyping: true });
}

/* ── Current-field highlight ──────────────────────────────── */
function showCurrentField(field) {
  const card = document.getElementById("current-field-card");
  if (!card) return;
  card.style.display = "block";
  const label = card.querySelector(".current-field-label");
  const value = card.querySelector(".current-field-value");
  if (label) label.textContent = field.labels[selectedLang] || field.labels["en-IN"];
  if (value) {
    value.textContent = "Listening…";
    value.className = "current-field-value";
  }
}

function updateCurrentFieldValue(fieldId, rawValue) {
  const value = document.getElementById("current-field-value");
  if (!value) return;
  const display = maskSensitive(fieldId, rawValue);
  value.textContent = display;
  value.className = "current-field-value" + (display !== rawValue ? " masked" : "");
}

function hideCurrentField() {
  const card = document.getElementById("current-field-card");
  if (card) card.style.display = "none";
}

/* ── Confirmation card ────────────────────────────────────── */
function showConfirmCard(fieldLabel, displayValue, onYes, onNo) {
  const card = document.getElementById("confirm-card");
  if (!card) { onYes(); return; }
  card.style.display = "block";
  const title = card.querySelector(".confirm-card-title");
  const val   = card.querySelector(".confirm-card-value");
  const yes   = card.querySelector(".confirm-yes");
  const no    = card.querySelector(".confirm-no");

  const sys = getSys(selectedLang);
  if (title) title.textContent = sys.confirmPrompt.replace("{field}", fieldLabel);
  if (val)   val.textContent   = displayValue;

  const cleanup = () => { card.style.display = "none"; yes.onclick = no.onclick = null; };
  if (yes) yes.onclick = () => { cleanup(); onYes(); };
  if (no)  no.onclick  = () => { cleanup(); onNo();  };
}

function hideConfirmCard() {
  const card = document.getElementById("confirm-card");
  if (card) card.style.display = "none";
}

/* ── Field filled indicator ───────────────────────────────── */
function markFieldFilled(fieldId) {
  const el = document.getElementById(fieldId);
  if (el) el.classList.add("filled");
}

/* ── Aadhaar / sensitive data masking ────────────────────── */
function maskSensitive(fieldId, value) {
  const fid = fieldId.toLowerCase();
  if (!value) return "";
  if (fid.includes("aadhaar") && /^\d+$/.test(value.replace(/\s/g,""))) {
    const v = value.replace(/\s/g,"");
    if (v.length === 12) return `XXXX XXXX ${v.slice(8)}`;
    if (v.length > 4)    return `XXXX ${v.slice(-4)}`;
  }
  if (fid.includes("account") && value.length > 4) return `XXXX XXXX ${value.slice(-4)}`;
  if (fid.includes("pan") && value.length > 4)     return `XXXXX${value.slice(-4)}`;
  return value;
}

/* ── Progress bar ─────────────────────────────────────────── */
function updateProgress() {
  const bar  = document.getElementById("progress-bar");
  const fill = document.querySelector(".progress-fill");
  const text = document.getElementById("progress-text");
  if (!bar || !fill) return;
  const fields = window.SARATHI_FIELDS || [];
  if (!fields.length || mode !== "form") { bar.style.display = "none"; return; }
  const done = Object.keys(collectedData).filter(k => fields.find(f=>f.id===k)).length;
  const pct  = Math.round((done / fields.length) * 100);
  bar.style.display = "block";
  fill.style.width  = pct + "%";
  if (text) text.textContent = `${done} of ${fields.length} fields filled`;
}

/* ── Live status text ─────────────────────────────────────── */
function setStatus(msg, type = "info") {
  const el = document.getElementById("live-text");
  if (!el) return;
  el.style.display = "block";
  el.className = "live-text-box " + type;
  el.innerHTML = msg;
}

function clearStatus() {
  const el = document.getElementById("live-text");
  if (el) el.style.display = "none";
}

function setMicBtn(state) {
  const btn = document.getElementById("micBtn");
  if (!btn) return;
  const S = {
    listening:  { cls:"mic-listening",  text:"🔴 Listening — Speak Now"   },
    processing: { cls:"mic-processing", text:"🧠 AI Processing…"          },
    speaking:   { cls:"mic-speaking",   text:"🔊 Speaking…"               },
    confirming: { cls:"mic-confirming", text:"❓ Confirm above ↑"         },
    idle:       { cls:"",              text:"🎙️ Tap to Start"             },
  };
  const s = S[state] || S.idle;
  btn.className = "mic-btn" + (s.cls ? " " + s.cls : "");
  btn.innerHTML = s.text;
}

function showWaveform(show) {
  const wf = document.getElementById("waveform");
  if (wf) wf.style.display = show ? "flex" : "none";
}

/* ═══════════════════════════════════════════════════════════
   WEB AUDIO API — Real Waveform Visualizer
═══════════════════════════════════════════════════════════ */

async function startWaveformVisualizer(stream) {
  try {
    if (!audioContext) {
      audioContext = new (window.AudioContext || window.webkitAudioContext)();
    }
    if (audioContext.state === "suspended") await audioContext.resume();
    if (analyser) { try { analyser.disconnect(); } catch(e){} }

    analyser = audioContext.createAnalyser();
    analyser.fftSize = 256;
    analyser.smoothingTimeConstant = 0.8;

    if (waveformSource) { try { waveformSource.disconnect(); } catch(e){} }
    waveformSource = audioContext.createMediaStreamSource(stream);
    waveformSource.connect(analyser);

    const bars = document.querySelectorAll(".wave-bar");
    const dataArray = new Uint8Array(analyser.frequencyBinCount);
    const barCount  = bars.length;

    function draw() {
      waveframeId = requestAnimationFrame(draw);
      analyser.getByteFrequencyData(dataArray);
      for (let i = 0; i < barCount; i++) {
        const dataIdx = Math.floor(i * (dataArray.length / barCount / 2));
        const value   = dataArray[dataIdx] / 255;
        const height  = Math.max(5, value * 40);
        if (bars[i]) bars[i].style.height = height + "px";
      }
    }
    draw();
    showWaveform(true);
  } catch(e) {
    // Fallback to CSS animation
    document.querySelectorAll(".wave-bar").forEach(b => b.style.height = "");
    showWaveform(true);
  }
}

function stopWaveformVisualizer() {
  // Fix 6: Prevent AudioContext memory leak
  if (typeof audioContext !== "undefined" && audioContext && audioContext.state !== "closed") {
    audioContext.suspend().catch(()=>{});
  }
  if (waveframeId) { cancelAnimationFrame(waveframeId); waveframeId = null; }
  document.querySelectorAll(".wave-bar").forEach(b => b.style.height = "");
  showWaveform(false);
  if (waveformSource) { try { waveformSource.disconnect(); } catch(e){} waveformSource = null; }
}

/* ═══════════════════════════════════════════════════════════
   TTS — Google WaveNet + Browser fallback
═══════════════════════════════════════════════════════════ */

async function aiSpeak(text, lang, onComplete) {
  if (currentAudio) { currentAudio.pause(); currentAudio = null; }
  stopListeningAll();
  setMicBtn("speaking");

  let _done = false;
  const safeComplete = () => {
    if (_done) return; _done = true;
    setMicBtn("idle");
    if (onComplete) setTimeout(onComplete, 550);
  };

  if (useGcpTts) {
    try {
      const resp = await fetch("/api/tts", {
        method:"POST", headers:{"Content-Type":"application/json"},
        body: JSON.stringify({ text, lang })
      });
      const data = await resp.json();
      if (data.audio_base64) {
        const audio = new Audio("data:audio/mp3;base64," + data.audio_base64);
        currentAudio = audio;
        audio.onended = () => { currentAudio=null; safeComplete(); };
        audio.onerror = () => { currentAudio=null; browserSpeak(text,lang,safeComplete); };
        const p = audio.play();
        if (p) p.catch(() => browserSpeak(text,lang,safeComplete));
        return;
      }
    } catch(e) { console.warn("GCP TTS failed:", e); }
  }
  browserSpeak(text, lang, safeComplete);
}

function browserSpeak(text, lang, onComplete) {
  if (speechSynthesis.speaking) speechSynthesis.cancel(); // Fix 9: prevent race
  const msg = new SpeechSynthesisUtterance(text);
  msg.lang  = lang;
  const voices = window.speechSynthesis.getVoices();
  const preferred = voices.filter(v => v.lang.startsWith(lang.split("-")[0]));
  const googleV   = preferred.find(v => v.name.includes("Google"));
  const genderV   = preferred.find(v =>
    voiceGender === "female"
      ? /female|woman|girl/i.test(v.name)
      : /male|man|guy/i.test(v.name)
  );
  msg.voice = googleV || genderV || preferred[0] || null;
  msg.rate  = IS_MOBILE ? 0.85 : 0.90;
  msg.onend = msg.onerror = () => { if (onComplete) onComplete(); };
  window.speechSynthesis.speak(msg);
}

/* ═══════════════════════════════════════════════════════════
   SOCKET.IO — GCP Speech-to-Text
═══════════════════════════════════════════════════════════ */

function initSocket() {
  if (socket || typeof io === "undefined") {
    if (typeof io === "undefined") useGcpStt = false;
    return;
  }
  socket = io({ transports:["websocket"], reconnection:true });
  socket.on("stt_capability", d => { if (!d.use_gcp) useGcpStt = false; });
  socket.on("transcript_result", d => {
    clearSilenceTimer();
    if (d.transcripts?.length) handleTranscript(d.transcripts[0].transcript);
  });
  socket.on("no_speech", () => {
    clearSilenceTimer(); _micGuard=false; isRecording=false;
    if (mode !== "idle") setTimeout(() => startMic(selectedLang), 500);
  });
  socket.on("stt_error", d => {
    clearSilenceTimer(); _micGuard=false; isRecording=false;
    if (d.fallback) useGcpStt = false;
    if (mode !== "idle") setTimeout(() => startMic(selectedLang), 600);
  });
  socket.on("use_browser_stt", () => { useGcpStt = false; });
}

/* ═══════════════════════════════════════════════════════════
   MICROPHONE — GCP + Browser fallback
═══════════════════════════════════════════════════════════ */

async function startGcpMic(lang) {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation:true, noiseSuppression:true, channelCount:1 }
    });
    micStream  = stream;
    const mime = getBestMimeType();
    mediaRecorder = new MediaRecorder(stream, mime ? {mimeType:mime} : {});
    audioChunks   = [];
    isRecording   = true;
    setMicBtn("listening");
    startWaveformVisualizer(stream);

    mediaRecorder.ondataavailable = e => {
      if (e.data.size > 0 && socket?.connected) {
        const reader = new FileReader();
        reader.onload = () => socket.emit("audio_chunk", { audio:reader.result.split(",")[1], lang });
        reader.readAsDataURL(e.data);
      }
    };
    mediaRecorder.onstop = () => {
      stream.getTracks().forEach(t=>t.stop());
      stopWaveformVisualizer();
      isRecording=false; _micGuard=false;
    };
    mediaRecorder.start(IS_MOBILE ? 500 : 250);
    armSilenceTimer(8500);
  } catch(err) {
    _micGuard = false;
    handleMicError(err);
  }
}

function initBrowserStt() {
  if (recognition) return true;
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) {
    if (IS_IOS && IS_SAFARI) setStatus("📱 On iPhone, use <strong>Chrome</strong> for voice.", "warn");
    else setStatus("⚠️ Voice needs Chrome or Edge browser.", "error");
    return false;
  }
  recognition = new SR();
  recognition.continuous     = false;
  recognition.interimResults = true;
  if ("maxAlternatives" in recognition) recognition.maxAlternatives = 3; // Fix 7: Safari guard

  recognition.onstart = () => setMicBtn("listening");

  recognition.onresult = async (event) => {
    let txt = "", isFinal = false;
    for (let i=event.resultIndex; i<event.results.length; i++) {
      // Take highest confidence alternative
      let best = event.results[i][0].transcript;
      for (let j=1; j<event.results[i].length; j++) {
        if (event.results[i][j].confidence > event.results[i][0].confidence)
          best = event.results[i][j].transcript;
      }
      txt += best;
      if (event.results[i].isFinal) isFinal = true;
    }
    txt = txt.trim();
    if (isFinal && txt) {
      clearSilenceTimer();
      isListening=false; _micGuard=false;
      stopWaveformVisualizer();
      setMicBtn("processing");
      setStatus(`🗣️ Heard: <em>"${txt}"</em>`, "speaking");
      await handleTranscript(txt);
    } else if (txt) {
      setStatus(`🎤 <em>"${txt}"</em>`, "speaking");
    }
  };

  recognition.onend = () => {
    clearSilenceTimer();
    // Fix 3: prevent rapid restart loop on Android/Safari
    if (!isListening || mode === "idle") {
      isListening = false; _micGuard = false;
      stopWaveformVisualizer(); setMicBtn("idle");
      return;
    }
    setTimeout(() => {
      try {
        recognition.lang = selectedLang;
        recognition.start();
      } catch(e) {
        isListening = false; _micGuard = false;
        stopWaveformVisualizer(); setMicBtn("idle");
      }
    }, 500);
  };

  recognition.onerror = e => {
    if (e.error==="no-speech") {
      isListening=false; _micGuard=false; stopWaveformVisualizer();
      if (mode !== "idle") promptRetry(selectedLang);
      return;
    }
    if (e.error==="aborted" || e.error==="interrupted") return;
    if (e.error==="not-allowed") {
      isListening=false; _micGuard=false; stopWaveformVisualizer();
      setStatus("⚠️ Microphone access denied. Please allow microphone in browser settings.", "error");
      return;
    }
    isListening=false; _micGuard=false; stopWaveformVisualizer();
    setMicBtn("idle");
  };
  return true;
}

async function startBrowserMic(lang) {
  if (!initBrowserStt()) return;

  // Always update language before starting
  recognition.lang = lang;

  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation:true, noiseSuppression:true, autoGainControl:true }
    });
    micStream = stream;
    startWaveformVisualizer(stream);
  } catch(e) {
    _micGuard=false;
    handleMicError(e);
    return;
  }

  isListening=true; _micGuard=true;
  setMicBtn("listening");
  // Small delay ensures audio context is ready
  setTimeout(() => {
    try {
      recognition.lang = lang; // set again to be sure
      recognition.start();
    } catch(e) {
      isListening=false; _micGuard=false;
    }
  }, 150);
  armSilenceTimer(10000); // 10s silence timeout
}

function armSilenceTimer(ms) {
  clearSilenceTimer();
  _silenceTimer = setTimeout(() => {
    if (isRecording || isListening) {
      stopListeningAll();
      if (mode !== "idle") promptRetry(selectedLang);
    }
  }, ms);
}

function clearSilenceTimer() {
  if (_silenceTimer) { clearTimeout(_silenceTimer); _silenceTimer=null; }
}

function stopRecording() {
  clearSilenceTimer();
  if (mediaRecorder && mediaRecorder.state!=="inactive") {
    try { mediaRecorder.stop(); } catch(e){}
  }
  isRecording=false; _micGuard=false;
}

function stopListeningAll() {
  clearSilenceTimer();
  isListening=false; _micGuard=false;
  try { if (recognition) recognition.stop(); } catch(e){}
  stopRecording();
  stopWaveformVisualizer();
  setMicBtn("idle");
}

function handleMicError(err) {
  stopWaveformVisualizer(); setMicBtn("idle");
  if (err.name==="NotAllowedError"||err.name==="PermissionDeniedError") {
    setStatus("⚠️ Microphone access denied. Please allow microphone in browser settings.", "error");
    addChatBubble("ai","⚠️ Please allow microphone access in your browser settings, then tap again.");
  } else {
    setStatus("⚠️ Could not access microphone. Please check your device.", "error");
  }
}

function startMic(lang) {
  // Fix 5: Stop any running session before starting new one
  if (isListening || isRecording) stopListeningAll();
  if (_micGuard) return;
  _micGuard = true;
  const activeLang = lang || selectedLang || "en-IN";
  if (useGcpStt) {
    if (!socket) initSocket();
    startGcpMic(activeLang);
  } else {
    startBrowserMic(activeLang);
  }
}

function promptRetry(lang) {
  const fields = window.SARATHI_FIELDS || [];
  const label  = (mode==="form" && fields[currentStep])
    ? (fields[currentStep].labels[selectedLang] || fields[currentStep].labels["en-IN"])
    : "that";
  const sys = getSys(lang);
  const msg = sys.retry.replace("{field}", label);
  addChatBubble("ai", `❓ ${msg}`);
  setStatus(`⚠️ ${msg}`, "warn");
  aiSpeak(msg, lang, () => startMic(lang));
}

/* ═══════════════════════════════════════════════════════════
   AI APIs
═══════════════════════════════════════════════════════════ */

async function callApi(path, body) {
  try {
    const r = await fetch(path, {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    return await r.json();
  } catch(e) { console.warn("API error:", path, e); return null; }
}

async function detectLang(text) {
  const r = await callApi("/api/detect_language", {text});
  return r?.detected_lang || "en-IN";
}

async function extractEntity(fieldType, transcript) {
  if (!useGemini) return transcript;
  const r = await callApi("/api/extract_entity", {field_type:fieldType, transcript});
  return r?.value || transcript;
}

async function geminiProcess(userInput) {
  const fields    = window.SARATHI_FIELDS || [];
  const remaining = fields.slice(currentStep).map(f=>f.id);
  return await callApi("/api/gemini_chat", {
    scheme_name:      window.SARATHI_SERVICE_ID || "scheme",
    conversation:     conversationHistory.slice(-6),
    fields_collected: collectedData,
    fields_remaining: remaining,
    user_input:       userInput,
    lang:             selectedLang
  });
}

async function validateField(fieldId, value) {
  const r = await callApi("/api/validate_field", {field_id:fieldId, value});
  return r || {valid:true, message:""};
}

/* ═══════════════════════════════════════════════════════════
   OCR — Document scanning via Tesseract.js
═══════════════════════════════════════════════════════════ */

function setupOcrUpload() {
  const card  = document.getElementById("ocr-upload-card");
  const input = document.getElementById("ocr-file-input");
  if (!card || !input) return;

  card.addEventListener("click", () => input.click());
  card.addEventListener("dragover", e => { e.preventDefault(); card.style.borderColor="#7b1fa2"; });
  card.addEventListener("dragleave", () => { card.style.borderColor=""; });
  card.addEventListener("drop", e => {
    e.preventDefault(); card.style.borderColor="";
    const file = e.dataTransfer?.files?.[0];
    if (file) processOcrFile(file);
  });
  input.addEventListener("change", () => {
    if (input.files?.[0]) processOcrFile(input.files[0]);
  });
}

async function processOcrFile(file) {
  const progress     = document.getElementById("ocr-progress");
  const progressFill = document.getElementById("ocr-progress-fill");
  const progressText = document.getElementById("ocr-progress-text");
  if (progress) progress.style.display = "block";
  if (progressText) progressText.textContent = "📄 Uploading document…";
  if (progressFill) progressFill.style.width = "20%";

  addChatBubble("ai","📄 I can see your document. Let me extract the details automatically…");
  setStatus("🔍 Reading your document with OCR…","info");

  try {
    const formData = new FormData();
    formData.append("file", file);

    if (progressFill) progressFill.style.width = "40%";
    if (progressText) progressText.textContent = "🔍 Extracting text…";

    // Fix 8: OCR 30s timeout to prevent freeze
    const _ocrCtrl = new AbortController();
    const _ocrTimer = setTimeout(() => _ocrCtrl.abort(), 30000);
    const resp = await fetch("/api/ocr_upload", { method:"POST", body:formData, signal:_ocrCtrl.signal });
    clearTimeout(_ocrTimer);
    const data = await resp.json();

    if (progressFill) progressFill.style.width = "80%";

    if (data.fields && Object.keys(data.fields).length > 0) {
      let filled = 0;
      for (const [key, val] of Object.entries(data.fields)) {
        if (val && val.trim()) {
          collectedData[key] = val.trim();
          const el = document.getElementById(key);
          if (el) { el.value = val.trim(); el.classList.add("filled"); }
          filled++;
        }
      }

      if (progressFill) progressFill.style.width = "100%";
      setTimeout(() => { if (progress) progress.style.display="none"; }, 1500);

      const msg = `✅ I extracted ${filled} field(s) from your document! Please check the values below and speak any missing ones.`;
      addChatBubble("ai", msg);
      setStatus(`✅ OCR filled ${filled} fields automatically!`, "success");
      await aiSpeak(msg, selectedLang, () => {
        // Jump to first unfilled field
        const fields = window.SARATHI_FIELDS || [];
        const firstMissing = fields.findIndex(f => !collectedData[f.id]);
        if (firstMissing >= 0) {
          currentStep = firstMissing;
          updateProgress();
          askNextQuestion();
        } else {
          askForConfirmation();
        }
      });
    } else {
      if (progress) progress.style.display="none";
      const msg = "I couldn't extract details from that image. Please make sure the document is clear and well-lit. Let me ask you each detail.";
      addChatBubble("ai", msg);
      await aiSpeak(msg, selectedLang, () => { if (mode==="idle"||mode==="language") startFormFlow(); });
    }
  } catch(e) {
    if (progress) progress.style.display="none";
    console.warn("OCR error:", e);
    addChatBubble("ai","⚠️ Could not read document. Let me ask you the details instead.");
    setStatus("⚠️ OCR failed. Please use voice instead.", "warn");
  }
}

/* ═══════════════════════════════════════════════════════════
   PDF GENERATION
═══════════════════════════════════════════════════════════ */

async function downloadPdf(refId) {
  setStatus("📄 Generating your official application PDF…","info");
  try {
    const resp = await fetch("/api/generate_pdf", {
      method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({ ref_id:refId, data:collectedData,
                              service:window.SARATHI_SERVICE_ID, lang:selectedLang })
    });
    if (!resp.ok) throw new Error("PDF generation failed");
    const blob  = await resp.blob();
    const url   = URL.createObjectURL(blob);
    const a     = document.createElement("a");
    a.href      = url;
    a.download  = `Sarathi_${window.SARATHI_SERVICE_ID}_${refId}.pdf`;
    a.click();
    URL.revokeObjectURL(url);
    setStatus("✅ PDF downloaded successfully!", "success");
  } catch(e) {
    console.warn("PDF error:", e);
    setStatus("⚠️ PDF generation failed. Please contact support.", "warn");
  }
}

/* ═══════════════════════════════════════════════════════════
   LANGUAGE STRINGS
═══════════════════════════════════════════════════════════ */
/* ═══════════════════════════════════════════════════════════
   READBACK — AI reads back what it heard in user's language
═══════════════════════════════════════════════════════════ */

function getReadbackMsg(lang, fieldLabel, value) {
  const templates = {
    "en-IN": `Got it! Your ${fieldLabel} is ${value}.`,
    "te-IN": `అర్థమైంది! మీ ${fieldLabel} ${value} అని నమోదు చేశాను.`,
    "hi-IN": `समझ गया! आपका ${fieldLabel} ${value} दर्ज किया गया।`,
    "ta-IN": `புரிந்தது! உங்கள் ${fieldLabel} ${value} பதிவு செய்யப்பட்டது.`,
    "kn-IN": `ಅರ್ಥವಾಯಿತು! ನಿಮ್ಮ ${fieldLabel} ${value} ದಾಖಲಿಸಲಾಗಿದೆ.`,
    "ml-IN": `മനസ്സിലായി! നിങ്ങളുടെ ${fieldLabel} ${value} രേഖപ്പെടുത്തി.`,
    "bn-IN": `বুঝেছি! আপনার ${fieldLabel} ${value} নথিভুক্ত করা হয়েছে।`,
    "mr-IN": `समजले! तुमचे ${fieldLabel} ${value} नोंदवले गेले.`,
    "gu-IN": `સમજાઈ ગયું! તમારું ${fieldLabel} ${value} નોંધ્યું.`,
    "pa-IN": `ਸਮਝ ਗਿਆ! ਤੁਹਾਡਾ ${fieldLabel} ${value} ਦਰਜ ਕੀਤਾ ਗਿਆ।`,
  };
  return templates[lang] || templates["en-IN"];
}

const SYS = {
  "en-IN": {
    welcome:        "Welcome to Sarathi! Which language would you like to use? Say Telugu, Hindi, Tamil, or English.",
    askCorrect:     "All details filled! Should I submit your application? Say Yes to submit or No to change something.",
    confirmPrompt:  "I heard your {field} as:",
    confirmQuestion:"Is this correct? Say Yes or No.",
    yesWords:       ["yes","correct","right","ok","okay","sure","submit","confirm","fine","done","yep","haan","avunu","ha","sahi","correct"],
    noWords:        ["no","wrong","mistake","change","nope","nah","nahin","kaadu","vaddu","galat","galt"],
    submitting:     "Perfect. Submitting your application now — please wait.",
    submitted:      "Your application has been submitted! Note your reference ID.",
    retryAll:       "Okay, let us start fresh from the beginning.",
    askField:       "Which detail do you want to change? Say the field name, or say 'start again'.",
    notUnderstood:  "Sorry, I did not understand. Please say Yes or No.",
    correctionNotFound: "I could not find that field. Please say the exact field name.",
    fixPrefix:      "Please say the correct value for",
    startAgainWords:["start again","beginning","restart","from start","once more"],
    retry:          "I didn't catch that. Please say your {field} again clearly.",
    eligQ:  ["What is your annual family income? Say the amount in rupees.",
             "Do you own agricultural land? Say yes or no.",
             "What is your caste category? Say General, OBC, SC, ST, or Minority."],
    eligLabel:      ["Annual Income","Land Ownership","Caste Category"],
    checkingElig:   "Checking your eligibility now.",
    langSelected:   "Great! I will guide you in English.",
  },
  "te-IN": {
    welcome:        "సారథికి స్వాగతం! మీ భాషను చెప్పండి — తెలుగు, హిందీ, లేదా ఇంగ్లీష్.",
    askCorrect:     "అన్ని వివరాలు నమోదయ్యాయి! దరఖాస్తు సమర్పించమా? అవును లేదా కాదు చెప్పండి.",
    confirmPrompt:  "మీ {field} ఇలా వినిపించింది:",
    confirmQuestion:"ఇది సరైనదా? అవును లేదా కాదు అనండి.",
    yesWords:       ["అవును","సరే","yes","avunu","ok","right","confirm","సరైనది","ha"],
    noWords:        ["కాదు","no","wrong","kaadu","vaddu","తప్పు"],
    submitting:     "సరే. మీ దరఖాస్తును సమర్పిస్తున్నాను.",
    submitted:      "మీ దరఖాస్తు విజయవంతంగా సమర్పించబడింది!",
    retryAll:       "సరే. మళ్ళీ మొదటి నుండి ప్రారంభిద్దాం.",
    askField:       "ఏ వివరం మార్చాలి? ఫీల్డ్ పేరు చెప్పండి.",
    notUnderstood:  "క్షమించండి, అర్థం కాలేదు. అవును లేదా కాదు అనండి.",
    correctionNotFound: "ఆ ఫీల్డ్ కనుగొనలేకపోయాను.",
    fixPrefix:      "దయచేసి సరైన విలువ చెప్పండి:",
    startAgainWords:["మళ్ళీ ప్రారంభించు","మొదటి నుండి","restart"],
    retry:          "వినిపించలేదు. మీ {field} మళ్ళీ చెప్పండి.",
    eligQ:  ["మీ కుటుంబ వార్షిక ఆదాయం ఎంత?","మీకు వ్యవసాయ భూమి ఉందా?","మీ కుల వర్గం ఏమిటి?"],
    eligLabel:      ["వార్షిక ఆదాయం","భూమి","కుల వర్గం"],
    checkingElig:   "మీ అర్హతను తనిఖీ చేస్తున్నాను.",
    langSelected:   "సరే! నేను తెలుగులో మార్గదర్శనం చేస్తాను.",
  },
  "ta-IN": {
    welcome:        "சாரதியில் வரவேற்கிறோம்! மொழியைத் தேர்வு செய்யுங்கள் — தமிழ், தெலுங்கு, ஹிந்தி, அல்லது ஆங்கிலம்.",
    askCorrect:     "அனைத்து விவரங்களும் நிரப்பப்பட்டன! விண்ணப்பத்தை சமர்ப்பிக்கவா? ஆம் அல்லது இல்லை சொல்லுங்கள்.",
    confirmPrompt:  "உங்கள் {field} இவ்வாறு கேட்கிறேன்:",
    confirmQuestion:"இது சரியா? ஆம் அல்லது இல்லை சொல்லுங்கள்.",
    yesWords:       ["ஆம்","சரி","yes","ok","right","submit","confirm","ha","avunu"],
    noWords:        ["இல்லை","தவறு","no","wrong","change"],
    submitting:     "சரி. உங்கள் விண்ணப்பத்தை சமர்ப்பிக்கிறேன்.",
    submitted:      "உங்கள் விண்ணப்பம் வெற்றிகரமாக சமர்ப்பிக்கப்பட்டது!",
    retryAll:       "சரி. மீண்டும் தொடங்குவோம்.",
    askField:       "எந்த விவரத்தை மாற்ற வேண்டும்? புல பெயரை சொல்லுங்கள்.",
    notUnderstood:  "மன்னிக்கவும், புரியவில்லை. ஆம் அல்லது இல்லை சொல்லுங்கள்.",
    correctionNotFound: "அந்த புலம் கிடைக்கவில்லை.",
    fixPrefix:      "சரியான மதிப்பை சொல்லுங்கள்:",
    startAgainWords:["மீண்டும் தொடங்கு","restart","from start"],
    retry:          "கேட்கவில்லை. உங்கள் {field} மீண்டும் சொல்லுங்கள்.",
    eligQ:  ["குடும்பத்தின் ஆண்டு வருமானம் என்ன?","விவசாய நிலம் உள்ளதா?","சாதி வகை என்ன?"],
    eligLabel:      ["வருடாந்திர வருமானம்","நிலம்","சாதி வகை"],
    checkingElig:   "உங்கள் தகுதியை சரிபார்க்கிறேன்.",
    langSelected:   "சரி! நான் தமிழில் வழிகாட்டுவேன்.",
  },
  "kn-IN": {
    welcome:        "ಸಾರಥಿಗೆ ಸ್ವಾಗತ! ಭಾಷೆ ಆಯ್ಕೆ ಮಾಡಿ — ಕನ್ನಡ, ತೆಲುಗು, ಹಿಂದಿ, ಅಥವಾ ಇಂಗ್ಲಿಷ್.",
    askCorrect:     "ಎಲ್ಲಾ ವಿವರಗಳು ತುಂಬಲಾಗಿದೆ! ಅರ್ಜಿ ಸಲ್ಲಿಸಲೇ? ಹೌದು ಅಥವಾ ಇಲ್ಲ ಹೇಳಿ.",
    confirmPrompt:  "ನಿಮ್ಮ {field} ಹೀಗೆ ಕೇಳಿದೆ:",
    confirmQuestion:"ಇದು ಸರಿಯೇ? ಹೌದು ಅಥವಾ ಇಲ್ಲ ಹೇಳಿ.",
    yesWords:       ["ಹೌದು","ಸರಿ","yes","ok","ha","right","submit"],
    noWords:        ["ಇಲ್ಲ","ತಪ್ಪು","no","wrong","change"],
    submitting:     "ಸರಿ. ನಿಮ್ಮ ಅರ್ಜಿಯನ್ನು ಸಲ್ಲಿಸುತ್ತಿದ್ದೇನೆ.",
    submitted:      "ನಿಮ್ಮ ಅರ್ಜಿ ಯಶಸ್ವಿಯಾಗಿ ಸಲ್ಲಿಸಲಾಗಿದೆ!",
    retryAll:       "ಸರಿ. ಮತ್ತೆ ಪ್ರಾರಂಭಿಸೋಣ.",
    askField:       "ಯಾವ ವಿವರ ಬದಲಾಯಿಸಬೇಕು? ಕ್ಷೇತ್ರ ಹೆಸರು ಹೇಳಿ.",
    notUnderstood:  "ಕ್ಷಮಿಸಿ, ಅರ್ಥವಾಗಲಿಲ್ಲ. ಹೌದು ಅಥವಾ ಇಲ್ಲ ಹೇಳಿ.",
    correctionNotFound: "ಆ ಕ್ಷೇತ್ರ ಕಂಡುಬಂದಿಲ್ಲ.",
    fixPrefix:      "ಸರಿಯಾದ ಮೌಲ್ಯ ಹೇಳಿ:",
    startAgainWords:["ಮತ್ತೆ ಪ್ರಾರಂಭಿಸು","restart","from start"],
    retry:          "ಕೇಳಿಸಲಿಲ್ಲ. ನಿಮ್ಮ {field} ಮತ್ತೆ ಹೇಳಿ.",
    eligQ:  ["ಕುಟುಂಬದ ವಾರ್ಷಿಕ ಆದಾಯ ಎಷ್ಟು?","ಕೃಷಿ ಭೂಮಿ ಇದೆಯೇ?","ಜಾತಿ ವರ್ಗ ಯಾವುದು?"],
    eligLabel:      ["ವಾರ್ಷಿಕ ಆದಾಯ","ಭೂಮಿ","ಜಾತಿ"],
    checkingElig:   "ನಿಮ್ಮ ಅರ್ಹತೆ ಪರಿಶೀಲಿಸುತ್ತಿದ್ದೇನೆ.",
    langSelected:   "ಸರಿ! ನಾನು ಕನ್ನಡದಲ್ಲಿ ಮಾರ್ಗದರ್ಶನ ಮಾಡುತ್ತೇನೆ.",
  },
  "ml-IN": {
    welcome:        "സാരഥിയിലേക്ക് സ്വാഗതം! ഭാഷ തിരഞ്ഞെടുക്കൂ — മലയാളം, തെലുഗു, ഹിന്ദി, ഇംഗ്ലീഷ്.",
    askCorrect:     "എല്ലാ വിവരങ്ങളും പൂർത്തിയായി! അപേക്ഷ സമർപ്പിക്കണോ? അതെ അല്ലെങ്കിൽ ഇല്ല പറയൂ.",
    confirmPrompt:  "നിങ്ങളുടെ {field} ഇങ്ങനെ കേട്ടു:",
    confirmQuestion:"ഇത് ശരിയാണോ? അതെ അല്ലെങ്കിൽ ഇല്ല പറയൂ.",
    yesWords:       ["അതെ","ശരി","yes","ok","ha","right","submit"],
    noWords:        ["ഇല്ല","തെറ്റ്","no","wrong","change"],
    submitting:     "ശരി. നിങ്ങളുടെ അപേക്ഷ സമർപ്പിക്കുന്നു.",
    submitted:      "നിങ്ങളുടെ അപേക്ഷ വിജയകരമായി സമർപ്പിച്ചു!",
    retryAll:       "ശരി. വീണ്ടും തുടങ്ങാം.",
    askField:       "ഏത് വിവരം മാറ്റണം? ഫീൽഡ് പേര് പറയൂ.",
    notUnderstood:  "ക്ഷമിക്കൂ, മനസ്സിലായില്ല. അതെ അല്ലെങ്കിൽ ഇല്ല പറയൂ.",
    correctionNotFound: "ആ ഫീൽഡ് കണ്ടെത്തിയില്ല.",
    fixPrefix:      "ശരിയായ മൂല്യം പറയൂ:",
    startAgainWords:["വീണ്ടും തുടങ്ങൂ","restart","from start"],
    retry:          "കേൾക്കുന്നില്ല. നിങ്ങളുടെ {field} വീണ്ടും പറയൂ.",
    eligQ:  ["കുടുംബത്തിന്റെ വാർഷിക വരുമാനം?","കൃഷിഭൂമി ഉണ്ടോ?","ജാതി വിഭാഗം?"],
    eligLabel:      ["വാർഷിക വരുമാനം","ഭൂമി","ജാതി"],
    checkingElig:   "നിങ്ങളുടെ യോഗ്യത പരിശോധിക്കുന്നു.",
    langSelected:   "ശരി! ഞാൻ മലയാളത്തിൽ നയിക്കുന്നു.",
  },
  "bn-IN": {
    welcome:        "সারথিতে স্বাগতম! ভাষা বেছে নিন — বাংলা, তেলেগু, হিন্দি, বা ইংরেজি।",
    askCorrect:     "সব তথ্য পূর্ণ হয়েছে! আবেদন জমা দেব? হ্যাঁ বা না বলুন।",
    confirmPrompt:  "আপনার {field} এভাবে শুনলাম:",
    confirmQuestion:"এটা কি ঠিক? হ্যাঁ বা না বলুন।",
    yesWords:       ["হ্যাঁ","ঠিক","yes","ok","ha","right","submit"],
    noWords:        ["না","ভুল","no","wrong","change"],
    submitting:     "ঠিক আছে। আপনার আবেদন জমা দিচ্ছি।",
    submitted:      "আপনার আবেদন সফলভাবে জমা হয়েছে!",
    retryAll:       "ঠিক আছে। আবার শুরু করা যাক।",
    askField:       "কোন তথ্য পরিবর্তন করতে চান? ফিল্ডের নাম বলুন।",
    notUnderstood:  "ক্ষমা করবেন, বুঝিনি। হ্যাঁ বা না বলুন।",
    correctionNotFound: "সেই ফিল্ড পাওয়া যায়নি।",
    fixPrefix:      "সঠিক মান বলুন:",
    startAgainWords:["আবার শুরু","restart","from start"],
    retry:          "শুনতে পাইনি। আপনার {field} আবার বলুন।",
    eligQ:  ["পরিবারের বার্ষিক আয়?","কৃষি জমি আছে?","জাতি শ্রেণী?"],
    eligLabel:      ["বার্ষিক আয়","জমি","জাতি"],
    checkingElig:   "আপনার যোগ্যতা যাচাই করছি।",
    langSelected:   "ঠিক আছে! আমি বাংলায় গাইড করব।",
  },
  "mr-IN": {
    welcome:        "सारथीमध्ये आपले स्वागत! भाषा निवडा — मराठी, तेलुगू, हिंदी, किंवा इंग्रजी.",
    askCorrect:     "सर्व माहिती भरली! अर्ज सादर करायचा? होय किंवा नाही सांगा.",
    confirmPrompt:  "तुमचे {field} असे ऐकले:",
    confirmQuestion:"हे बरोबर आहे का? होय किंवा नाही सांगा.",
    yesWords:       ["होय","बरोबर","yes","ok","ha","right","submit","हो"],
    noWords:        ["नाही","चुकीचे","no","wrong","change"],
    submitting:     "ठीक आहे. तुमचा अर्ज सादर करत आहे.",
    submitted:      "तुमचा अर्ज यशस्वीरित्या सादर झाला!",
    retryAll:       "ठीक आहे. पुन्हा सुरू करूया.",
    askField:       "कोणती माहिती बदलायची? फील्डचे नाव सांगा.",
    notUnderstood:  "माफ करा, समजले नाही. होय किंवा नाही सांगा.",
    correctionNotFound: "ते फील्ड सापडले नाही.",
    fixPrefix:      "योग्य मूल्य सांगा:",
    startAgainWords:["पुन्हा सुरू","restart","from start"],
    retry:          "ऐकू आले नाही. तुमचे {field} पुन्हा सांगा.",
    eligQ:  ["कुटुंबाचे वार्षिक उत्पन्न?","शेती जमीन आहे का?","जात श्रेणी?"],
    eligLabel:      ["वार्षिक उत्पन्न","जमीन","जात"],
    checkingElig:   "तुमची पात्रता तपासत आहे.",
    langSelected:   "ठीक आहे! मी मराठीत मार्गदर्शन करेन.",
  },
  "gu-IN": {
    welcome:        "સારથીમાં આપનું સ્વાગત! ભાષા પસંદ કરો — ગુજરાતી, તેલુગુ, હિન્દી, અથવા અંગ્રેજી.",
    askCorrect:     "બધી વિગતો ભરાઈ! અરજી સબમિટ કરું? હા અથવા ના કહો.",
    confirmPrompt:  "તમારો {field} આ રીતે સાંભળ્યો:",
    confirmQuestion:"શું આ સાચું છે? હા અથવા ના કહો.",
    yesWords:       ["હા","સાચું","yes","ok","ha","right","submit"],
    noWords:        ["ના","ખોટું","no","wrong","change"],
    submitting:     "ઠીક છે. તમારી અરજી સબમિટ કરી રહ્યો છું.",
    submitted:      "તમારી અરજી સફળતાપૂર્વક સબમિટ થઈ!",
    retryAll:       "ઠીક છે. ફરીથી શરૂ કરીએ.",
    askField:       "કઈ વિગત બદલવી છે? ફીલ્ડ નામ કહો.",
    notUnderstood:  "માફ કરો, સમજ ન પડ્યું. હા અથવા ના કહો.",
    correctionNotFound: "તે ફીલ્ડ મળ્યું નહિ.",
    fixPrefix:      "સાચો મૂલ્ય કહો:",
    startAgainWords:["ફરી શરૂ","restart","from start"],
    retry:          "સાંભળ્યું નહિ. તમારો {field} ફરી કહો.",
    eligQ:  ["કુટુંબની વાર્ષિક આવક?","ખેતીની જમીન છે?","જ્ઞાતિ વર્ગ?"],
    eligLabel:      ["વાર્ષિક આવક","જમીન","જ્ઞાતિ"],
    checkingElig:   "તમારી પાત્રતા ચકાસી રહ્યો છું.",
    langSelected:   "ઠીક છે! હું ગુજરાતીમાં માર્ગદર્શન આપીશ.",
  },
  "pa-IN": {
    welcome:        "ਸਾਰਥੀ ਵਿੱਚ ਤੁਹਾਡਾ ਸੁਆਗਤ ਹੈ! ਭਾਸ਼ਾ ਚੁਣੋ — ਪੰਜਾਬੀ, ਤੇਲੁਗੂ, ਹਿੰਦੀ, ਜਾਂ ਅੰਗਰੇਜ਼ੀ।",
    askCorrect:     "ਸਾਰੀ ਜਾਣਕਾਰੀ ਭਰੀ ਗਈ! ਅਰਜ਼ੀ ਜਮ੍ਹਾ ਕਰਾਂ? ਹਾਂ ਜਾਂ ਨਹੀਂ ਕਹੋ।",
    confirmPrompt:  "ਤੁਹਾਡਾ {field} ਇਸ ਤਰ੍ਹਾਂ ਸੁਣਿਆ:",
    confirmQuestion:"ਕੀ ਇਹ ਸਹੀ ਹੈ? ਹਾਂ ਜਾਂ ਨਹੀਂ ਕਹੋ।",
    yesWords:       ["ਹਾਂ","ਸਹੀ","yes","ok","ha","right","submit"],
    noWords:        ["ਨਹੀਂ","ਗਲਤ","no","wrong","change"],
    submitting:     "ਠੀਕ ਹੈ। ਤੁਹਾਡੀ ਅਰਜ਼ੀ ਜਮ੍ਹਾ ਕਰ ਰਿਹਾ ਹਾਂ।",
    submitted:      "ਤੁਹਾਡੀ ਅਰਜ਼ੀ ਸਫਲਤਾਪੂਰਵਕ ਜਮ੍ਹਾ ਹੋ ਗਈ!",
    retryAll:       "ਠੀਕ ਹੈ। ਫਿਰ ਤੋਂ ਸ਼ੁਰੂ ਕਰੀਏ।",
    askField:       "ਕਿਹੜੀ ਜਾਣਕਾਰੀ ਬਦਲਣੀ ਹੈ? ਫੀਲਡ ਦਾ ਨਾਮ ਕਹੋ।",
    notUnderstood:  "ਮਾਫ਼ ਕਰਨਾ, ਸਮਝ ਨਹੀਂ ਆਈ। ਹਾਂ ਜਾਂ ਨਹੀਂ ਕਹੋ।",
    correctionNotFound: "ਉਹ ਫੀਲਡ ਨਹੀਂ ਮਿਲੀ।",
    fixPrefix:      "ਸਹੀ ਮੁੱਲ ਦੱਸੋ:",
    startAgainWords:["ਫਿਰ ਸ਼ੁਰੂ","restart","from start"],
    retry:          "ਸੁਣਿਆ ਨਹੀਂ। ਆਪਣਾ {field} ਦੁਬਾਰਾ ਕਹੋ।",
    eligQ:  ["ਪਰਿਵਾਰ ਦੀ ਸਾਲਾਨਾ ਆਮਦਨ?","ਖੇਤੀ ਜ਼ਮੀਨ ਹੈ?","ਜਾਤੀ ਵਰਗ?"],
    eligLabel:      ["ਸਾਲਾਨਾ ਆਮਦਨ","ਜ਼ਮੀਨ","ਜਾਤੀ"],
    checkingElig:   "ਤੁਹਾਡੀ ਯੋਗਤਾ ਜਾਂਚ ਰਿਹਾ ਹਾਂ।",
    langSelected:   "ਠੀਕ ਹੈ! ਮੈਂ ਪੰਜਾਬੀ ਵਿੱਚ ਮਾਰਗਦਰਸ਼ਨ ਕਰਾਂਗਾ।",
  },
  "hi-IN": {
    welcome:        "सारथी में आपका स्वागत! भाषा बताएं — तेलुगु, हिन्दी, या अंग्रेजी।",
    askCorrect:     "सभी विवरण भरे गए! आवेदन जमा करें? हाँ या नहीं बोलें।",
    confirmPrompt:  "मैंने आपका {field} सुना:",
    confirmQuestion:"क्या यह सही है? हाँ या नहीं।",
    yesWords:       ["हाँ","सही","yes","ha","haan","ok","thik","sahi","bilkul","submit"],
    noWords:        ["नहीं","galat","no","wrong","nahin","गलत"],
    submitting:     "ठीक है। आपका आवेदन जमा कर रहे हैं।",
    submitted:      "आपका आवेदन सफलतापूर्वक जमा हो गया!",
    retryAll:       "ठीक है, फिर से शुरू करते हैं।",
    askField:       "कौन सी जानकारी बदलनी है? फ़ील्ड का नाम बोलें।",
    notUnderstood:  "माफ़ करें, समझ नहीं आया। हाँ या नहीं बोलें।",
    correctionNotFound: "वह फ़ील्ड नहीं मिली।",
    fixPrefix:      "कृपया सही मान बताएं:",
    startAgainWords:["शुरू से","फिर से","restart"],
    retry:          "सुनाई नहीं दिया। अपना {field} फिर से बोलें।",
    eligQ:  ["परिवार की सालाना आय?","कृषि भूमि है?","जाति श्रेणी?"],
    eligLabel:      ["वार्षिक आय","भूमि","जाति"],
    checkingElig:   "आपकी पात्रता जाँच रहे हैं।",
    langSelected:   "ठीक है! मैं हिन्दी में मार्गदर्शन करूँगा।",
  }
};

function getSys(lang) { return SYS[lang] || SYS["en-IN"]; }

function matchIntent(text, words) {
  return words.some(w => text.includes(w.toLowerCase()) || text.startsWith(w.toLowerCase()));
}

/* ═══════════════════════════════════════════════════════════
   ELIGIBILITY CHECK
═══════════════════════════════════════════════════════════ */

function startEligibilityFlow() {
  mode="eligibility"; eligibilityStep=0; eligibilityAnswers={};
  const q = getSys(selectedLang).eligQ[0];
  addChatBubble("ai",q);
  aiSpeak(q, selectedLang, () => startMic(selectedLang));
}

async function handleEligibilityAnswer(text) {
  const sys = getSys(selectedLang);
  addChatBubble("user", text);
  eligibilityAnswers[sys.eligLabel[eligibilityStep]] = text;
  eligibilityStep++;
  if (eligibilityStep < sys.eligQ.length) {
    const q = sys.eligQ[eligibilityStep];
    addChatBubble("ai", q);
    await aiSpeak(q, selectedLang, () => startMic(selectedLang));
  } else {
    const checking = sys.checkingElig;
    addChatBubble("ai", checking);
    await aiSpeak(checking, selectedLang);
    await checkEligibility();
  }
}

async function checkEligibility() {
  setMicBtn("processing");
  const result = await callApi("/api/eligibility_check", {
    scheme_name: window.SARATHI_SERVICE_ID || "scheme",
    answers: eligibilityAnswers
  });

  // Fix 10: null guard
  if (!result) { addChatBubble("ai","⚠️ Server unavailable. Please try again."); return; }
  const banner = document.getElementById("eligibility-banner");
  if (banner && result) {
    banner.style.display = "block";
    banner.className = result.eligible ? "elig-banner eligible" : "elig-banner not-eligible";
    banner.innerHTML = `<strong>${result.eligible ? "✅ You are eligible!" : "⚠️ Eligibility issue"}</strong> ${result.reason}
      ${result.also_qualifies?.length ? `<br><small>Also eligible for: <strong>${result.also_qualifies.join(", ")}</strong></small>` : ""}`;
  }

  const speakMsg = result?.eligible
    ? result.reason + " Let me now fill your application."
    : result?.reason + " Would you like to continue anyway?";

  addChatBubble("ai", speakMsg);
  if (result?.eligible) {
    await aiSpeak(speakMsg, selectedLang, () => startFormFlow());
  } else {
    await aiSpeak(speakMsg, selectedLang, () => startMic(selectedLang));
    mode = "confirm_proceed";
  }
}

/* ═══════════════════════════════════════════════════════════
   FORM FLOW — Conversational one-at-a-time
═══════════════════════════════════════════════════════════ */

function startFormFlow() {
  mode="form"; currentStep=0; retryCount=0;
  collectedData={}; conversationHistory=[];
  updateProgress();
  document.querySelectorAll(".field-item-input").forEach(el => {
    el.value=""; el.classList.remove("filled","masked");
  });
  hideCurrentField();
  askNextQuestion();
}

async function askNextQuestion() {
  const fields = window.SARATHI_FIELDS || [];
  if (currentStep >= fields.length) { await askForConfirmation(); return; }
  const field    = fields[currentStep];
  const question = field.questions[selectedLang] || field.questions["en-IN"];
  updateProgress();
  showCurrentField(field);
  addChatBubble("ai", question);
  conversationHistory.push({role:"sarathi", text:question});
  await aiSpeak(question, selectedLang, () => startMic(selectedLang));
}

async function processFieldInput(text) {
  const fields = window.SARATHI_FIELDS || [];
  const field  = fields[currentStep];
  if (!field) { await askForConfirmation(); return; }

  let value = text;
  setMicBtn("processing");

  // Gemini-first: try to extract all remaining fields at once
  if (useGemini) {
    const typing = addAiTyping();
    const geminiResult = await geminiProcess(text);
    removeTypingBubble();
    if (geminiResult?.extracted_fields && Object.keys(geminiResult.extracted_fields).length > 0) {
      for (const [key, val] of Object.entries(geminiResult.extracted_fields)) {
        if (fields.find(f=>f.id===key) && val) {
          collectedData[key] = val;
          const el = document.getElementById(key);
          if (el) { el.value = maskSensitive(key, val); el.classList.add("filled"); }
        }
      }
      const nextMissing = fields.findIndex(f => !collectedData[f.id]);
      currentStep = nextMissing === -1 ? fields.length : nextMissing;
      updateProgress();
      if (currentStep < fields.length) {
        // Readback all newly filled fields
        const filledNow = Object.keys(geminiResult.extracted_fields || {});
        if (filledNow.length > 0) {
          const filledText = filledNow.map(k => {
            const f = fields.find(ff=>ff.id===k);
            const label = f?.labels[selectedLang] || f?.labels["en-IN"] || k;
            return `${label}: ${maskSensitive(k, geminiResult.extracted_fields[k])}`;
          }).join(". ");
          const readbackMsg = getReadbackMsg(selectedLang, filledText, "");
          const cleanMsg    = readbackMsg.replace(": .", ".");
          addChatBubble("ai", "✅ " + cleanMsg);
        }
        const nextQ = geminiResult.next_question
          || fields[currentStep].questions[selectedLang]
          || fields[currentStep].questions["en-IN"];
        addChatBubble("ai", nextQ);
        conversationHistory.push({role:"sarathi",text:nextQ});
        await aiSpeak(nextQ, selectedLang, () => { showCurrentField(fields[currentStep]); startMic(selectedLang); });
      } else {
        await askForConfirmation();
      }
      return;
    }
  }

  // Entity extraction
  if (field.isNumeric) {
    value = await extractEntity(field.id + "_number", text);
    if (!value || !/\d/.test(value)) {
      value = text.replace(/\D/g,"");
      if (value.length < 4) value = wordsToDigits(text.toLowerCase()) || value;
    }
  } else if (field.isId) {
    value = (await extractEntity(field.id, text)).replace(/\s+/g,"").toUpperCase();
  } else if (field.isName) {
    value = await extractEntity("name", text);
    value = value.split(" ").map(w=>w.charAt(0).toUpperCase()+w.slice(1).toLowerCase()).join(" ");
  }

  // AI Confirmation Layer for sensitive fields
  if (field.isNumeric || field.isId) {
    const validation = await validateField(field.id, value);
    if (!validation.valid && validation.message) {
      retryCount++;
      const msg = retryCount >= 2
        ? `Let me help. Please say your ${field.labels[selectedLang]||field.labels["en-IN"]} clearly, one digit at a time.`
        : validation.message + " Please try again.";
      addChatBubble("ai", `⚠️ ${msg}`);
      setStatus(`⚠️ ${msg}`, "warn");
      await aiSpeak(msg, selectedLang, () => startMic(selectedLang));
      return;
    }

    // Show confirmation for sensitive fields
    const fieldLabel = field.labels[selectedLang] || field.labels["en-IN"];
    const displayVal = maskSensitive(field.id, value);
    const sys = getSys(selectedLang);
    const confirmMsg = `${sys.confirmPrompt.replace("{field}", fieldLabel)} ${displayVal}. ${sys.confirmQuestion}`;
    addChatBubble("ai", confirmMsg);

    pendingValue    = value;
    pendingFieldIdx = currentStep;
    setMicBtn("confirming");
    mode = "field_confirm";
    await aiSpeak(confirmMsg, selectedLang, () => startMic(selectedLang));
    return;
  }

  // All fields get confirmed — AI reads back what it heard
  const fieldLabel  = field.labels[selectedLang] || field.labels["en-IN"];
  const displayVal  = maskSensitive(field.id, value);
  const sys         = getSys(selectedLang);
  const confirmMsg  = `${sys.confirmPrompt.replace("{field}", fieldLabel)} ${displayVal}. ${sys.confirmQuestion}`;

  addChatBubble("ai", confirmMsg);
  pendingValue    = value;
  pendingFieldIdx = currentStep;
  setMicBtn("confirming");
  mode = "field_confirm";
  await aiSpeak(confirmMsg, selectedLang, () => startMic(selectedLang));
}

async function finalizeField(field, value) {
  retryCount = 0;
  collectedData[field.id] = value;
  const el = document.getElementById(field.id);
  if (el) {
    el.value = maskSensitive(field.id, value);
    el.classList.add("filled");
  }
  updateCurrentFieldValue(field.id, value);
  addChatBubble("user", maskSensitive(field.id, value));
  conversationHistory.push({role:"user", text:value});

  // Readback: AI confirms what it heard in user's language
  const fieldLabel  = field.labels[selectedLang] || field.labels["en-IN"];
  const displayVal  = maskSensitive(field.id, value);
  const readbackMsg = getReadbackMsg(selectedLang, fieldLabel, displayVal);
  addChatBubble("ai", "✅ " + readbackMsg);

  currentStep++;
  updateProgress();
  markFieldFilled(field.id);

  // Speak readback THEN ask next question
  await aiSpeak(readbackMsg, selectedLang, () => {
    const fields = window.SARATHI_FIELDS || [];
    if (currentStep < fields.length) askNextQuestion(); else askForConfirmation();
  });
}

async function askForConfirmation() {
  hideCurrentField();
  mode="confirm"; updateProgress();
  const fields = window.SARATHI_FIELDS || [];
  const sys    = getSys(selectedLang);

  // Build summary text for display
  const summaryLines = fields.map(f => {
    const label = f.labels[selectedLang] || f.labels["en-IN"];
    const val   = maskSensitive(f.id, collectedData[f.id] || "—");
    return `<strong>${label}:</strong> ${val}`;
  }).join("<br>");

  addChatBubble("ai", `📋 <strong>Your details:</strong><br>${summaryLines}`);

  // Build spoken summary — read each field aloud in user language
  const spokenSummary = fields.map(f => {
    const label = f.labels[selectedLang] || f.labels["en-IN"];
    const val   = maskSensitive(f.id, collectedData[f.id] || "not filled");
    return `${label}: ${val}`;
  }).join(". ");

  const summaryIntro = getSummaryIntro(selectedLang);
  const fullSpeech   = `${summaryIntro} ${spokenSummary}. ${sys.askCorrect}`;

  addChatBubble("ai", sys.askCorrect);
  setStatus(`🤖 ${sys.askCorrect}`, "info");

  // Speak full summary then ask yes/no
  await aiSpeak(fullSpeech, selectedLang, () => startMic(selectedLang));
}

function getSummaryIntro(lang) {
  const intros = {
    "en-IN": "Here are the details I have collected.",
    "te-IN": "మీరు అందించిన వివరాలు ఇవి.",
    "hi-IN": "आपने जो जानकारी दी है वह इस प्रकार है।",
    "ta-IN": "நீங்கள் வழங்கிய விவரங்கள் இவை.",
    "kn-IN": "ನೀವು ನೀಡಿದ ವಿವರಗಳು ಇವು.",
    "ml-IN": "നിങ്ങൾ നൽകിയ വിവരങ്ങൾ ഇതാണ്.",
    "bn-IN": "আপনি যে তথ্য দিয়েছেন তা এখানে।",
    "mr-IN": "तुम्ही दिलेली माहिती अशी आहे.",
    "gu-IN": "તમે આપેલી વિગતો આ છે.",
    "pa-IN": "ਤੁਸੀਂ ਜੋ ਜਾਣਕਾਰੀ ਦਿੱਤੀ ਉਹ ਇਹ ਹੈ।",
  };
  return intros[lang] || intros["en-IN"];
}

/* ═══════════════════════════════════════════════════════════
   FORM SUBMISSION
═══════════════════════════════════════════════════════════ */

async function submitFinalForm() {
  stopListeningAll();
  const sys = getSys(selectedLang);
  addChatBubble("ai","⏳ " + sys.submitting);
  setStatus("⏳ Submitting securely…","info"); setMicBtn("processing");

  const resp = await callApi("/api/submit_form", {
    service: window.SARATHI_SERVICE_ID, lang:selectedLang, data:collectedData
  });

  if (resp?.success) {
    setStatus(`✅ ${sys.submitted}`, "success");
    addChatBubble("ai", `🎉 ${sys.submitted} Reference: <strong>${resp.ref_id}</strong>`);

    const sb = document.getElementById("success-banner");
    if (sb) { sb.style.display="block"; sb.innerHTML=`🎉 Application submitted! Reference: <strong>${resp.ref_id}</strong>`; }

    if (resp.qr_code) {
      const qrDiv = document.getElementById("qr-container");
      if (qrDiv) {
        qrDiv.style.display="block";
        qrDiv.innerHTML=`<div class="qr-card">
          <div class="qr-card-title">📱 Your Application QR Code — Save or Screenshot</div>
          <img src="data:image/png;base64,${resp.qr_code}" alt="QR" class="qr-img">
          <div class="qr-ref">${resp.ref_id}</div>
          <div class="qr-actions">
            <button class="qr-btn qr-btn-pdf" onclick="downloadPdf('${resp.ref_id}')">📄 Download PDF Receipt</button>
            <button class="qr-btn qr-btn-save" onclick="saveQr('${resp.qr_code}')">💾 Save QR</button>
          </div>
          <div class="mt-8" style="font-size:12px;color:#6b7280;">Show at any government office</div>
        </div>`;
      }
    }
    const refSpoken = resp.ref_id.split("").join(" ");
    await aiSpeak(`${sys.submitted} Your reference ID is ${refSpoken}`, selectedLang);
    mode="idle"; setMicBtn("idle");
  } else {
    const errMsg = resp?.error || "Submission failed. Please try again.";
    addChatBubble("ai","❌ " + errMsg);
    setStatus("❌ " + errMsg,"error");
    await aiSpeak(errMsg, selectedLang);
    mode="idle"; setMicBtn("idle");
  }
}

function saveQr(b64) {
  const a = document.createElement("a");
  a.href = "data:image/png;base64," + b64;
  a.download = "Sarathi_QR.png"; a.click();
}

/* ═══════════════════════════════════════════════════════════
   MAIN TRANSCRIPT HANDLER
═══════════════════════════════════════════════════════════ */

async function handleTranscript(text) {
  stopListeningAll();
  const lower = text.toLowerCase().trim();
  conversationHistory.push({role:"user", text});
  console.log(`[Mode:${mode}] "${text}"`);

  /* LANGUAGE SELECTION */
  if (mode === "language") {
    const detected = await detectLang(text);
    if      (/(telugu|తెలుగు)/i.test(text))    selectedLang = "te-IN";
    else if (/(hindi|हिन्दी|हिंदी)/i.test(text)) selectedLang = "hi-IN";
    else if (/(tamil|தமிழ்)/i.test(text))        selectedLang = "ta-IN";
    else if (/(kannada|ಕನ್ನಡ)/i.test(text))      selectedLang = "kn-IN";
    else if (/malayalam/i.test(text))             selectedLang = "ml-IN";
    else if (/(bengali|বাংলা)/i.test(text))       selectedLang = "bn-IN";
    else if (/(marathi|मराठी)/i.test(text))       selectedLang = "mr-IN";
    else if (/gujarati/i.test(text))              selectedLang = "gu-IN";
    else if (/punjabi/i.test(text))               selectedLang = "pa-IN";
    else selectedLang = detected;

    document.querySelectorAll(".lang-pill").forEach(p => p.classList.remove("active"));
    const LANG_NATIVE = {"te-IN":"తెలుగు","hi-IN":"हिन्दी","en-IN":"English","ta-IN":"தமிழ்","kn-IN":"ಕನ್ನಡ"};
    const nativeName = LANG_NATIVE[selectedLang] || "English";
    const pill = [...document.querySelectorAll(".lang-pill")].find(p=>p.textContent.trim()===nativeName);
    if (pill) pill.classList.add("active");

    const confirmMsg = getSys(selectedLang).langSelected;
    addChatBubble("user", text);
    addChatBubble("ai", confirmMsg);
    await aiSpeak(confirmMsg, selectedLang, () => {
      if (useGemini && window.SARATHI_CHECK_ELIGIBILITY !== false) startEligibilityFlow();
      else startFormFlow();
    });
    return;
  }

  /* ELIGIBILITY */
  if (mode === "eligibility") { await handleEligibilityAnswer(text); return; }

  /* CONFIRM PROCEED AFTER INELIGIBLE */
  if (mode === "confirm_proceed") {
    const sys = getSys(selectedLang);
    if (matchIntent(lower, sys.yesWords)) startFormFlow();
    else {
      addChatBubble("ai","Okay. Returning to dashboard.");
      await aiSpeak("Okay. Returning to dashboard.", selectedLang, () => { window.location.href="/"; });
    }
    return;
  }

  /* FIELD CONFIRMATION (sensitive fields) */
  if (mode === "field_confirm") {
    const sys = getSys(selectedLang);
    if (matchIntent(lower, sys.yesWords)) {
      mode = "form";
      hideConfirmCard();
      const fields = window.SARATHI_FIELDS || [];
      await finalizeField(fields[pendingFieldIdx], pendingValue);
    } else if (matchIntent(lower, sys.noWords)) {
      hideConfirmCard();
      // Fix 2: save BEFORE clearing — was using null idx after clearing
      const retryField = (window.SARATHI_FIELDS||[])[pendingFieldIdx ?? currentStep];
      const retryMsg   = sys.retry.replace("{field}", retryField?.labels[selectedLang] || "that");
      pendingValue = null; pendingFieldIdx = null;
      mode = "form";
      addChatBubble("ai", retryMsg);
      await aiSpeak(retryMsg, selectedLang, () => startMic(selectedLang));
    } else {
      await aiSpeak(sys.notUnderstood, selectedLang, () => startMic(selectedLang));
    }
    return;
  }

  /* FORM FILL */
  if (mode === "form") {
    addChatBubble("user", text);
    await processFieldInput(text);
    return;
  }

  /* FINAL CONFIRMATION */
  if (mode === "confirm") {
    const sys = getSys(selectedLang);
    if (matchIntent(lower, sys.yesWords)) {
      addChatBubble("user", "✅ Yes, submit!");
      await aiSpeak(sys.submitting, selectedLang, () => submitFinalForm());
    } else if (matchIntent(lower, sys.noWords)) {
      addChatBubble("user", "❌ No, change something");
      mode = "correction";
      const msg = sys.askField;
      addChatBubble("ai", msg);
      await aiSpeak(msg, selectedLang, () => startMic(selectedLang));
    } else {
      await aiSpeak(sys.notUnderstood, selectedLang, () => startMic(selectedLang));
    }
    return;
  }

  /* FIELD CORRECTION */
  if (mode === "correction") {
    const sys = getSys(selectedLang);
    if (matchIntent(lower, sys.startAgainWords)) {
      addChatBubble("user","Start again"); await aiSpeak(sys.retryAll, selectedLang, () => startFormFlow()); return;
    }
    const idx = matchFieldIdx(lower);
    if (idx >= 0) {
      fixingFieldIndex=idx; mode="fixField";
      const label = window.SARATHI_FIELDS[idx].labels[selectedLang] || window.SARATHI_FIELDS[idx].labels["en-IN"];
      const msg = `${getSys(selectedLang).fixPrefix} ${label}`;
      addChatBubble("ai", msg);
      await aiSpeak(msg, selectedLang, () => startMic(selectedLang));
    } else {
      await aiSpeak(sys.correctionNotFound, selectedLang, () => startMic(selectedLang));
    }
    return;
  }

  /* FIX A FIELD */
  if (mode === "fixField") {
    const field = window.SARATHI_FIELDS[fixingFieldIndex];
    let value = text;
    if (field.isNumeric) {
      value = await extractEntity(field.id+"_number", text);
      if (!value||!/\d/.test(value)) { value=text.replace(/\D/g,""); }
    }
    if (field.isId) value = (await extractEntity(field.id,text)).replace(/\s+/g,"").toUpperCase();
    const valid = await validateField(field.id, value);
    if (!valid.valid && valid.message) {
      addChatBubble("ai","⚠️ "+valid.message);
      await aiSpeak(valid.message, selectedLang, () => startMic(selectedLang)); return;
    }
    collectedData[field.id]=value;
    const el = document.getElementById(field.id);
    if (el) { el.value=maskSensitive(field.id,value); el.classList.add("filled"); }
    fixingFieldIndex=-1; mode="form";
    setTimeout(() => askForConfirmation(), 350);
    return;
  }

  /* RECOMMENDER */
  if (mode === "recommender") { await handleRecommenderInput(text); return; }
}

/* ── Field match helper ───────────────────────────────────── */
function matchFieldIdx(text) {
  const fields = window.SARATHI_FIELDS || [];
  for (let i=0;i<fields.length;i++) {
    const f = fields[i];
    if (f.labels["en-IN"] && text.includes(f.labels["en-IN"].toLowerCase())) return i;
    if (f.labels[selectedLang] && text.includes(f.labels[selectedLang].toLowerCase())) return i;
    if (f.matchKeywords?.some(k=>text.includes(k.toLowerCase()))) return i;
  }
  return -1;
}

/* ── Digit converter ──────────────────────────────────────── */
function wordsToDigits(text) {
  const map = {
    zero:0,one:1,two:2,three:3,four:4,five:5,six:6,seven:7,eight:8,nine:9,
    "సున్న":0,"ఒకటి":1,"రెండు":2,"మూడు":3,"నాలుగు":4,"అయిదు":5,"ఆరు":6,"ఏడు":7,"ఎనిమిది":8,"తొమ్మిది":9,
    "शून्य":0,"एक":1,"दो":2,"तीन":3,"चार":4,"पाँच":5,"छह":6,"सात":7,"आठ":8,"नौ":9
  };
  return text.trim().split(/\s+/).map(w => w in map ? map[w] : "").join("");
}

/* ═══════════════════════════════════════════════════════════
   SCHEME RECOMMENDER
═══════════════════════════════════════════════════════════ */

async function startSchemeRecommender() {
  mode = "recommender";
  const msg = "Please describe your situation — for example: 'I am a farmer in Telangana with 2 acres of land, my annual income is 80,000 rupees, and I have a ration card.'";
  addChatBubble("ai", msg);
  setStatus("🤖 Tell me about yourself to find matching schemes","info");
  await aiSpeak(msg, "en-IN", () => startMic("en-IN"));
}

async function handleRecommenderInput(text) {
  addChatBubble("user", text);
  setMicBtn("processing"); setStatus("🤖 Analysing your situation…","info");
  const typing = addAiTyping();
  const result = await callApi("/api/scheme_recommend", { situation:text, lang:selectedLang });
  removeTypingBubble();
  if (result) {
    showSchemeCards(result);
    if (result.message) aiSpeak(result.message, selectedLang);
  }
  mode="idle"; setMicBtn("idle");
}

function showSchemeCards(result) {
  const container = document.getElementById("recommend-results");
  if (!container) return;
  container.style.display = "block";
  let html = result.message ? `<div style="font-size:14px;font-weight:600;color:#1a237e;margin-bottom:12px;">${result.message}</div>` : "";
  (result.schemes||[]).forEach(s => {
    html += `<div style="background:white;border:1.5px solid #e2e8f0;border-radius:10px;padding:14px;margin-bottom:10px;">
      <strong style="font-size:14px;">${s.name}</strong>
      <div style="font-size:13px;color:#4b5563;margin:4px 0;">${s.benefit}</div>
      <div style="font-size:12px;color:#6b7280;margin-bottom:8px;">${s.reason}</div>
      <a href="${s.url}" style="background:#1a237e;color:white;padding:7px 16px;border-radius:20px;text-decoration:none;font-size:12px;font-weight:700;">Apply Now →</a>
    </div>`;
  });
  container.innerHTML = html;
  const chatBubbleText = (result.schemes||[]).map(s=>`• ${s.name}: ${s.benefit}`).join("<br>");
  if (chatBubbleText) addChatBubble("ai", "💡 Based on your situation:<br>" + chatBubbleText);
}

/* ═══════════════════════════════════════════════════════════
   PUBLIC ENTRY POINT
═══════════════════════════════════════════════════════════ */

window.startSpeech = function() {
  if (mode !== "idle") { stopListeningAll(); if (speechSynthesis.speaking) speechSynthesis.cancel(); // Fix 9: prevent race }
  collectedData={}; conversationHistory=[]; currentStep=0; retryCount=0;
  selectedLang="en-IN"; eligibilityStep=0; eligibilityAnswers={}; _micGuard=false;
  mode = "language";
  updateProgress();
  const eb = document.getElementById("eligibility-banner");
  if (eb) eb.style.display="none";
  const sb = document.getElementById("success-banner");
  if (sb) sb.style.display="none";
  const panel = document.getElementById("chat-panel");
  if (panel) panel.innerHTML="";
  hideConfirmCard(); hideCurrentField();
  unlockAudio();
  const welcome = SYS["en-IN"].welcome;
  addChatBubble("ai", welcome);
  aiSpeak(welcome, "en-IN", () => startMic("en-IN"));
};

/* ── Voice gender toggle ──────────────────────────────────── */
window.toggleVoice = function(gender) {
  voiceGender = gender;
  document.querySelectorAll(".voice-toggle").forEach(b => b.classList.remove("active"));
  document.querySelector(`.voice-toggle[data-gender="${gender}"]`)?.classList.add("active");
};

/* ── Lang pill setup ──────────────────────────────────────── */
function setupLangPills() {
  const PILL_MAP = {
    "తెలుగు":"te-IN","हिन्दी":"hi-IN","English":"en-IN",
    "தமிழ்":"ta-IN","ಕನ್ನಡ":"kn-IN","বাংলা":"bn-IN",
    "मराठी":"mr-IN","ગુજ.":"gu-IN","ਪੰਜ.":"pa-IN"
  };
  document.querySelectorAll(".lang-pill").forEach(pill => {
    pill.addEventListener("click", () => {
      const code = PILL_MAP[pill.textContent.trim()];
      if (code) {
        selectedLang = code;
        document.querySelectorAll(".lang-pill").forEach(p=>p.classList.remove("active"));
        pill.classList.add("active");
      }
    });
  });
}

/* ═══════════════════════════════════════════════════════════
   INIT
═══════════════════════════════════════════════════════════ */
window.addEventListener("load", () => {
  if (window.speechSynthesis) {
    window.speechSynthesis.getVoices();
    if (window.speechSynthesis.onvoiceschanged !== undefined)
      window.speechSynthesis.onvoiceschanged = () => window.speechSynthesis.getVoices();
  }

  setupLangPills();
  setupOcrUpload();

  // Show iOS banner
  if (IS_IOS && IS_SAFARI) {
    const b = document.getElementById("ios-banner");
    if (b) b.classList.add("show");
  }

  if (useGcpStt) setTimeout(initSocket, 400);
  else setTimeout(() => initBrowserStt(), 400);

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js")
      .then(()=>console.log("[PWA] SW registered"))
      .catch(e=>console.warn("[PWA] SW error:",e));
  }

  // First tap unlocks audio on all browsers
  document.addEventListener("click", unlockAudio, {once:true});

  console.log(`🚀 Sarathi Voice Engine v${SV_VERSION} loaded. GCP STT:${useGcpStt} TTS:${useGcpTts} Gemini:${useGemini}`);
});
