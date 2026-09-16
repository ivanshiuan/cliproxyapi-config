// 共用小工具：回應、錯誤、時間、亂數

export function json(data, status = 200, extra = {}) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", ...extra },
  });
}

export function err(status, code, message) {
  return json({ error: { code, message } }, status);
}

export function nowIso() {
  return new Date().toISOString().replace("T", " ").slice(0, 19); // 與 datetime('now') 同格式
}

export function addDays(days) {
  const d = new Date(Date.now() + days * 86400_000);
  return d.toISOString().replace("T", " ").slice(0, 19);
}

/** 台北時間的 yyyy-mm-dd（配額日界線用台北時區才符合用戶直覺） */
export function taipeiDate(ts = Date.now()) {
  return new Date(ts + 8 * 3600_000).toISOString().slice(0, 10);
}

export function randomHex(bytes = 32) {
  const buf = new Uint8Array(bytes);
  crypto.getRandomValues(buf);
  return [...buf].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** 綠界 MerchantTradeNo：≤20 字英數。C16 + base36 毫秒 + 4 碼亂數 */
export function merchantTradeNo() {
  const t = Date.now().toString(36).toUpperCase();
  const r = randomHex(2).toUpperCase();
  return `C16${t}${r}`.slice(0, 20);
}

/** D1 計數器 +1 並回傳新值（免費配額 / 限流共用） */
export async function bumpCounter(db, key) {
  await db
    .prepare(
      "INSERT INTO usage_counters (key, count) VALUES (?, 1) ON CONFLICT(key) DO UPDATE SET count = count + 1"
    )
    .bind(key)
    .run();
  const row = await db.prepare("SELECT count FROM usage_counters WHERE key = ?").bind(key).first();
  return row ? row.count : 1;
}

export function clientIp(request) {
  return request.headers.get("cf-connecting-ip") || "0.0.0.0";
}
