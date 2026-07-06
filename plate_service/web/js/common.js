/* 選牌通 — 共用工具：導覽列、資料載入、車牌渲染、收藏 */

// ---- 手機導覽 ----
(function () {
  const toggle = document.querySelector(".nav-toggle");
  const links = document.querySelector(".nav-links");
  if (!toggle || !links) return;
  toggle.addEventListener("click", () => {
    const open = links.classList.toggle("open");
    toggle.setAttribute("aria-expanded", String(open));
  });
  links.addEventListener("click", (e) => {
    if (e.target.tagName === "A") {
      links.classList.remove("open");
      toggle.setAttribute("aria-expanded", "false");
    }
  });
})();

// ---- 資料載入 ----
async function loadJSON(name) {
  const res = await fetch(`data/${name}.json`);
  if (!res.ok) throw new Error(`載入 ${name} 失敗：HTTP ${res.status}`);
  return res.json();
}

// ---- 車牌 HTML ----
function plateHTML(item) {
  return `<span class="plate ${item.type}">${item.plate}</span>`;
}

function luckyBadge(score) {
  if (score >= 80) return `<span class="badge badge-lucky">🌟 吉祥度 ${score}</span>`;
  if (score >= 65) return `<span class="badge badge-lucky">吉祥度 ${score}</span>`;
  return "";
}

function fmtMoney(n) {
  return "NT$" + Number(n).toLocaleString("zh-Hant-TW");
}

function fmtDate(iso) {
  const [y, m, d] = iso.split("-");
  return `${m}/${d}`;
}

function daysUntil(iso) {
  const today = new Date("2026-07-06T00:00:00+08:00"); // demo 基準日；正式版用 new Date()
  const diff = (new Date(iso + "T23:59:59+08:00") - today) / 86400000;
  return Math.max(0, Math.ceil(diff));
}

// ---- 收藏（localStorage）----
const FAV_KEY = "plategoFavs";

function getFavs() {
  try {
    return new Set(JSON.parse(localStorage.getItem(FAV_KEY) || "[]"));
  } catch {
    return new Set();
  }
}

function toggleFav(plate) {
  const favs = getFavs();
  favs.has(plate) ? favs.delete(plate) : favs.add(plate);
  localStorage.setItem(FAV_KEY, JSON.stringify([...favs]));
  return favs;
}
