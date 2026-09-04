// 註冊 / 登入 / 登出 / me / watchlist
import { json, err, bumpCounter, clientIp } from "../util.js";
import { hashPassword, verifyPassword, createSession, getUser, destroySession } from "../auth.js";
import { resolveEntitlements } from "../entitlements.js";

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export async function register(request, env) {
  const db = env.DB;
  const body = await request.json().catch(() => null);
  if (!body) return err(400, "bad_json", "請提供 JSON body");
  const email = String(body.email || "").trim().toLowerCase();
  const password = String(body.password || "");
  if (!EMAIL_RE.test(email)) return err(400, "bad_email", "email 格式不正確");
  if (password.length < 8) return err(400, "weak_password", "密碼至少 8 個字元");

  const exists = await db.prepare("SELECT id FROM users WHERE email = ?").bind(email).first();
  if (exists) return err(409, "email_taken", "這個 email 已註冊過，請直接登入");

  const hash = await hashPassword(password);
  const res = await db
    .prepare("INSERT INTO users (email, password_hash, display_name, phone) VALUES (?, ?, ?, ?)")
    .bind(email, hash, body.display_name || null, body.phone || null)
    .run();
  const userId = res.meta.last_row_id;
  const token = await createSession(db, userId);
  return json({ token, user: { id: userId, email } }, 201);
}

export async function login(request, env) {
  const db = env.DB;
  // 登入限流：每 IP 每小時 20 次嘗試
  const hourKey = `login:${clientIp(request)}:${new Date().toISOString().slice(0, 13)}`;
  if ((await bumpCounter(db, hourKey)) > 20) return err(429, "too_many_attempts", "嘗試次數過多，請一小時後再試");

  const body = await request.json().catch(() => null);
  if (!body) return err(400, "bad_json", "請提供 JSON body");
  const email = String(body.email || "").trim().toLowerCase();
  const row = await db.prepare("SELECT id, password_hash FROM users WHERE email = ?").bind(email).first();
  if (!row || !(await verifyPassword(String(body.password || ""), row.password_hash)))
    return err(401, "bad_credentials", "email 或密碼錯誤");
  const token = await createSession(db, row.id);
  return json({ token, user: { id: row.id, email } });
}

export async function logout(request, env) {
  await destroySession(request, env.DB);
  return json({ ok: true });
}

export async function me(request, env) {
  const db = env.DB;
  const user = await getUser(request, db);
  const ent = await resolveEntitlements(db, user?.id);
  return json({ user, tier: ent.tier, features: ent.features, plan_expires_at: ent.expires_at });
}

// ── watchlist（付費功能：features.watchlist > 0）──────────────────
export async function listWatchlist(request, env) {
  const db = env.DB;
  const user = await getUser(request, db);
  if (!user) return err(401, "unauthorized", "請先登入");
  const rows = await db
    .prepare("SELECT id, pattern, vehicle_type, region, created_at FROM watchlists WHERE user_id = ? ORDER BY created_at DESC")
    .bind(user.id)
    .all();
  return json({ results: rows.results });
}

export async function addWatchlist(request, env) {
  const db = env.DB;
  const user = await getUser(request, db);
  if (!user) return err(401, "unauthorized", "請先登入");
  const ent = await resolveEntitlements(db, user.id);
  if (ent.features.watchlist <= 0) return err(403, "upgrade_required", "追蹤清單是付費功能，請升級方案");

  const body = await request.json().catch(() => null);
  const pattern = String(body?.pattern || "").trim().toUpperCase();
  if (!/^[A-Z0-9*?-]{2,10}$/.test(pattern)) return err(400, "bad_pattern", "樣式格式不正確（可用 A-Z 0-9 * ? -）");

  const count = await db.prepare("SELECT COUNT(*) AS n FROM watchlists WHERE user_id = ?").bind(user.id).first();
  if (count.n >= ent.features.watchlist)
    return err(403, "watchlist_full", `你的方案追蹤上限為 ${ent.features.watchlist} 組`);

  await db
    .prepare(
      "INSERT INTO watchlists (user_id, pattern, vehicle_type, region) VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING"
    )
    .bind(user.id, pattern, body?.vehicle_type || null, body?.region || null)
    .run();
  return json({ ok: true }, 201);
}

export async function removeWatchlist(request, env, _url, params) {
  const db = env.DB;
  const user = await getUser(request, db);
  if (!user) return err(401, "unauthorized", "請先登入");
  await db.prepare("DELETE FROM watchlists WHERE id = ? AND user_id = ?").bind(params.id, user.id).run();
  return json({ ok: true });
}
