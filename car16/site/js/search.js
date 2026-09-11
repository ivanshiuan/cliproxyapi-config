// 選號查詢頁
(async function () {
  const $ = (id) => document.getElementById(id);
  let lastResults = [];
  let canWatch = false;
  let canExport = false;

  // 下拉選單資料 + 目前權益
  try {
    const f = await api.get("/api/plates/facets");
    for (const r of f.regions) $("region").insertAdjacentHTML("beforeend", `<option>${r}</option>`);
    for (const t of f.vehicle_types) $("vehicle_type").insertAdjacentHTML("beforeend", `<option>${t}</option>`);
  } catch { /* 無資料時保持「全部」 */ }
  if (api.token()) {
    try {
      const me = await api.get("/api/me");
      canWatch = me.features.watchlist > 0;
      canExport = me.features.export;
    } catch { /* 未登入視同 free */ }
  }

  async function run() {
    $("err").textContent = "";
    const q = new URLSearchParams();
    if ($("pattern").value.trim()) q.set("pattern", $("pattern").value.trim());
    if ($("region").value) q.set("region", $("region").value);
    if ($("vehicle_type").value) q.set("vehicle_type", $("vehicle_type").value);
    try {
      const d = await api.get(`/api/plates/search?${q}`);
      lastResults = d.results;
      render(d);
    } catch (e) {
      $("err").textContent = e.message;
      if (e.code === "quota_exceeded") $("err").innerHTML += ' — <a href="index.html#pricing">看方案</a>';
    }
  }

  function render(d) {
    const rows = $("rows");
    if (!d.results.length) {
      rows.innerHTML = '<tr><td colspan="4" class="muted">沒有符合的號碼 — 可到「委託代辦」留需求，或加入追蹤等到號通知</td></tr>';
    } else {
      rows.innerHTML = d.results
        .map(
          (r) => `<tr>
            <td class="plate">${r.plate_no}</td><td>${r.region}</td><td>${r.vehicle_type}</td>
            <td>${canWatch ? `<button class="btn ghost sm watch" data-p="${r.plate_no}">追蹤</button>` : ""}</td>
          </tr>`
        )
        .join("");
      rows.querySelectorAll(".watch").forEach((b) => {
        b.onclick = async () => {
          try {
            await api.post("/api/me/watchlist", { pattern: b.dataset.p });
            b.textContent = "已追蹤";
            b.disabled = true;
          } catch (e) { alert(e.message); }
        };
      });
    }
    $("capped").style.display = d.capped ? "block" : "none";
    $("export").style.display = canExport && d.results.length ? "inline-block" : "none";
    $("quota").textContent = d.quota_left >= 0 ? `今日剩餘免費查詢：${d.quota_left} 次` : "";
    const meta = $("meta");
    meta.style.display = "block";
    meta.textContent = d.data_as_of
      ? `資料時間：${d.data_as_of}（UTC）｜來源：監理服務網${d.stale ? "｜⚠ 資料可能非最新，選號前請以監理服務網為準" : ""}`
      : "資料庫尚無快取資料 — 攝取排程啟動後即有資料";
  }

  $("go").onclick = run;
  $("pattern").addEventListener("keydown", (e) => { if (e.key === "Enter") run(); });
  document.querySelectorAll(".lucky").forEach((b) => (b.onclick = () => { $("pattern").value = b.dataset.p; run(); }));

  $("export").onclick = () => {
    const csv = ["plate_no,region,vehicle_type", ...lastResults.map((r) => `${r.plate_no},${r.region},${r.vehicle_type}`)].join("\n");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob(["﻿" + csv], { type: "text/csv" }));
    a.download = "car16-search.csv";
    a.click();
  };
})();
