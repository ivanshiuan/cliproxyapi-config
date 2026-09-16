// 綠界 ECPay 全方位金流（AioCheckOut V5）— CheckMacValue 簽章/驗章 + 結帳表單
// 演算法（官方 SDK 相容，EncryptType=1 / SHA256）：
//   1. 排除 CheckMacValue，參數名依字典序（不分大小寫）排序
//   2. 串成 HashKey=..&k1=v1&..&HashIV=..
//   3. encodeURIComponent 後全轉小寫，再做 .NET UrlEncode 相容替換（%20→+ 等）
//   4. SHA256 → 大寫 hex

const STAGE_URL = "https://payment-stage.ecpay.com.tw/Cashier/AioCheckOut/V5";
const PROD_URL = "https://payment.ecpay.com.tw/Cashier/AioCheckOut/V5";

/** .NET HttpUtility.UrlEncode 相容編碼（綠界官方 Node SDK 同款替換表） */
export function dotNetUrlEncode(s) {
  return encodeURIComponent(s)
    .toLowerCase()
    .replace(/%2d/g, "-")
    .replace(/%5f/g, "_")
    .replace(/%2e/g, ".")
    .replace(/%21/g, "!")
    .replace(/%2a/g, "*")
    .replace(/%28/g, "(")
    .replace(/%29/g, ")")
    .replace(/%20/g, "+");
}

async function sha256Hex(text) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("").toUpperCase();
}

/** 產生 CheckMacValue。params 不含 CheckMacValue 本身。 */
export async function checkMacValue(params, hashKey, hashIv) {
  const keys = Object.keys(params)
    .filter((k) => k.toLowerCase() !== "checkmacvalue")
    .sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase(), "en"));
  const query = keys.map((k) => `${k}=${params[k]}`).join("&");
  const raw = `HashKey=${hashKey}&${query}&HashIV=${hashIv}`;
  return sha256Hex(dotNetUrlEncode(raw));
}

/** 驗證綠界回傳（webhook / result）的 CheckMacValue */
export async function verifyReturn(params, hashKey, hashIv) {
  const given = params.CheckMacValue || params.checkMacValue;
  if (!given) return false;
  const expected = await checkMacValue(params, hashKey, hashIv);
  return expected === String(given).toUpperCase();
}

function tradeDate(ts = Date.now()) {
  // 綠界要求台北時間 yyyy/MM/dd HH:mm:ss
  const d = new Date(ts + 8 * 3600_000);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getUTCFullYear()}/${p(d.getUTCMonth() + 1)}/${p(d.getUTCDate())} ${p(d.getUTCHours())}:${p(d.getUTCMinutes())}:${p(d.getUTCSeconds())}`;
}

/**
 * 建立結帳參數 + 自動送出表單 HTML。
 * method: 'Credit' | 'ATM' | 'ALL'
 */
export async function buildCheckout(env, { merchantTradeNo, amountTwd, itemName, method, userId }) {
  const apiUrl = env.ECPAY_ENV === "production" ? PROD_URL : STAGE_URL;
  const workerOrigin = env.WORKER_ORIGIN || ""; // 部署後於 vars 設定，如 https://car16-api.xxx.workers.dev
  const params = {
    MerchantID: env.ECPAY_MERCHANT_ID,
    MerchantTradeNo: merchantTradeNo,
    MerchantTradeDate: tradeDate(),
    PaymentType: "aio",
    TotalAmount: String(amountTwd),
    TradeDesc: "car16 plan pass",
    ItemName: itemName,
    ReturnURL: `${workerOrigin}/api/ecpay/return`,
    OrderResultURL: `${env.SITE_ORIGIN}/pay/result.html`,
    ClientBackURL: `${env.SITE_ORIGIN}/dashboard.html`,
    ChoosePayment: method || "ALL",
    EncryptType: "1",
    CustomField1: String(userId),
    ExpireDate: "3", // ATM 取號後 3 天內有效
    NeedExtraPaidInfo: "N",
  };
  params.CheckMacValue = await checkMacValue(params, env.ECPAY_HASH_KEY, env.ECPAY_HASH_IV);

  const inputs = Object.entries(params)
    .map(([k, v]) => `<input type="hidden" name="${k}" value="${String(v).replace(/"/g, "&quot;")}">`)
    .join("\n");
  const formHtml = `<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><title>前往付款…</title>
<body style="font-family:sans-serif;text-align:center;padding-top:20vh">
<p>正在前往綠界安全付款頁…</p>
<form id="f" method="post" action="${apiUrl}">
${inputs}
</form>
<script>document.getElementById('f').submit()</script>
</body></html>`;
  return { params, formHtml };
}
