/* ═══════════════════════════════════════════════════════════
   api.js — Sarathi Voice Engine
   All server API calls, null guards, timeout handling
═══════════════════════════════════════════════════════════ */
"use strict";

const API_TIMEOUT_MS = 15000;

async function callApi(endpoint, body) {
  try {
    const ctrl    = new AbortController();
    const timer   = setTimeout(() => ctrl.abort(), API_TIMEOUT_MS);
    const resp    = await fetch(endpoint, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify(body),
      signal:  ctrl.signal,
    });
    clearTimeout(timer);
    if (!resp.ok) return null;
    return await resp.json();
  } catch(e) {
    if (e.name === "AbortError")
      console.warn(`[API] ${endpoint} timed out after ${API_TIMEOUT_MS}ms`);
    else
      console.error(`[API] ${endpoint} failed:`, e);
    return null;
  }
}

async function uploadOcr(formData) {
  // Fix 8: 30s timeout for OCR to prevent freeze
  try {
    const ctrl  = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 30000);
    const resp  = await fetch("/api/ocr_upload", {
      method: "POST",
      body:   formData,
      signal: ctrl.signal,
    });
    clearTimeout(timer);
    if (!resp.ok) return null;
    return await resp.json();
  } catch(e) {
    if (e.name === "AbortError")
      console.warn("[OCR] Upload timed out after 30s");
    return null;
  }
}

async function submitForm(payload) {
  return await callApi("/api/submit_form", payload);
}

async function checkEligibilityApi(scheme_name, answers) {
  const result = await callApi("/api/eligibility_check", { scheme_name, answers });
  // Fix 10: Always null-guard API results
  if (!result) {
    addChatBubble("ai", "⚠️ Server unavailable. Please try again.");
    return null;
  }
  return result;
}

async function recommendSchemeApi(situation, lang) {
  const result = await callApi("/api/scheme_recommend", { situation, lang });
  if (!result) {
    addChatBubble("ai", "⚠️ Recommendation service unavailable.");
    return null;
  }
  return result;
}

async function geminiProcessApi(transcript, fields, collectedData, currentStep, selectedLang) {
  return await callApi("/api/gemini_process", {
    transcript,
    fields,
    collected: collectedData,
    step:      currentStep,
    lang:      selectedLang,
  });
}
