// 會員中心
(async function () {
  if (!api.token()) { location.href = "login.html?next=dashboard.html"; return; }
  const $ = (id) => document.getElementById(id);

  // 方案
  let me;
  try {
    me = await api.get("/api/me");
    if (!me.user) { api.setToken(null); location.href = "login.html?next=dashboard.html"; return; }
  } catch { location.href = "login.html?next=dashboard.html"; return; }

  $("plan-info").innerHTML =
    me.tier === "free"
      ? `目前是 <b>免費會員</b>（每日 10 次查詢）。<a href="index.html#pricing">升級方案</a> 解鎖不限次查詢與號碼追蹤。`
      : `<b>${CAR16.PLANS[me.tier] || me.tier}</b> · 效期至 <b>${me.plan_expires_at}</b>（UTC）
         <div style="margin-top:10px"><a class="btn sm" href="index.html#pricing">續費 / 升級</a></div>`;

  // 追蹤清單
  async function loadWatchlist() {
    try {
      const d = await api.get("/api/me/watchlist");
      $("wl-count").textContent = `（${d.results.length} / ${me.features.watchlist} 組）`;
      $("wl-rows").innerHTML = d.results.length
        ? d.results.map((w) => `<tr>
            <td class="plate">${w.pattern}</td><td class="muted">${w.region || "全部監理所"}</td>
            <td class="muted">${w.vehicle_type || "全部車種"}</td>
            <td style="text-align:right"><button class="btn ghost sm wl-del" data-id="${w.id}">移除</button></td>
          </tr>`).join("")
        : `<tr><td class="muted">尚無追蹤 — 在查詢結果按「追蹤」或於上方直接新增</td></tr>`;
      document.querySelectorAll(".wl-del").forEach((b) => {
        b.onclick = async () => { await api.del(`/api/me/watchlist/${b.dataset.id}`); loadWatchlist(); };
      });
    } catch (e) {
      $("wl-rows").innerHTML = `<tr><td class="muted">${e.message}</td></tr>`;
    }
  }
  $("wl-add").onclick = async () => {
    $("wl-err").textContent = "";
    try {
      await api.post("/api/me/watchlist", { pattern: $("wl-pattern").value });
      $("wl-pattern").value = "";
      loadWatchlist();
    } catch (e) {
      $("wl-err").textContent = e.message;
      if (e.code === "upgrade_required") $("wl-err").innerHTML += ' — <a href="index.html#pricing">看方案</a>';
    }
  };
  loadWatchlist();

  // 代辦委託
  const KIND = { pickno: "選號代辦", bid: "競標代辦", support: "客服" };
  const AG_STATUS = { new: "已收到", contacted: "已聯繫", assigned: "處理中", done: "已完成", dropped: "已取消" };
  try {
    const d = await api.get("/api/me/agency-requests");
    $("ag-rows").innerHTML = d.results.length
      ? d.results.map((r) => `<tr>
          <td>${KIND[r.kind] || r.kind}</td><td class="plate">${r.plate_pattern || "—"}</td>
          <td><span class="badge ${r.status === "done" ? "paid" : "free"}">${AG_STATUS[r.status] || r.status}</span></td>
          <td class="muted">${r.created_at}</td>
        </tr>`).join("")
      : '<tr><td colspan="4" class="muted">尚無委託 — <a href="agency.html">建立第一筆</a></td></tr>';
  } catch { /* ignore */ }

  // 付款紀錄
  const PAY_STATUS = { pending: "待付款", paid: "已付款", failed: "失敗", expired: "逾期" };
  try {
    const d = await api.get("/api/me/billing");
    $("pay-rows").innerHTML = d.payments.length
      ? d.payments.map((p) => {
          const atm = p.status === "pending" && p.atm_v_account
            ? `<div class="muted" style="font-size:12.5px">ATM：${p.atm_bank_code} ${p.atm_v_account}（${p.atm_expire_date} 前）</div>` : "";
          return `<tr>
            <td class="muted" style="font-size:12.5px">${p.merchant_trade_no}</td>
            <td>${CAR16.PLANS[p.plan_id] || p.plan_id}</td><td>NT$${p.amount_twd}</td><td>${p.method || "—"}</td>
            <td><span class="badge ${p.status === "paid" ? "paid" : p.status === "pending" ? "warn" : "free"}">${PAY_STATUS[p.status] || p.status}</span>${atm}</td>
            <td class="muted">${p.created_at}</td>
          </tr>`;
        }).join("")
      : '<tr><td colspan="6" class="muted">尚無付款紀錄</td></tr>';
  } catch { /* ignore */ }
})();
