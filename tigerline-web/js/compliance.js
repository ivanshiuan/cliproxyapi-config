/* =====================================================================
 * compliance.js — bilingual disclaimer + forbidden-phrase guard.
 * Mirrors tigerline/compliance_guard.py:DEFAULT_DISCLAIMER_* and _PATTERNS.
 * ===================================================================== */
"use strict";

export const DISCLAIMER_ZH =
  "本報告僅供體育賽事資料研究與風險控管參考，不構成保證獲利承諾。" +
  "投注涉及風險，請遵守所在地法律，並量力而為。";

export const DISCLAIMER_EN =
  "This report is for sports data research and risk management reference only. " +
  "It does not constitute a guarantee of profit. Betting involves risk; " +
  "comply with local law and bet within your means.";

export const DISCLAIMER = `${DISCLAIMER_ZH}\n\n${DISCLAIMER_EN}`;

// Chinese: \b doesn't work between CJK chars; use plain literals.
const FORBIDDEN = [
  /保證獲利/,
  /保證賺錢/,
  /保證命中/,
  /保證穩贏/,
  /穩賺/,
  /必中/,
  /包贏/,
  /guaranteed (profit|win|hits?|returns?)/i,
  /100%\s*(win|guarantee|profit)/i,
  /can'?t\s+lose/i,
  /risk[-\s]?free/i,
];

export function checkText(text) {
  const hits = [];
  for (const pat of FORBIDDEN) {
    const m = text.match(pat);
    if (m) hits.push(m[0]);
  }
  return hits;
}

export function enforce(text) {
  // The disclaimer itself contains "保證獲利承諾" (negating a profit guarantee).
  // Scan only the pre-disclaimer body so enforce() is safely idempotent.
  const stripped = text.split(DISCLAIMER_ZH)[0];
  const hits = checkText(stripped);
  if (hits.length) throw new Error(`compliance violations: ${JSON.stringify(hits)}`);
  return text.includes(DISCLAIMER_ZH) ? text : `${text}\n\n---\n\n${DISCLAIMER}`;
}
