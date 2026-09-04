#!/usr/bin/env node
/* 備援攝取推送器 — 當 Cloudflare Worker 直抓 mvdis 被擋時，
 * 從本機 / GitHub Actions（住宅或一般 IP）抓資料後推給 Worker。
 *
 * 用法：
 *   API_BASE=https://car16-api.xxx.workers.dev INGEST_TOKEN=xxx node scripts/ingest-fallback.mjs
 *   node scripts/ingest-fallback.mjs --file rows.json --kind auction   # 推手動整理的 JSON
 *
 * rows.json 格式（標準列，見 worker/src/ingest/normalize.js）：
 *   [{ "region": "臺北市區監理所", "plate_no": "BXP-8888", "phase": "bidding",
 *      "base_price_twd": 3000, "bid_start": "2026/07/01", "bid_end": "2026/07/15" }]
 */
const API_BASE = process.env.API_BASE || "http://localhost:8787";
const TOKEN = process.env.INGEST_TOKEN || "";

const args = process.argv.slice(2);
const flag = (name) => {
  const i = args.indexOf(`--${name}`);
  return i >= 0 ? args[i + 1] : null;
};

async function push(kind, rows) {
  const res = await fetch(`${API_BASE}/api/ingest`, {
    method: "POST",
    headers: { "content-type": "application/json", authorization: `Bearer ${TOKEN}` },
    body: JSON.stringify({ kind, rows }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(`push ${kind} HTTP ${res.status}: ${JSON.stringify(data)}`);
  console.log(`✓ ${kind}: upserted ${data.upserted}`);
}

async function fetchMvdis(path) {
  const res = await fetch(`https://www.mvdis.gov.tw/m3-emv-plate${path}`, {
    headers: {
      "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
      "accept-language": "zh-TW,zh;q=0.9",
    },
  });
  if (!res.ok) throw new Error(`mvdis ${path} HTTP ${res.status}`);
  return res.text();
}

function extractPlates(html) {
  const found = new Set();
  const re = /\b([A-Z]{2,4}-\d{2,4}|\d{2,4}-[A-Z]{2,4}|[A-Z0-9]{3}-\d{4})\b/g;
  let m;
  while ((m = re.exec(html)) !== null) found.add(m[1].toUpperCase());
  return [...found];
}

const fileArg = flag("file");
if (fileArg) {
  // 手動模式：推整理好的 JSON
  const { readFileSync } = await import("node:fs");
  const rows = JSON.parse(readFileSync(fileArg, "utf8"));
  await push(flag("kind") || "auction", rows);
} else {
  // 自動模式：抓公告/競標中頁 → 保底解析 → 推送
  for (const [kind, path, phase] of [
    ["auction", "/bid/announce", "announced"],
    ["bidding", "/bid/queryBiding", "bidding"],
  ]) {
    try {
      const html = await fetchMvdis(path);
      const rows = extractPlates(html).map((plate_no) => ({ region: "監理服務網", plate_no, phase, bid_start: "" }));
      console.log(`${kind}: 抽出 ${rows.length} 個號碼`);
      if (rows.length) await push(kind, rows);
    } catch (e) {
      console.error(`✗ ${kind}: ${e.message}`);
      process.exitCode = 1;
    }
  }
}
