/* ═══════════════════════════════════════════════════════════
   stt.js — Sarathi Voice Engine
   Browser STT + GCP WebSocket STT, all fixes applied:
   Fix 1: no duplicate condition
   Fix 3: no restart loop (500ms delay)
   Fix 5: no simultaneous sessions
   Fix 7: Safari maxAlternatives guard
═══════════════════════════════════════════════════════════ */
"use strict";

let recognition   = null;
let isListening   = false;
let isRecording   = false;
let _micGuard     = false;
let silenceTimer  = null;
let socket        = null;
let mediaRecorder = null;

const IS_IOS    = /iPhone|iPad|iPod/i.test(navigator.userAgent);
const IS_SAFARI = /^((?!chrome|android).)*safari/i.test(navigator.userAgent);
const SV_VERSION = "4.1";

// ── Silence timer ─────────────────────────────────────────
function armSilenceTimer(ms = 10000) {
  clearSilenceTimer();
  silenceTimer = setTimeout(() => {
    if (isListening) {
      stopListeningAll();
      promptRetry(window.selectedLang || "en-IN");
    }
  }, ms);
}

function clearSilenceTimer() {
  if (silenceTimer) { clearTimeout(silenceTimer); silenceTimer = null; }
}

// ── Stop everything ───────────────────────────────────────
function stopListeningAll() {
  clearSilenceTimer();
  isListening = false; isRecording = false; _micGuard = false;
  if (recognition) { try { recognition.abort(); } catch(e) {} }
  if (mediaRecorder && mediaRecorder.state !== "inactive") {
    try { mediaRecorder.stop(); } catch(e) {}
  }
  stopAllMicTracks();   // from audio.js
  stopWaveformVisualizer();
  setMicBtn("idle");
}

// ── Browser STT init ──────────────────────────────────────
function initBrowserStt() {
  if (recognition) return true;
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) {
    setStatus("⚠️ Speech recognition not supported. Try Chrome.", "error");
    return false;
  }
  recognition = new SR();
  recognition.continuous     = false;
  recognition.interimResults = true;
  // Fix 7: Safari crashes on maxAlternatives assignment
  if ("maxAlternatives" in recognition) recognition.maxAlternatives = 3;

  recognition.onstart = () => setMicBtn("listening");

  recognition.onresult = async (event) => {
    let txt = "", isFinal = false;
    for (let i = event.resultIndex; i < event.results.length; i++) {
      // Pick highest-confidence alternative
      let best = event.results[i][0].transcript;
      for (let j = 1; j < event.results[i].length; j++) {
        if (event.results[i][j].confidence > event.results[i][0].confidence)
          best = event.results[i][j].transcript;
      }
      txt += best;
      if (event.results[i].isFinal) isFinal = true;
    }
    txt = txt.trim();
    if (isFinal && txt) {
      clearSilenceTimer();
      isListening = false; _micGuard = false;
      stopWaveformVisualizer();
      setMicBtn("processing");
      setStatus(`🗣️ Heard: <em>"${txt}"</em>`, "speaking");
      await handleTranscript(txt);   // formFlow.js
    } else if (txt) {
      setStatus(`🎤 <em>"${txt}"</em>`, "speaking");
    }
  };

  // Fix 3: No restart loop — 500ms delay prevents rapid-fire crash on Android/Safari
  recognition.onend = () => {
    clearSilenceTimer();
    if (!isListening || mode === "idle") {
      isListening = false; _micGuard = false;
      stopWaveformVisualizer(); setMicBtn("idle");
      return;
    }
    setTimeout(() => {
      try {
        recognition.lang = window.selectedLang || "en-IN";
        recognition.start();
      } catch(e) {
        isListening = false; _micGuard = false;
        stopWaveformVisualizer(); setMicBtn("idle");
      }
    }, 500);
  };

  recognition.onerror = e => {
    if (e.error === "no-speech") {
      isListening = false; _micGuard = false; stopWaveformVisualizer();
      if (mode !== "idle") promptRetry(window.selectedLang || "en-IN");
      return;
    }
    if (e.error === "aborted" || e.error === "interrupted") return;
    if (e.error === "not-allowed") {
      isListening = false; _micGuard = false; stopWaveformVisualizer();
      setStatus("⚠️ Microphone access denied. Allow mic in browser settings.", "error");
      return;
    }
    isListening = false; _micGuard = false; stopWaveformVisualizer();
    setMicBtn("idle");
  };
  return true;
}

// ── startBrowserMic ───────────────────────────────────────
async function startBrowserMic(lang) {
  if (!initBrowserStt()) return;
  recognition.lang = lang;   // set BEFORE getUserMedia
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true }
    });
    micStream = stream;
    startWaveformVisualizer(stream);
  } catch(e) {
    _micGuard = false;
    if (e.name === "NotAllowedError")
      setStatus("⚠️ Microphone blocked. Please allow access.", "error");
    else
      setStatus("⚠️ Microphone unavailable.", "error");
    return;
  }
  isListening = true; _micGuard = true;
  setMicBtn("listening");
  setTimeout(() => {
    try {
      recognition.lang = lang;   // confirm lang after stream ready
      recognition.start();
    } catch(e) {
      isListening = false; _micGuard = false;
    }
  }, 150);
  armSilenceTimer(10000);
}

// ── GCP WebSocket STT ─────────────────────────────────────
function initSocket() {
  if (socket) return;
  socket = io();
  socket.on("transcript", async data => {
    if (data.final && data.text) {
      clearSilenceTimer();
      isListening = false; isRecording = false; _micGuard = false;
      stopWaveformVisualizer(); setMicBtn("processing");
      setStatus(`🗣️ Heard: <em>"${data.text}"</em>`, "speaking");
      await handleTranscript(data.text);
    } else if (data.text) {
      setStatus(`🎤 <em>"${data.text}"</em>`, "speaking");
    }
  });
  socket.on("error", () => {
    stopListeningAll();
    setStatus("⚠️ STT error. Please try again.", "error");
  });
}

async function startGcpMic(lang) {
  if (!socket) initSocket();
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true }
    });
    micStream = stream;
    startWaveformVisualizer(stream);
    mediaRecorder = new MediaRecorder(stream, { mimeType: "audio/webm" });
    mediaRecorder.ondataavailable = e => {
      if (e.data.size > 0 && socket) socket.emit("audio_chunk", e.data);
    };
    socket.emit("start_stream", { lang });
    mediaRecorder.start(250);
    isListening = true; isRecording = true; _micGuard = true;
    setMicBtn("listening");
    armSilenceTimer(12000);
  } catch(e) {
    _micGuard = false;
    setStatus("⚠️ Microphone unavailable.", "error");
  }
}

// ── Primary mic entry ─────────────────────────────────────
function startMic(lang) {
  // Fix 5: Stop any running session before starting new one
  if (isListening || isRecording) stopListeningAll();
  if (_micGuard) return;
  _micGuard = true;
  const activeLang = lang || window.selectedLang || "en-IN";
  if (window.useGcpStt) {
    startGcpMic(activeLang);
  } else {
    startBrowserMic(activeLang);
  }
}

function promptRetry(lang) {
  const fields = window.SARATHI_FIELDS || [];
  const field  = fields[window.currentStep];
  const sys    = getSys(lang);
  const msg    = field
    ? sys.retry.replace("{field}", field.labels[lang] || field.labels["en-IN"])
    : sys.notUnderstood;
  addChatBubble("ai", msg);
  aiSpeak(msg, lang, () => startMic(lang));
}
