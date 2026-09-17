/* 選牌通 — 首頁：可選號牌查詢 */

(async function () {
  const grid = document.getElementById("plate-grid");
  if (!grid) return;

  const PAGE = 24;
  let allPlates = [];
  let filtered = [];
  let shown = 0;
  const quickOn = new Set();

  const $ = (id) => document.getElementById(id);

  try {
    const [plates, stations, auctions] = await Promise.all([
      loadJSON("plates"),
      loadJSON("stations"),
      loadJSON("auctions"),
    ]);
    allPlates = plates;

    // hero 統計
    $("stat-plates").textContent = plates.length.toLocaleString();
    $("stat-stations").textContent = stations.length;
    $("stat-auctions").textContent = auctions.length;

    // 監理所站下拉
    const sel = $("f-station");
    for (const st of stations) {
      const opt = document.createElement("option");
      opt.value = st.name;
      opt.textContent = st.name;
      sel.appendChild(opt);
    }
  } catch (err) {
    grid.innerHTML = `<div class="empty-state" style="grid-column:1/-1;">
      資料載入失敗（${err.message}）。請用本機伺服器開啟：
      <code>python3 -m http.server -d plate_service/web</code></div>`;
    return;
  }

  // ---- 篩選邏輯 ----
  const digitsOf = (p) => p.plate.split("-")[1];

  const quickTests = {
    has8: (p) => digitsOf(p).includes("8"),
    no4: (p) => !digitsOf(p).includes("4"),
    repeat: (p) => {
      const d = digitsOf(p);
      return new Set(d).size <= 2 && /(.)\1/.test(d);
    },
    straight: (p) =>
      ["0123", "1234", "2345", "3456", "4567", "5678", "6789"].includes(digitsOf(p)),
    mirror: (p) => {
      const d = digitsOf(p);
      return d === d.split("").reverse().join("");
    },
    lucky80: (p) => p.luckyScore >= 80,
  };

  function applyFilters() {
    const type = $("f-type").value;
    const station = $("f-station").value;
    const region = $("f-region").value;
    const kw = $("f-keyword").value.trim();
    const mode = $("f-mode").value;

    filtered = allPlates.filter((p) => {
      if (type && p.type !== type) return false;
      if (station && p.station !== station) return false;
      if (region && p.region !== region) return false;
      if (kw) {
        const d = digitsOf(p);
        if (mode === "starts" && !d.startsWith(kw)) return false;
        if (mode === "ends" && !d.endsWith(kw)) return false;
        if (mode === "contains" && !d.includes(kw)) return false;
      }
      for (const q of quickOn) if (!quickTests[q](p)) return false;
      return true;
    });

    const sort = $("f-sort").value;
    if (sort === "lucky") filtered.sort((a, b) => b.luckyScore - a.luckyScore);
    if (sort === "date") filtered.sort((a, b) => a.openDate.localeCompare(b.openDate));
    if (sort === "plate") filtered.sort((a, b) => a.plate.localeCompare(b.plate));

    shown = 0;
    grid.innerHTML = "";
    renderMore();
  }

  function renderMore() {
    const favs = getFavs();
    const batch = filtered.slice(shown, shown + PAGE);
    const frag = document.createDocumentFragment();

    for (const p of batch) {
      const card = document.createElement("div");
      card.className = "plate-card";
      card.innerHTML = `
        <button class="fav-btn ${favs.has(p.plate) ? "on" : ""}" data-plate="${p.plate}"
                aria-label="收藏 ${p.plate}">★</button>
        ${plateHTML(p)}
        <div class="meta">
          <strong>${p.station}</strong><br>
          ${p.typeLabel} · ${fmtDate(p.openDate)} 起可選<br>
          ${luckyBadge(p.luckyScore)}
        </div>`;
      frag.appendChild(card);
    }

    grid.appendChild(frag);
    shown += batch.length;

    $("result-count").textContent = `共 ${filtered.length.toLocaleString()} 面可選號牌，顯示 ${shown} 面`;
    $("empty-state").classList.toggle("hidden", filtered.length > 0);
    $("load-more").classList.toggle("hidden", shown >= filtered.length);
    updateFavCount();
  }

  function updateFavCount() {
    $("fav-count").textContent = getFavs().size;
  }

  // ---- 事件 ----
  for (const id of ["f-type", "f-station", "f-region", "f-mode", "f-sort"]) {
    $(id).addEventListener("change", applyFilters);
  }
  $("f-keyword").addEventListener("input", applyFilters);
  $("load-more").addEventListener("click", renderMore);

  document.getElementById("quick-chips").addEventListener("click", (e) => {
    const btn = e.target.closest(".chip");
    if (!btn) return;
    const key = btn.dataset.quick;
    quickOn.has(key) ? quickOn.delete(key) : quickOn.add(key);
    btn.classList.toggle("on");
    applyFilters();
  });

  grid.addEventListener("click", (e) => {
    const btn = e.target.closest(".fav-btn");
    if (!btn) return;
    const favs = toggleFav(btn.dataset.plate);
    btn.classList.toggle("on", favs.has(btn.dataset.plate));
    updateFavCount();
  });

  applyFilters();
})();
