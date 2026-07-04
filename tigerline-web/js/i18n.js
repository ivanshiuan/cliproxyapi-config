/* =====================================================================
 * i18n.js — 顯示層中文化對照表。引擎內部一律用英文 enum；只在 render
 * 時透過這裡查表換成中文，資料層完全不動。
 * ===================================================================== */
"use strict";

export const SCENARIO_ZH = {
  two_goal_landing: "兩球落點盤（強隊會贏 2 球以上）",
  must_win_pressure: "必勝壓力盤（強隊要贏但盤口保留餘地）",
  pressure_under: "壓力小分（雙方都需要贏但戰意謹慎）",
  goal_market: "進球盤機會（讓球淺、大小盤開放）",
  narrow_win: "微差勝（強隊小勝一顆）",
  rotation_trap: "輪休陷阱（強隊會輪休 → 跳過）",
  unclear_skip: "訊號不明（跳過重看）",
};

export const HARNESS_ZH = {
  upgrade: "升級（加碼）",
  normal: "正常",
  downgrade: "降級（減碼）",
  skip: "跳過（本場不下）",
};

export const MAIN_RESULT_ZH = {
  win: "全贏",
  half_win: "贏半",
  push: "退款（走盤）",
  half_lose: "輸半",
  lose: "全輸",
  void: "作廢",
  skipped: "本場沒下",
};

export const MARKET_ZH = {
  AH: "亞洲讓球",
  totals: "大小",
  correct_score: "波膽",
  BTTS: "雙方進球",
};

export const NEEDS_ZH = {
  must_win: "必須贏",
  draw_ok: "平就過關",
  rotation: "會輪休",
  neutral: "普通壓力",
  unknown: "未知",
};

export function tScenario(en)   { return SCENARIO_ZH[en] || en; }
export function tHarness(en)    { return HARNESS_ZH[en]  || en; }
export function tMainResult(en) { return MAIN_RESULT_ZH[en] || en; }
export function tMarket(en)     { return MARKET_ZH[en]   || en; }
export function tNeed(en)       { return NEEDS_ZH[en]    || en; }
