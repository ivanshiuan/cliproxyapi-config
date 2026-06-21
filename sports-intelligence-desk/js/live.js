/* =====================================================================
 * SPORTS INTELLIGENCE DESK — LIVE 對接層（即時資料）
 * ---------------------------------------------------------------------
 * 支援三家賽事 API，擇一設定 key 即可；抓不到時自動降級用內建快照。
 * 球隊評級(Elo/xG)仍用本系統 curated 先驗 — API 只更新「賽程/比分/盤口」。
 *
 * 設定方式（在瀏覽器 console 或 app 設定頁）：
 *   SID.liveConfig = { provider: "apifootball", key: "你的KEY", season: 2026 };
 *   await SID.refresh();   // 抓最新 → 覆寫 SID.matches → 重算
 *
 * ⚠️ CORS：多數賽事 API 不允許瀏覽器直連，需經 proxy（見 docs 部署說明）。
 *    Node/伺服器端則無此限制。
 * ===================================================================== */
(function (SID) {
  "use strict";

  SID.liveConfig = SID.liveConfig || { provider: null, key: "", season: 2026, proxy: "" };

  /* 隊名 → 內建代碼（API 回傳英文名，對映到我們的 curated 評級） */
  const NAME2CODE = {
    "Spain": "ESP", "Saudi Arabia": "KSA", "Belgium": "BEL", "Iran": "IRN",
    "IR Iran": "IRN", "Uruguay": "URU", "Cape Verde": "CPV", "Cabo Verde": "CPV",
    "New Zealand": "NZL", "Egypt": "EGY",
  };
  function codeOf(name) { return NAME2CODE[name] || null; }

  function url(path) {
    const p = SID.liveConfig.proxy || "";
    return p ? p + encodeURIComponent(path) : path;   // proxy 解 CORS
  }

  /* 非 2xx（無效 key / 額度 / 403）視為失敗，避免被當成「成功零結果」 */
  async function fetchJson(input, init) {
    const r = await fetch(input, init);
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error((j && (j.error || j.message)) || `HTTP ${r.status}`);
    return j;
  }

  /* ---------- 三家 Provider 的 fetch + 映射 ---------- */
  const providers = {
    /* API-Football (v3.football.api-sports.io)，league 1 = World Cup */
    apifootball: {
      async fixtures(cfg) {
        const j = await fetchJson(url(`https://v3.football.api-sports.io/fixtures?league=1&season=${cfg.season}`),
          { headers: { "x-apisports-key": cfg.key } });
        return (j.response || []).map(f => ({
          id: "API-" + f.fixture.id,
          competition: "FIFA World Cup", group: (f.league.round || "").replace("Group ", ""),
          stage: f.league.round, home: codeOf(f.teams.home.name), away: codeOf(f.teams.away.name),
          home_name: f.teams.home.name, away_name: f.teams.away.name,
          stadium: f.fixture.venue && f.fixture.venue.name, city: f.fixture.venue && f.fixture.venue.city,
          kickoff_utc: f.fixture.date,
          score: f.goals.home != null ? { gh: f.goals.home, ga: f.goals.away } : null,
        }));
      },
      /* 傷病：回傳 [{ code, name, status }]，只給 refresh 比對 curated 名單用 */
      async injuries(cfg) {
        const j = await fetchJson(url(`https://v3.football.api-sports.io/injuries?league=1&season=${cfg.season}`),
          { headers: { "x-apisports-key": cfg.key } });
        return (j.response || []).map(e => ({
          code: codeOf(e.team && e.team.name),
          name: (e.player && e.player.name) || "",
          // type 含 "Questionable"/"Doubtful" → doubt；其餘（Missing/Injury/Suspended）→ out
          status: /quest|doubt/i.test(e.player && e.player.type || "") ? "doubt" : "out",
        })).filter(x => x.code && x.name);
      },
    },
    /* football-data.org v4（免費 token） */
    footballdata: {
      async fixtures(cfg) {
        const j = await fetchJson(url("https://api.football-data.org/v4/competitions/WC/matches"),
          { headers: { "X-Auth-Token": cfg.key } });
        return (j.matches || []).map(m => ({
          id: "FD-" + m.id, competition: "FIFA World Cup", group: m.group || "",
          stage: m.stage, home: codeOf(m.homeTeam.name), away: codeOf(m.awayTeam.name),
          home_name: m.homeTeam.name, away_name: m.awayTeam.name,
          kickoff_utc: m.utcDate,
          score: m.score.fullTime.home != null ? { gh: m.score.fullTime.home, ga: m.score.fullTime.away } : null,
        }));
      },
    },
    /* TheSportsDB（免費 key=3，無盤口） */
    thesportsdb: {
      async fixtures(cfg) {
        // cfg.date 未設時退回今天（避免 d=undefined 打壞查詢）
        const day = cfg.date || new Date().toISOString().slice(0, 10);
        const j = await fetchJson(url(`https://www.thesportsdb.com/api/v1/json/${cfg.key || 3}/eventsday.php?d=${day}&s=Soccer`));
        return (j.events || []).map(e => ({
          id: "TSD-" + e.idEvent, competition: e.strLeague, group: "",
          home: codeOf(e.strHomeTeam), away: codeOf(e.strAwayTeam),
          home_name: e.strHomeTeam, away_name: e.strAwayTeam,
          kickoff_utc: e.strTimestamp ? new Date(e.strTimestamp).toISOString() : null,
          score: e.intHomeScore != null ? { gh: +e.intHomeScore, ga: +e.intAwayScore } : null,
        }));
      },
    },
  };

  /* ---------- refresh()：抓 live → 合併 → 重算 ---------- */
  SID.refresh = async function () {
    const cfg = SID.liveConfig;
    if (!cfg.provider || !providers[cfg.provider]) {
      return { ok: false, reason: "未設定 provider/key，沿用內建快照", live: false };
    }
    try {
      const fixtures = await providers[cfg.provider].fixtures(cfg);
      const usable = fixtures.filter(f => f.home && f.away);   // 有對映到 curated 評級的才能算
      // 合併：更新賽程；已知盤口若 API 沒給則保留原值
      for (const f of usable) {
        const existing = SID.matches.find(m => m.home === f.home && m.away === f.away);
        if (existing) {
          existing.kickoff_utc = f.kickoff_utc || existing.kickoff_utc;
          if (f.score) SID.results[existing.id] = f.score;   // 賽後比分 → 回測
        }
      }
      // 傷病：只「比對」curated 名單裡已存在的球員（守鐵律：不無中生有、不亂造傷病）。
      let injApplied = 0;
      const provider = providers[cfg.provider];
      if (provider.injuries) {
        try {
          const surname = (n) => (n || "").trim().split(/\s+/).pop().toLowerCase();
          const inj = await provider.injuries(cfg);
          const byTeam = {};
          for (const x of inj) (byTeam[x.code] = byTeam[x.code] || []).push(x);
          for (const [code, list] of Object.entries(SID.players)) {
            const feed = byTeam[code]; if (!feed) continue;
            for (const p of list) {
              const hit = feed.find(f => surname(f.name) === surname(p.name));
              if (hit && p.status !== hit.status) { p.status = hit.status; injApplied++; }
            }
          }
        } catch (_) { /* 傷病端點失敗不影響賽程更新 */ }
      }

      SID.meta.live = true;
      SID.meta.source = `LIVE: ${cfg.provider}`;
      SID.meta.snapshot = new Date().toISOString().slice(0, 10);
      return { ok: true, live: true, fetched: fixtures.length, usable: usable.length, injuriesApplied: injApplied };
    } catch (e) {
      return { ok: false, reason: "抓取失敗（CORS/網路/key）：" + e.message, live: false };
    }
  };

  /* 覆寫 data.js 的預留接口，讓 fetchFixtures 走真實 refresh */
  SID.fetchFixtures = async function () {
    const r = await SID.refresh();
    return { matches: SID.matches, teams: SID.teams, meta: SID.meta, status: r };
  };
})(window.SID);
