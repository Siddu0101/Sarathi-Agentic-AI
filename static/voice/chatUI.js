/* ═══════════════════════════════════════════════════════════
   chatUI.js — Sarathi Voice Engine
   Chat bubbles, typing indicator, progress bar, status
═══════════════════════════════════════════════════════════ */
"use strict";

function addChatBubble(role, html) {
  const panel = document.getElementById("chat-panel");
  if (!panel) return;
  const div = document.createElement("div");
  div.className = `chat-bubble ${role === "ai" ? "bubble-ai" : "bubble-user"}`;
  div.innerHTML  = html;
  panel.appendChild(div);
  panel.scrollTop = panel.scrollHeight;
  return div;
}

function addAiTyping() {
  const panel = document.getElementById("chat-panel");
  if (!panel) return null;
  const div = document.createElement("div");
  div.className = "chat-bubble bubble-ai typing-indicator";
  div.id        = "typing-bubble";
  div.innerHTML = "<span></span><span></span><span></span>";
  panel.appendChild(div);
  panel.scrollTop = panel.scrollHeight;
  return div;
}

function removeTypingBubble() {
  const el = document.getElementById("typing-bubble");
  if (el) el.remove();
}

function setStatus(html, type = "info") {
  const el = document.getElementById("status-bar");
  if (!el) return;
  el.innerHTML  = html;
  el.className  = `status-bar status-${type}`;
  el.style.display = "block";
}

function updateProgress() {
  const fields = window.SARATHI_FIELDS || [];
  const pct    = fields.length ? Math.round((currentStep / fields.length) * 100) : 0;
  const bar    = document.getElementById("progress-bar");
  const label  = document.getElementById("progress-label");
  if (bar)   bar.style.width   = `${pct}%`;
  if (label) label.textContent = `${currentStep}/${fields.length} fields`;
}

function markFieldFilled(fieldId) {
  const row = document.querySelector(`[data-field-id="${fieldId}"]`);
  if (row) row.classList.add("filled");
}

function showCurrentField(field) {
  hideCurrentField();
  const el = document.getElementById(field.id);
  if (el) {
    const wrapper = el.closest(".field-row") || el.parentElement;
    if (wrapper) wrapper.classList.add("field-active");
    el.focus();
  }
}

function hideCurrentField() {
  document.querySelectorAll(".field-active").forEach(el => el.classList.remove("field-active"));
}

function hideConfirmCard() {
  const card = document.getElementById("confirm-card");
  if (card) card.style.display = "none";
}

function showSchemeCards(result) {
  const container = document.getElementById("recommend-results");
  if (!container || !result) return;
  container.style.display = "block";
  let html = result.message
    ? `<div class="rec-header">${result.message}</div>`
    : "";
  (result.schemes || []).forEach(s => {
    html += `
      <div class="scheme-card-rec">
        <strong>${s.name}</strong>
        <div class="scheme-benefit">${s.benefit}</div>
        <div class="scheme-reason">${s.reason}</div>
        <a href="${s.url}" class="apply-btn">Apply Now →</a>
      </div>`;
  });
  container.innerHTML = html;
  const chatLines = (result.schemes || []).map(s => `• ${s.name}: ${s.benefit}`).join("<br>");
  if (chatLines) addChatBubble("ai", "💡 Based on your situation:<br>" + chatLines);
}

function updateCurrentFieldValue(fieldId, value) {
  const el = document.getElementById(fieldId);
  if (el) el.value = maskSensitive(fieldId, value);
}
