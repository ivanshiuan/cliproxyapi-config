/* =====================================================================
 * SPORTS INTELLIGENCE DESK — 歷史交手 + 大賽戰績庫
 * ---------------------------------------------------------------------
 * 普通預測器最弱的一層，懂球的最看重的一層。
 * 全為查證事實；首次交手據實標「無歷史交手」，不杜撰。
 * pedigree(0~100) = 世界盃/大賽底蘊量化，供信心與心理對位參考。
 * ===================================================================== */
(function (SID) {
  "use strict";

  /* ---------- TournamentHistory｜大賽戰績（世界盃為主） ---------- */
  SID.tournamentHistory = {
    ESP: { titles: 1, apps: 16, best: "冠軍 2010", knockout: "強（2008-2012 三連霸時代）", pedigree: 90 },
    KSA: { titles: 0, apps: 7, best: "16強 1994", knockout: "弱（多止步小組）", pedigree: 38,
      note: "2022 爆冷 2-1 勝阿根廷" },
    BEL: { titles: 0, apps: 14, best: "季軍 2018", knockout: "中上（黃金世代）", pedigree: 72,
      note: "世代交替，核心老化" },
    IRN: { titles: 0, apps: 6, best: "小組賽", knockout: "無（從未出線）", pedigree: 40 },
    URU: { titles: 2, apps: 14, best: "冠軍 1930/1950", knockout: "強（2010 四強）", pedigree: 85,
      note: "大賽底蘊與兇悍著稱" },
    CPV: { titles: 0, apps: 1, best: "首次參賽 2026", knockout: "無", pedigree: 22,
      note: "隊史首屆世界盃，無大賽經驗" },
    NZL: { titles: 0, apps: 3, best: "小組賽（2010 不敗出局）", knockout: "無", pedigree: 30,
      note: "2010 三和不敗仍止步小組" },
    EGY: { titles: 0, apps: 4, best: "小組賽", knockout: "無", pedigree: 35,
      note: "非洲盃 7 冠，世界盃舞台經驗有限" },
  };

  /* ---------- HeadToHead｜歷史交手（查證；無則標 first） ----------
   * key = "AAA_BBB"（兩隊代碼字典序）。games 由舊到新。
   */
  SID.headToHead = {
    BEL_IRN: { first: false, summary: "2014 世界盃小組賽比利時 1-0 伊朗（伊朗鐵桶僅末段失守）",
      games: [{ comp: "2014 WC", score: "BEL 1-0 IRN" }] },
    ESP_KSA: { first: false, summary: "僅熱身賽交手，無正式大賽對戰紀錄；實力差距懸殊",
      games: [] },
    CPV_URU: { first: true, summary: "兩隊史上首次交手，無歷史對位可參考（維德角首屆世界盃）",
      games: [] },
    EGY_NZL: { first: true, summary: "兩隊近代無正式交手紀錄，無歷史對位可參考",
      games: [] },
  };

  function key(a, b) { return [a, b].sort().join("_"); }

  /* 查詢：回傳交手 + 雙方底蘊 + 心理/底蘊對位註解 */
  SID.history = {
    lookup(m, home, away) {
      const h2h = SID.headToHead[key(m.home, m.away)] || { first: true, summary: "無歷史交手紀錄", games: [] };
      const hp = SID.tournamentHistory[m.home] || null;
      const ap = SID.tournamentHistory[m.away] || null;
      const notes = [];
      if (hp && ap) {
        const gap = hp.pedigree - ap.pedigree;
        if (Math.abs(gap) >= 35)
          notes.push(`大賽底蘊懸殊（${gap > 0 ? home.name : away.name} 經驗壓制）：${home.name} ${hp.pedigree} vs ${away.name} ${ap.pedigree}`);
        else
          notes.push(`大賽底蘊：${home.name} ${hp.pedigree} / ${away.name} ${ap.pedigree}`);
        if (hp.note) notes.push(`${home.name}：${hp.note}`);
        if (ap.note) notes.push(`${away.name}：${ap.note}`);
      }
      if (h2h.first) notes.push("⚠️ 首次交手 → 無對位先驗，模型對位項信心略降");
      return { h2h, homePedigree: hp, awayPedigree: ap, notes };
    },
  };
})(window.SID);
