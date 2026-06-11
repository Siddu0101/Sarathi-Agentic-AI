/* ════════════════════════════════════════════════════════════
   eligibility.js — Sarathi v5.0
   Eligibility API helper. The voice eligibility Q&A flow
   is handled entirely inside sarathi_main.js.
   This module provides the server-side eligibility check
   helper for the /api/eligibility_check endpoint.
════════════════════════════════════════════════════════════ */
"use strict";

/**
 * Call the server eligibility API and return results.
 * Called from the dashboard "Check Eligibility" button only.
 */
async function checkEligibilityAPI(answers, schemeName) {
  try {
    const resp = await fetch("/api/eligibility_check", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ answers, scheme_name: schemeName || "" }),
    });
    if(!resp.ok) return null;
    return await resp.json();
  } catch(e) {
    console.error("Eligibility API error:", e);
    return null;
  }
}

window.checkEligibilityAPI = checkEligibilityAPI;
