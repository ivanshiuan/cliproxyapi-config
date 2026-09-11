// 共用版頭/版尾/登入狀態 + 注入客服機器人（每頁引 ui.js 即可）
(function () {
  const here = location.pathname.split("/").pop() || "index.html";
  const NAV = [
    ["index.html", "首頁"],
    ["search.html", "選號查詢"],
    ["auction.html", "標牌競標"],
    ["agency.html", "委託代辦"],
    ["dashboard.html", "會員中心"],
  ];
  const base = location.pathname.includes("/legal/") || location.pathname.includes("/pay/") ? "../" : "";

  const header = document.createElement("header");
  header.className = "site";
  header.innerHTML = `<div class="wrap">
    <a class="logo" href="${base}index.html">car<b>16</b></a>
    <nav class="main">${NAV.map(([f, t]) => `<a href="${base}${f}" class="${f === here ? "active" : ""}">${t}</a>`).join("")}</nav>
    <div id="nav-auth"></div>
  </div>`;
  document.body.prepend(header);

  const footer = document.createElement("footer");
  footer.className = "site";
  footer.innerHTML = `
    <div>資料來源：監理服務網（mvdis.gov.tw）公開資料彙整 · 本站非公路監理機關官方網站</div>
    <div style="margin-top:6px">
      <a href="${base}legal/terms.html">服務條款</a>·<a href="${base}legal/privacy.html">隱私權政策</a>·<a href="${CAR16.LINE_URL}" target="_blank" rel="noopener">LINE 客服</a>
    </div>
    <div style="margin-top:6px">© car16 車牌選號平台</div>`;
  document.body.append(footer);

  // 登入狀態
  const slot = header.querySelector("#nav-auth");
  if (api.token()) {
    api.get("/api/me").then((d) => {
      if (!d.user) { api.setToken(null); render(null); return; }
      render(d);
    }).catch(() => render(null));
  } else render(null);

  function render(d) {
    if (d && d.user) {
      const badge = d.tier === "free"
        ? '<span class="badge free">免費</span>'
        : `<span class="badge paid">${CAR16.PLANS[d.tier] || d.tier}</span>`;
      slot.innerHTML = `${badge} <span class="muted">${d.user.email}</span> <button class="btn ghost sm" id="btn-logout">登出</button>`;
      slot.querySelector("#btn-logout").onclick = async () => {
        try { await api.post("/api/auth/logout"); } catch { /* token 失效也照樣清除 */ }
        api.setToken(null);
        location.href = `${base}index.html`;
      };
    } else {
      slot.innerHTML = `<a class="btn ghost sm" href="${base}login.html">登入</a><a class="btn sm" href="${base}login.html?mode=register">免費註冊</a>`;
    }
  }

  // 客服機器人（chatbot.js 已由各頁引入）
  if (window.initChatbot) window.initChatbot();
})();
