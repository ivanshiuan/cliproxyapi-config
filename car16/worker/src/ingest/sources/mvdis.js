// 監理服務網直抓來源（可插拔）。
//
// ⚠️ 校準說明：mvdis.gov.tw 從開發容器連不出去（egress 白名單），以下請求參數
// 是依公開頁面結構的最佳推測，**部署後用 GET /api/ingest/probe 實測並校準**。
// 若 Cloudflare IP 被擋 → 走備援：scripts/ingest-fallback.mjs 推 /api/ingest。

import { extractPlatesFromHtml } from "../normalize.js";

const BASE = "https://www.mvdis.gov.tw/m3-emv-plate";

// 監理所清單（名稱為準；deptCode 部署後校準）
export const REGIONS = [
  { name: "臺北市區監理所", deptCode: "" },
  { name: "臺北區監理所", deptCode: "" },
  { name: "新竹區監理所", deptCode: "" },
  { name: "臺中區監理所", deptCode: "" },
  { name: "嘉義區監理所", deptCode: "" },
  { name: "高雄市區監理所", deptCode: "" },
  { name: "高雄區監理所", deptCode: "" },
  { name: "花蓮區監理所", deptCode: "" },
];

export const VEHICLE_TYPES = [
  "自用小客車",
  "自用重型機車",
  "大型重型機車(550cc以上)",
  "大型重型機車(250-550cc)",
  "營業小客車",
];

const HEADERS = {
  "user-agent":
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
  accept: "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
  "accept-language": "zh-TW,zh;q=0.9",
};

async function get(path) {
  const res = await fetch(`${BASE}${path}`, { headers: HEADERS, redirect: "follow" });
  const text = await res.text();
  return { status: res.status, contentType: res.headers.get("content-type") || "", text };
}

/**
 * 探測三個端點的連通性與回應形態（校準用，由 /api/ingest/probe 呼叫）。
 * 只回摘要，不入庫。
 */
export async function probe() {
  const targets = [
    ["pickno", "/webpickno/queryPickNo"],
    ["auction", "/bid/announce"],
    ["bidding", "/bid/queryBiding"],
  ];
  const out = {};
  for (const [kind, path] of targets) {
    try {
      const r = await get(path);
      out[kind] = {
        status: r.status,
        contentType: r.contentType,
        bytes: r.text.length,
        sample: r.text.slice(0, 500),
        platesFound: extractPlatesFromHtml(r.text).slice(0, 10),
      };
    } catch (e) {
      out[kind] = { error: String(e && e.message ? e.message : e) };
    }
  }
  return out;
}

/**
 * 直抓標牌公告頁 → 盡力抽出車牌號（保底解析）。
 * 完整欄位（底價/日期）需依實際 DOM 校準後補；在那之前主要靠備援推送。
 */
export async function fetchAuctions() {
  const r = await get("/bid/announce");
  if (r.status !== 200) throw new Error(`mvdis announce HTTP ${r.status}`);
  const plates = extractPlatesFromHtml(r.text);
  return plates.map((plate_no) => ({ region: "監理服務網", plate_no, phase: "announced", bid_start: "" }));
}

export async function fetchBidding() {
  const r = await get("/bid/queryBiding");
  if (r.status !== 200) throw new Error(`mvdis bidding HTTP ${r.status}`);
  const plates = extractPlatesFromHtml(r.text);
  return plates.map((plate_no) => ({ region: "監理服務網", plate_no, phase: "bidding", bid_start: "" }));
}

/** 選號查詢需要 POST 表單參數（deptCode/carType），未校準前不啟用直抓。 */
export async function fetchPickNo() {
  return { unsupported: true, rows: [] };
}
