// 委託代辦 intake：平台只收需求與聯絡方式（媒合），不收證件、不碰保證金。
import { json, err, bumpCounter, clientIp } from "../util.js";
import { getUser } from "../auth.js";

const PHONE_RE = /^09\d{8}$|^0\d{1,2}-?\d{6,8}$/;

export async function createAgencyRequest(request, env) {
  const db = env.DB;
  // 未登入也能留單，但限流：每 IP 每日 5 單
  const ipKey = `agency:${clientIp(request)}:${new Date().toISOString().slice(0, 10)}`;
  if ((await bumpCounter(db, ipKey)) > 5) return err(429, "too_many", "今日留單次數已達上限，請直接加 LINE 聯繫");

  const user = await getUser(request, db);
  const body = await request.json().catch(() => null);
  if (!body) return err(400, "bad_json", "請提供 JSON body");

  const kind = ["pickno", "bid", "support"].includes(body.kind) ? body.kind : "pickno";
  const name = String(body.contact_name || "").trim();
  const phone = String(body.contact_phone || "").replace(/\s/g, "");
  if (!name) return err(400, "need_name", "請留姓名或稱呼");
  if (!PHONE_RE.test(phone)) return err(400, "bad_phone", "電話格式不正確");

  const res = await db
    .prepare(
      `INSERT INTO agency_requests
         (user_id, kind, contact_name, contact_phone, contact_line, plate_pattern, region, vehicle_type, budget_twd, note)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
    )
    .bind(
      user?.id || null,
      kind,
      name,
      phone,
      body.contact_line || null,
      body.plate_pattern || null,
      body.region || null,
      body.vehicle_type || null,
      Number.isFinite(Number(body.budget_twd)) ? Number(body.budget_twd) : null,
      String(body.note || "").slice(0, 1000) || null
    )
    .run();
  return json({ ok: true, id: res.meta.last_row_id }, 201);
}

export async function myAgencyRequests(request, env) {
  const db = env.DB;
  const user = await getUser(request, db);
  if (!user) return err(401, "unauthorized", "請先登入");
  const rows = await db
    .prepare(
      "SELECT id, kind, plate_pattern, region, vehicle_type, budget_twd, status, created_at FROM agency_requests WHERE user_id = ? ORDER BY created_at DESC"
    )
    .bind(user.id)
    .all();
  return json({ results: rows.results });
}
