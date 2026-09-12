/* 選牌通 — 標牌競標頁 */

(async function () {
  const list = document.getElementById("auction-list");
  if (!list) return;

  const $ = (id) => document.getElementById(id);
  let auctions = [];
  let records = [];

  try {
    [auctions, records] = await Promise.all([loadJSON("auctions"), loadJSON("records")]);
  } catch (err) {
    list.innerHTML = `<div class="empty-state">資料載入失敗（${err.message}）。請用本機伺服器開啟：
      <code>python3 -m http.server -d plate_service/web</code></div>`;
    return;
  }

  // hero 統計
  $("stat-biding").textContent = auctions.length;
  $("stat-closing").textContent = auctions.filter((a) => daysUntil(a.endDate) <= 3).length;
  const vol = records.reduce((s, r) => s + r.finalPrice, 0);
  $("stat-volume").textContent = (vol / 10000).toFixed(0) + " 萬";

  // 成交行情表
  const tbody = document.getElementById("records-body");
  tbody.innerHTML = records
    .map(
      (r) => `<tr>
        <td><b>${r.plate}</b></td>
        <td>${r.station}</td>
        <td>${r.closeDate}</td>
        <td>${r.bidCount} 次</td>
        <td class="price">${fmtMoney(r.finalPrice)}</td>
      </tr>`
    )
    .join("");

  function render() {
    const region = $("a-region").value;
    const type = $("a-type").value;
    const sort = $("a-sort").value;

    let rows = auctions.filter(
      (a) => (!region || a.region === region) && (!type || a.type === type)
    );

    if (sort === "end") rows.sort((a, b) => a.endDate.localeCompare(b.endDate));
    if (sort === "bid") rows.sort((a, b) => b.currentBid - a.currentBid);
    if (sort === "lucky") rows.sort((a, b) => b.luckyScore - a.luckyScore);

    $("a-count").textContent = `共 ${rows.length} 面競標中號牌`;
    $("a-empty").classList.toggle("hidden", rows.length > 0);

    list.innerHTML = rows
      .map((a) => {
        const days = daysUntil(a.endDate);
        const closing = days <= 3;
        return `<div class="auction-card">
          <div class="a-plate">
            ${plateHTML(a)}
            <div style="margin-top:6px;">${luckyBadge(a.luckyScore)}</div>
          </div>
          <div class="a-info">
            <div>標售單位<b>${a.station}</b></div>
            <div>底價<b>${fmtMoney(a.basePrice)}</b></div>
            <div class="a-bid">目前出價<b>${fmtMoney(a.currentBid)}</b>
              <span style="font-size:.78rem;">（${a.bidCount} 次出價）</span></div>
            <div>截止<b>${fmtDate(a.endDate)}
              <span class="badge ${closing ? "badge-hot" : "badge-open"}">
                ${days === 0 ? "今日截止" : `剩 ${days} 天`}</span></b></div>
          </div>
          <div class="a-actions">
            <a class="btn btn-navy btn-sm"
               href="https://www.mvdis.gov.tw/m3-emv-plate/bid/queryBiding"
               target="_blank" rel="noopener">監理服務網出價</a>
            <a class="btn btn-gold btn-sm"
               href="https://line.me/R/ti/p/@plategoTW?text=${encodeURIComponent("我想委託代標 " + a.plate)}"
               target="_blank" rel="noopener">委託代標</a>
          </div>
        </div>`;
      })
      .join("");
  }

  for (const id of ["a-region", "a-type", "a-sort"]) {
    $(id).addEventListener("change", render);
  }
  render();
})();
