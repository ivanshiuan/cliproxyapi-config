// node --test — CheckMacValue 簽章不變量測試
// （綠界正確性最終以 stage 環境實測為準：簽錯 stage 會回 10200073 CheckMacValue Error）
import { test } from "node:test";
import assert from "node:assert/strict";
import { checkMacValue, verifyReturn, dotNetUrlEncode } from "../src/ecpay.js";

const KEY = "5294y06JbISpM5x9";
const IV = "v77hoKGq4kWxNNIS";

const sampleParams = () => ({
  MerchantID: "2000132",
  MerchantTradeNo: "C16TEST0001",
  MerchantTradeDate: "2026/07/12 10:00:00",
  PaymentType: "aio",
  TotalAmount: "149",
  TradeDesc: "car16 plan pass",
  ItemName: "car16 一般版 30天",
  ReturnURL: "https://example.com/api/ecpay/return",
  ChoosePayment: "ALL",
  EncryptType: "1",
});

test("dotNetUrlEncode：空白轉 +、保留 -_.!*()、其餘小寫百分比編碼", () => {
  assert.equal(dotNetUrlEncode("a b"), "a+b");
  assert.equal(dotNetUrlEncode("A-B_c.d!e*f(g)"), "a-b_c.d!e*f(g)");
  assert.equal(dotNetUrlEncode("x/y"), "x%2fy");
});

test("checkMacValue：64 位大寫 hex、確定性", async () => {
  const p = sampleParams();
  const a = await checkMacValue(p, KEY, IV);
  const b = await checkMacValue(p, KEY, IV);
  assert.match(a, /^[0-9A-F]{64}$/);
  assert.equal(a, b);
});

test("checkMacValue：參數順序無關（排序後簽章）", async () => {
  const p = sampleParams();
  const reversed = Object.fromEntries(Object.entries(p).reverse());
  assert.equal(await checkMacValue(p, KEY, IV), await checkMacValue(reversed, KEY, IV));
});

test("checkMacValue：金額竄改 → 簽章改變", async () => {
  const p = sampleParams();
  const original = await checkMacValue(p, KEY, IV);
  const tampered = await checkMacValue({ ...p, TotalAmount: "1" }, KEY, IV);
  assert.notEqual(original, tampered);
});

test("verifyReturn：正確簽章通過、竄改被拒、缺簽章被拒", async () => {
  const p = sampleParams();
  p.CheckMacValue = await checkMacValue(p, KEY, IV);
  assert.equal(await verifyReturn(p, KEY, IV), true);
  assert.equal(await verifyReturn({ ...p, TotalAmount: "999999" }, KEY, IV), false);
  const { CheckMacValue: _omit, ...noMac } = p;
  assert.equal(await verifyReturn(noMac, KEY, IV), false);
});

test("checkMacValue：CheckMacValue 本身不參與簽章", async () => {
  const p = sampleParams();
  const clean = await checkMacValue(p, KEY, IV);
  const withMac = await checkMacValue({ ...p, CheckMacValue: "GARBAGE" }, KEY, IV);
  assert.equal(clean, withMac);
});
