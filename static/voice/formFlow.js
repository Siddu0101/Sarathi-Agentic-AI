/* ════════════════════════════════════════════════════════════
   formFlow.js — Sarathi Form Flow v5.0
   Handles: photo upload detection, field-by-field collection,
            readback confirmation, final review, submission.
   All logic lives in sarathi_main.js. This file provides
   helper functions and field-validation utilities.
════════════════════════════════════════════════════════════ */
"use strict";

/* ── Field value normalizer ───────────────────────────────── */
function normalizeFieldValue(fieldId, raw) {
  if(!raw) return "";
  const r = raw.trim();
  // Pincode: digits only
  if(fieldId === "pincode") return r.replace(/\D/g,"").slice(0,6);
  // Aadhaar: digits only
  if(fieldId === "aadhaar") return r.replace(/\D/g,"").slice(0,12);
  // Mobile: digits only
  if(fieldId === "mobile" || fieldId === "phone") return r.replace(/\D/g,"").slice(0,10);
  // Date fields
  if(fieldId === "dob" || fieldId === "date") return r;
  // Numbers
  if(fieldId === "age" || fieldId === "income") return r.replace(/\D/g,"");
  return r;
}

/* ── Auto-fill form element helper ───────────────────────── */
function autofillElement(id, value) {
  const el = document.getElementById(id);
  if(!el) return false;
  const norm = normalizeFieldValue(id, value);
  if(el.tagName === "SELECT") {
    // Try to find closest option
    const opts = Array.from(el.options);
    const lower = norm.toLowerCase();
    const match = opts.find(o =>
      o.value.toLowerCase() === lower ||
      o.text.toLowerCase().includes(lower) ||
      lower.includes(o.value.toLowerCase())
    );
    if(match) { el.value = match.value; el.classList.add("filled"); return true; }
  } else {
    el.value = norm;
    el.classList.add("filled");
    // Trigger change event so dependent fields update
    el.dispatchEvent(new Event("change",{bubbles:true}));
    el.dispatchEvent(new Event("input", {bubbles:true}));
    return true;
  }
  return false;
}

/* ── Photo upload detection (backup for main.js) ─────────── */
function setupPhotoUploadListener() {
  ["photo-input","photo_input","passPhotoInput"].forEach(id => {
    const el = document.getElementById(id);
    if(el && !el.dataset.sarathiListening) {
      el.dataset.sarathiListening = "1";
      el.addEventListener("change", () => {
        if(window.mode === "photo_wait") {
          window.dispatchEvent(new Event("sarathi_photo_uploaded"));
        }
      });
    }
  });
}

/* ── Listen for photo uploaded event ─────────────────────── */
window.addEventListener("sarathi_photo_uploaded", () => {
  if(window.mode !== "photo_wait") return;
  window.mode = "form_filling";
  window.currentStep = 0;
  if(typeof askNextQuestion === "function") askNextQuestion();
});

/* ── Form submit intercept ────────────────────────────────── */
function interceptFormSubmit() {
  const form = document.getElementById("sarathiForm") || document.querySelector("form");
  if(form && !form.dataset.sarathiIntercepted) {
    form.dataset.sarathiIntercepted = "1";
    form.addEventListener("submit", e => {
      // Let normal submit proceed; we just fill fields first
      if(window.collectedData) {
        Object.entries(window.collectedData).forEach(([k,v]) => autofillElement(k, v));
      }
    });
  }
}

/* ── Visual filled-field indicator ───────────────────────── */
function markFilledFields() {
  if(!window.collectedData) return;
  Object.keys(window.collectedData).forEach(k => {
    const el = document.getElementById(k);
    if(el && window.collectedData[k]) el.classList.add("filled");
  });
}

/* Init on DOM ready */
document.addEventListener("DOMContentLoaded", () => {
  setupPhotoUploadListener();
  interceptFormSubmit();
  // Expose autofill globally for sarathi_main.js
  window.autofillElement = autofillElement;
  window.normalizeFieldValue = normalizeFieldValue;
});
