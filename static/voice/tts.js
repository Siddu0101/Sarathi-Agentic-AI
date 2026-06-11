/* ═══════════════════════════════════════════════════════════
   tts.js — Sarathi Voice Engine
   Browser TTS + GCP TTS, speech queue, voice selection
═══════════════════════════════════════════════════════════ */
"use strict";

let voiceGender  = "female";
let currentAudio = null;
let ttsQueue     = [];
let ttsPlaying   = false;

// BCP-47 → lang name for voice matching
const LANG_VOICE_MAP = {
  "en-IN": ["en-IN","en-GB","en-US"],
  "te-IN": ["te-IN","te"],
  "hi-IN": ["hi-IN","hi"],
  "ta-IN": ["ta-IN","ta"],
  "kn-IN": ["kn-IN","kn"],
  "ml-IN": ["ml-IN","ml"],
  "bn-IN": ["bn-IN","bn"],
  "mr-IN": ["mr-IN","mr"],
  "gu-IN": ["gu-IN","gu"],
  "pa-IN": ["pa-IN","pa"],
};

function getBestVoice(lang) {
  const voices   = window.speechSynthesis.getVoices();
  const preferred = LANG_VOICE_MAP[lang] || [lang];
  for (const pref of preferred) {
    // Exact match first
    const exact = voices.find(v => v.lang === pref &&
      (voiceGender === "female"
        ? /female|woman|zira|heera|veena|samantha/i.test(v.name)
        : /male|man|ravi|david/i.test(v.name)));
    if (exact) return exact;
    // Partial lang match
    const partial = voices.find(v => v.lang.startsWith(pref.split("-")[0]));
    if (partial) return partial;
  }
  return voices[0] || null;
}

// aiSpeak — primary TTS entry point
// onComplete fires AFTER speech ends (or immediately if TTS unavailable)
function aiSpeak(text, lang, onComplete) {
  return new Promise(resolve => {
    const safeComplete = () => { if (onComplete) onComplete(); resolve(); };

    if (window.useGcpTts) {
      _gcpSpeak(text, lang, safeComplete);
    } else {
      _browserSpeak(text, lang, safeComplete);
    }
  });
}

function _browserSpeak(text, lang, onComplete) {
  if (!window.speechSynthesis) { if (onComplete) onComplete(); return; }

  // Fix 9: only cancel if actually speaking — prevents race on Android
  if (speechSynthesis.speaking) speechSynthesis.cancel();

  setMicBtn("speaking");
  setStatus(`🔊 <em>${text.slice(0, 60)}…</em>`, "speaking");

  const msg    = new SpeechSynthesisUtterance(text);
  msg.lang     = lang;
  msg.rate     = 0.92;
  msg.pitch    = voiceGender === "female" ? 1.1 : 0.9;
  msg.volume   = 1.0;
  const voice  = getBestVoice(lang);
  if (voice) msg.voice = voice;

  msg.onend = msg.onerror = () => {
    setMicBtn("idle");
    if (onComplete) onComplete();
  };

  // iOS workaround: chunked speech for long text
  if (text.length > 200) {
    const chunks = _chunkText(text, 180);
    _speakChunks(chunks, lang, onComplete);
  } else {
    window.speechSynthesis.speak(msg);
  }
}

function _chunkText(text, size) {
  const words = text.split(" ");
  const chunks = [];
  let current = "";
  words.forEach(w => {
    if ((current + " " + w).length > size) { chunks.push(current.trim()); current = w; }
    else current += " " + w;
  });
  if (current.trim()) chunks.push(current.trim());
  return chunks;
}

async function _speakChunks(chunks, lang, onComplete) {
  for (const chunk of chunks) {
    await new Promise(res => {
      const msg  = new SpeechSynthesisUtterance(chunk);
      msg.lang   = lang;
      msg.rate   = 0.92;
      const voice = getBestVoice(lang);
      if (voice) msg.voice = voice;
      msg.onend = msg.onerror = res;
      window.speechSynthesis.speak(msg);
    });
  }
  setMicBtn("idle");
  if (onComplete) onComplete();
}

function _gcpSpeak(text, lang, onComplete) {
  if (currentAudio) { currentAudio.pause(); currentAudio = null; }
  setMicBtn("speaking");
  fetch("/api/tts", {
    method:  "POST",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify({ text, lang, gender: voiceGender }),
  })
    .then(r => r.blob())
    .then(blob => {
      const url = URL.createObjectURL(blob);
      currentAudio = new Audio(url);
      currentAudio.onended = () => { currentAudio = null; if (onComplete) onComplete(); };
      currentAudio.onerror = () => { currentAudio = null; if (onComplete) onComplete(); };
      currentAudio.play();
    })
    .catch(() => { if (onComplete) onComplete(); });
}
