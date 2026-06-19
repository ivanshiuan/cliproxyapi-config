/* =====================================================================
 * SPORTS INTELLIGENCE DESK — DATA LAYER (8 資料庫)
 * ---------------------------------------------------------------------
 * 這份是「投研欄位總表」的可執行版本。所有數值是賽前情報快照。
 * 真實對戰來源：2026 FIFA World Cup 官方賽程（2026-06-21 / 台灣 06-22）。
 * 排名/Elo：FIFA Men's Ranking June 2026 + eloratings 近似值。
 *
 * 想換成 Live：把 SID.fetchFixtures() 接到 API-Football / Sportmonks，
 * 回傳同樣結構即可，引擎與畫面完全不用改。
 * ===================================================================== */

const SID = window.SID || {};
window.SID = SID;

/* 賽事節奏基準：世界盃單隊場均 xG 約 1.30 */
SID.TOURNAMENT_AVG_XG = 1.30;

/* ---------------------------------------------------------------------
 * TEAM DATABASE｜球隊資料庫（基本面）
 * xg_for / xg_against = 賽前 12 個月加權場均。
 * form10 = 近 10 場 [勝, 平, 負]。  squad_value 單位：億歐元。
 * wc_exp / coach = 0~100 量化分。 ppda 越低壓迫越強。
 * ------------------------------------------------------------------- */
SID.teams = {
  ESP: { name: "西班牙", code: "ESP", flag: "🇪🇸", fifa: 2, elo: 2078,
    xg_for: 2.10, xg_against: 0.78, gf: 2.4, ga: 0.7, form10: [8, 1, 1],
    squad_value: 14.2, avg_age: 26.1, wc_exp: 88, coach: 86,
    possession: 64, ppda: 8.5, directness: 28, set_piece_xg: 0.34,
    corners_for: 6.4, corners_against: 3.1,
    style: "高位控球壓迫，邊路內收 + 中場滲透" },
  KSA: { name: "沙烏地阿拉伯", code: "KSA", flag: "🇸🇦", fifa: 61, elo: 1492,
    xg_for: 1.00, xg_against: 1.62, gf: 1.0, ga: 1.6, form10: [3, 3, 4],
    squad_value: 0.8, avg_age: 28.4, wc_exp: 52, coach: 60,
    possession: 45, ppda: 12.5, directness: 41, set_piece_xg: 0.22,
    corners_for: 4.1, corners_against: 5.6,
    style: "中低位防守 + 快速轉換，主力依賴邊路個人能力" },

  BEL: { name: "比利時", code: "BEL", flag: "🇧🇪", fifa: 9, elo: 1818,
    xg_for: 1.78, xg_against: 1.02, gf: 2.0, ga: 1.0, form10: [6, 2, 2],
    squad_value: 6.1, avg_age: 27.8, wc_exp: 80, coach: 72,
    possession: 57, ppda: 10.2, directness: 33, set_piece_xg: 0.30,
    corners_for: 5.7, corners_against: 3.9,
    style: "技術中場主導，依賴前場個人創造力，防線老化" },
  IRN: { name: "伊朗", code: "IRN", flag: "🇮🇷", fifa: 20, elo: 1689,
    xg_for: 1.12, xg_against: 0.85, gf: 1.2, ga: 0.8, form10: [5, 3, 2],
    squad_value: 1.2, avg_age: 28.0, wc_exp: 64, coach: 70,
    possession: 43, ppda: 13.8, directness: 47, set_piece_xg: 0.31,
    corners_for: 3.6, corners_against: 4.4,
    style: "深度低位鐵桶 + 定位球與反擊，紀律極高" },

  URU: { name: "烏拉圭", code: "URU", flag: "🇺🇾", fifa: 16, elo: 1886,
    xg_for: 1.62, xg_against: 0.96, gf: 1.7, ga: 0.9, form10: [6, 2, 2],
    squad_value: 4.8, avg_age: 27.2, wc_exp: 85, coach: 82,
    possession: 54, ppda: 9.8, directness: 36, set_piece_xg: 0.33,
    corners_for: 5.3, corners_against: 3.8,
    style: "高強度壓迫 + 兇悍對抗，Bielsa 體系高耗能" },
  CPV: { name: "維德角", code: "CPV", flag: "🇨🇻", fifa: 70, elo: 1503,
    xg_for: 1.05, xg_against: 1.40, gf: 1.1, ga: 1.4, form10: [4, 2, 4],
    squad_value: 0.6, avg_age: 27.5, wc_exp: 30, coach: 58,
    possession: 46, ppda: 12.0, directness: 44, set_piece_xg: 0.20,
    corners_for: 4.0, corners_against: 5.1,
    style: "首次世界盃黑馬，體能與身體素質好，經驗不足" },

  NZL: { name: "紐西蘭", code: "NZL", flag: "🇳🇿", fifa: 86, elo: 1451,
    xg_for: 0.92, xg_against: 1.48, gf: 1.0, ga: 1.5, form10: [4, 3, 3],
    squad_value: 0.4, avg_age: 27.9, wc_exp: 40, coach: 55,
    possession: 44, ppda: 13.0, directness: 49, set_piece_xg: 0.28,
    corners_for: 3.8, corners_against: 5.3,
    style: "身體流 + 定位球高空優勢，地面組織弱" },
  EGY: { name: "埃及", code: "EGY", flag: "🇪🇬", fifa: 32, elo: 1654,
    xg_for: 1.35, xg_against: 1.05, gf: 1.4, ga: 1.0, form10: [5, 3, 2],
    squad_value: 2.0, avg_age: 27.0, wc_exp: 58, coach: 66,
    possession: 52, ppda: 11.0, directness: 38, set_piece_xg: 0.26,
    corners_for: 5.0, corners_against: 4.2,
    style: "Salah 核心右路爆點，整體中規中矩偏反擊" },
};

/* ---------------------------------------------------------------------
 * PLAYER DATABASE｜球員資產（只列影響模型的核心 + 缺陣）
 * status: ok / doubt / out。 importance 0~1 = 對球隊 xG 影響權重。
 * role: ATT 影響進攻 λ；GK 影響失球 λ；DEF 影響被反擊與定位球。
 * ------------------------------------------------------------------- */
SID.players = {
  ESP: [
    { name: "Lamine Yamal", role: "ATT", importance: 0.30, status: "ok" },
    { name: "Pedri", role: "MID", importance: 0.22, status: "ok" },
    { name: "Unai Simón", role: "GK", importance: 0.18, status: "ok" },
  ],
  KSA: [
    { name: "Salem Al-Dawsari", role: "ATT", importance: 0.28, status: "doubt" },
    { name: "Mohammed Al-Owais", role: "GK", importance: 0.20, status: "ok" },
  ],
  BEL: [
    { name: "Kevin De Bruyne", role: "ATT", importance: 0.34, status: "doubt" },
    { name: "Jérémy Doku", role: "ATT", importance: 0.22, status: "ok" },
    { name: "Koen Casteels", role: "GK", importance: 0.16, status: "ok" },
  ],
  IRN: [
    { name: "Mehdi Taremi", role: "ATT", importance: 0.30, status: "ok" },
    { name: "Alireza Beiranvand", role: "GK", importance: 0.18, status: "ok" },
  ],
  URU: [
    { name: "Federico Valverde", role: "MID", importance: 0.30, status: "ok" },
    { name: "Darwin Núñez", role: "ATT", importance: 0.24, status: "ok" },
    { name: "Ronald Araújo", role: "DEF", importance: 0.20, status: "doubt" },
  ],
  CPV: [
    { name: "Ryan Mendes", role: "ATT", importance: 0.24, status: "ok" },
  ],
  NZL: [
    { name: "Chris Wood", role: "ATT", importance: 0.32, status: "ok" },
  ],
  EGY: [
    { name: "Mohamed Salah", role: "ATT", importance: 0.38, status: "ok" },
    { name: "Mohamed Elneny", role: "MID", importance: 0.16, status: "out" },
  ],
};

/* ---------------------------------------------------------------------
 * MATCH + ENVIRONMENT + MARKET DATABASE
 * kickoff_utc: ISO。 台灣時間由引擎用 Asia/Taipei 轉。
 * env: temp(°C) humidity(%) altitude(m) rest_days travel(km) timezone_diff
 * odds: 莊家十進位賠率（含抽水），引擎會去除 overround 算隱含機率。
 * ------------------------------------------------------------------- */
SID.matches = [
  {
    id: "WC2026-H-ESP-KSA",
    competition: "FIFA World Cup 2026", group: "H", stage: "小組賽 R2",
    home: "ESP", away: "KSA", neutral: true,
    stadium: "Mercedes-Benz Stadium", city: "Atlanta", country: "USA",
    kickoff_utc: "2026-06-21T16:00:00Z", referee: "Sıvkov (中性尺度)",
    env: { temp: 24, humidity: 55, altitude: 320, roof: true,
      rest_home: 4, rest_away: 4, travel_home: 1100, travel_away: 1400, tz_diff: 7 },
    odds: { home: 1.22, draw: 7.00, away: 13.0, over25: 1.40, under25: 2.90,
      btts_yes: 2.30, btts_no: 1.60, opening_home: 1.28, sharp: "home steam" },
  },
  {
    id: "WC2026-G-BEL-IRN",
    competition: "FIFA World Cup 2026", group: "G", stage: "小組賽 R2",
    home: "BEL", away: "IRN", neutral: true,
    stadium: "SoFi Stadium", city: "Inglewood", country: "USA",
    kickoff_utc: "2026-06-21T19:00:00Z", referee: "Faghani (偏嚴)",
    env: { temp: 26, humidity: 60, altitude: 40, roof: true,
      rest_home: 4, rest_away: 4, travel_home: 900, travel_away: 600, tz_diff: 9 },
    odds: { home: 1.58, draw: 3.80, away: 5.40, over25: 2.35, under25: 1.58,
      btts_yes: 2.15, btts_no: 1.65, opening_home: 1.44, sharp: "draw/away drift" },
  },
  {
    id: "WC2026-H-URU-CPV",
    competition: "FIFA World Cup 2026", group: "H", stage: "小組賽 R2",
    home: "URU", away: "CPV", neutral: true,
    stadium: "Hard Rock Stadium", city: "Miami Gardens", country: "USA",
    kickoff_utc: "2026-06-21T22:00:00Z", referee: "Ramos (中性)",
    env: { temp: 33, humidity: 78, altitude: 2, roof: false,
      rest_home: 4, rest_away: 4, travel_home: 1700, travel_away: 800, tz_diff: 6 },
    odds: { home: 1.42, draw: 4.50, away: 8.00, over25: 2.05, under25: 1.78,
      btts_yes: 2.10, btts_no: 1.70, opening_home: 1.50, sharp: "home steam" },
  },
  {
    id: "WC2026-G-NZL-EGY",
    competition: "FIFA World Cup 2026", group: "G", stage: "小組賽 R2",
    home: "NZL", away: "EGY", neutral: true,
    stadium: "BC Place", city: "Vancouver", country: "Canada",
    kickoff_utc: "2026-06-22T01:00:00Z", referee: "Taylor (放任流暢)",
    env: { temp: 19, humidity: 50, altitude: 5, roof: true,
      rest_home: 4, rest_away: 4, travel_home: 500, travel_away: 4000, tz_diff: 10 },
    odds: { home: 4.20, draw: 3.20, away: 1.95, over25: 2.10, under25: 1.74,
      btts_yes: 2.25, btts_no: 1.62, opening_home: 4.00, sharp: "away supported" },
  },
];

/* 資料來源標記（畫面顯示用） */
SID.meta = {
  source: "FIFA 官方賽程 + FIFA Ranking Jun 2026（內建快照）",
  snapshot: "2026-06-19",
  live: false,
};

/* ---------------------------------------------------------------------
 * fetchFixtures()｜Live 接口（預留）
 * 目前回傳內建快照。要接真實 API：在這裡 fetch() 後映射成 SID.matches 結構。
 * ------------------------------------------------------------------- */
SID.fetchFixtures = async function () {
  // 範例（需 API key 與網路）：
  // const r = await fetch("https://v3.football.api-sports.io/fixtures?...",
  //   { headers: { "x-apisports-key": KEY } });
  // const j = await r.json(); return mapApiFootball(j);
  return { matches: SID.matches, teams: SID.teams, meta: SID.meta };
};
