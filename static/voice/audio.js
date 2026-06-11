/* ═══════════════════════════════════════════════════════════
   audio.js — Sarathi Voice Engine
   AudioContext, waveform visualizer, mic stream, unlock
═══════════════════════════════════════════════════════════ */
"use strict";

let audioContext      = null;
let waveformAnimId    = null;
let micStream         = null;
let analyser          = null;
let audioUnlocked     = false;

function unlockAudio() {
  if (audioUnlocked) return;
  try {
    audioContext = audioContext || new (window.AudioContext || window.webkitAudioContext)();
    if (audioContext.state === "suspended") audioContext.resume();
    // Create silent buffer to unlock on iOS
    const buf  = audioContext.createBuffer(1, 1, 22050);
    const src  = audioContext.createBufferSource();
    src.buffer = buf;
    src.connect(audioContext.destination);
    src.start(0);
    audioUnlocked = true;
  } catch(e) { /* desktop — no unlock needed */ }
}

function startWaveformVisualizer(stream) {
  stopWaveformVisualizer();
  const canvas = document.getElementById("waveform-canvas");
  if (!canvas) return;
  try {
    audioContext = audioContext || new (window.AudioContext || window.webkitAudioContext)();
    if (audioContext.state === "suspended") audioContext.resume();
    analyser = audioContext.createAnalyser();
    analyser.fftSize = 256;
    const src = audioContext.createMediaStreamSource(stream);
    src.connect(analyser);
    const data = new Uint8Array(analyser.frequencyBinCount);
    const ctx  = canvas.getContext("2d");

    function draw() {
      waveformAnimId = requestAnimationFrame(draw);
      analyser.getByteFrequencyData(data);
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.fillStyle = "#1a237e22";
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      const barW = canvas.width / data.length * 2.5;
      let x = 0;
      data.forEach(v => {
        const h = (v / 255) * canvas.height;
        ctx.fillStyle = `rgba(26,35,126,${v / 255})`;
        ctx.fillRect(x, canvas.height - h, barW, h);
        x += barW + 1;
      });
    }
    draw();
    canvas.style.display = "block";
  } catch(e) { console.warn("[Waveform]", e); }
}

function stopWaveformVisualizer() {
  if (waveformAnimId) { cancelAnimationFrame(waveformAnimId); waveformAnimId = null; }
  // Fix 6: Prevent AudioContext memory leak
  if (audioContext && audioContext.state !== "closed") {
    audioContext.suspend().catch(() => {});
  }
  if (micStream) {
    micStream.getTracks().forEach(t => t.stop());
    micStream = null;
  }
  const canvas = document.getElementById("waveform-canvas");
  if (canvas) {
    canvas.style.display = "none";
    const ctx = canvas.getContext("2d");
    if (ctx) ctx.clearRect(0, 0, canvas.width, canvas.height);
  }
}

function stopAllMicTracks() {
  if (micStream) {
    micStream.getTracks().forEach(t => t.stop());
    micStream = null;
  }
}

function setMicBtn(state) {
  const btn = document.getElementById("mic-btn");
  if (!btn) return;
  btn.dataset.state = state;
  const labels = {
    idle:        "🎤 Tap to Start",
    listening:   "🔴 Listening…",
    processing:  "⏳ Processing…",
    speaking:    "🔊 Speaking…",
    confirming:  "✅ Confirm?",
  };
  btn.textContent = labels[state] || "🎤";
  btn.disabled    = state === "processing" || state === "speaking";
}
