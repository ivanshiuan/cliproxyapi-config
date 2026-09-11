// 全站設定 — 部署後把 API_BASE 換成 Worker 正式網址
window.CAR16 = {
  API_BASE: localStorage.getItem("car16_api_base") || "https://car16-api.workers.dev",
  LINE_URL: "https://line.me/R/ti/p/@car16", // TODO: 換成真實官方帳號
  PLANS: { consumer: "一般版", pro: "業務專業版", dealer: "車商版" },
};
