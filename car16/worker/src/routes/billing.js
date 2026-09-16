// 結帳 + 綠界 webhook。權益開通只信 webhook，前端結果頁純顯示用。
import { json, err, merchantTradeNo, nowIso } from "../util.js";
import { getUser } from "../auth.js";
import { buildCheckout, verifyReturn } from "../ecpay.js";

export async function checkout(request, env) {
  const db = env.DB;
  const user = await getUser(request, db);
  if (!user) return err(401, "unauthorized", "請先登入再購買方案");

  const body = await request.json().catch(() => null);
  const planId = String(body?.plan_id || "");
  const method = ["Credit", "ATM", "ALL"].includes(body?.method) ? body.method : "ALL";
  const plan = await db.prepare("SELECT id, name, price_twd FROM plans WHERE id = ?").bind(planId).first();
  if (!plan) return err(400, "bad_plan", "方案不存在");

  const tradeNo = merchantTradeNo();
  await db
    .prepare(
      "INSERT INTO payments (merchant_trade_no, user_id, plan_id, amount_twd, method, status) VALUES (?, ?, ?, ?, ?, 'pending')"
    )
    .bind(tradeNo, user.id, plan.id, plan.price_twd, method)
    .run();

  const { formHtml } = await buildCheckout(env, {
    merchantTradeNo: tradeNo,
    amountTwd: plan.price_twd,
    itemName: `car16 ${plan.name} 30天`,
    method,
    userId: user.id,
  });
  // 前端拿 html 直接 document.write（避免跨域 form 組裝細節散落前端）
  return json({ merchant_trade_no: tradeNo, checkout_html: formHtml });
}

/** 綠界 server-to-server webhook（form-urlencoded）。必須回純文字 1|OK。 */
export async function ecpayReturn(request, env) {
  const db = env.DB;
  const form = await request.formData();
  const params = {};
  for (const [k, v] of form.entries()) params[k] = String(v);

  const valid = await verifyReturn(params, env.ECPAY_HASH_KEY, env.ECPAY_HASH_IV);
  if (!valid) return new Response("0|CheckMacValue Error", { status: 400 });

  const tradeNo = params.MerchantTradeNo;
  const payment = await db.prepare("SELECT * FROM payments WHERE merchant_trade_no = ?").bind(tradeNo).first();
  if (!payment) return new Response("0|Unknown Order", { status: 404 });

  const rtnCode = String(params.RtnCode);
  const raw = JSON.stringify(params);

  if (rtnCode === "2" || rtnCode === "10100073") {
    // ATM 取號成功（尚未付款）：存虛擬帳號資訊，維持 pending
    await db
      .prepare(
        `UPDATE payments SET method='ATM', atm_bank_code=?, atm_v_account=?, atm_expire_date=?, raw_return_json=?
          WHERE merchant_trade_no=?`
      )
      .bind(params.BankCode || null, params.vAccount || null, params.ExpireDate || null, raw, tradeNo)
      .run();
    return new Response("1|OK");
  }

  if (rtnCode === "1") {
    // 冪等：已 paid 就不重複開通
    if (payment.status !== "paid") {
      await db
        .prepare(
          `UPDATE payments SET status='paid', paid_at=?, ecpay_trade_no=?, method=COALESCE(?, method), raw_return_json=?
            WHERE merchant_trade_no=?`
        )
        .bind(nowIso(), params.TradeNo || null, params.PaymentType ? params.PaymentType.split("_")[0] : null, raw, tradeNo)
        .run();
      await activateSubscription(db, payment);
    }
    return new Response("1|OK");
  }

  // 其他 = 失敗
  await db
    .prepare("UPDATE payments SET status='failed', raw_return_json=? WHERE merchant_trade_no=? AND status='pending'")
    .bind(raw, tradeNo)
    .run();
  return new Response("1|OK"); // 已收到並處理，回 OK 避免綠界重送
}

/** 開通或展延 30 天：現有同方案未到期 → 從到期日續 30 天；否則從現在起算 */
async function activateSubscription(db, payment) {
  const plan = await db.prepare("SELECT duration_days FROM plans WHERE id = ?").bind(payment.plan_id).first();
  const days = plan ? plan.duration_days : 30;
  const current = await db
    .prepare(
      `SELECT id, expires_at FROM subscriptions
        WHERE user_id=? AND plan_id=? AND status='active' AND expires_at > datetime('now')
        ORDER BY expires_at DESC LIMIT 1`
    )
    .bind(payment.user_id, payment.plan_id)
    .first();

  if (current) {
    await db
      .prepare(`UPDATE subscriptions SET expires_at = datetime(expires_at, '+' || ? || ' days') WHERE id = ?`)
      .bind(days, current.id)
      .run();
  } else {
    await db
      .prepare(
        `INSERT INTO subscriptions (user_id, plan_id, starts_at, expires_at, payment_id, status)
         VALUES (?, ?, datetime('now'), datetime('now', '+' || ? || ' days'), ?, 'active')`
      )
      .bind(payment.user_id, payment.plan_id, days, payment.id)
      .run();
  }
}

export async function myBilling(request, env) {
  const db = env.DB;
  const user = await getUser(request, db);
  if (!user) return err(401, "unauthorized", "請先登入");
  const payments = await db
    .prepare(
      `SELECT merchant_trade_no, plan_id, amount_twd, method, status,
              atm_bank_code, atm_v_account, atm_expire_date, created_at, paid_at
         FROM payments WHERE user_id = ? ORDER BY created_at DESC LIMIT 50`
    )
    .bind(user.id)
    .all();
  const subs = await db
    .prepare(
      "SELECT plan_id, starts_at, expires_at, status FROM subscriptions WHERE user_id = ? ORDER BY expires_at DESC LIMIT 10"
    )
    .bind(user.id)
    .all();
  return json({ payments: payments.results, subscriptions: subs.results });
}

export async function listPlans(_request, env) {
  const rows = await env.DB.prepare("SELECT id, name, price_twd, duration_days, features_json FROM plans").all();
  return json({
    plans: rows.results.map((p) => ({ ...p, features: JSON.parse(p.features_json), features_json: undefined })),
  });
}
