// 選號查詢 + 標牌公告（免費層配額 gating 在這裡）
import { json, err, bumpCounter, clientIp, taipeiDate } from "../util.js";
import { getUser } from "../auth.js";
import { resolveEntitlements, quotaKey, quotaLimit } from "../entitlements.js";
import { freshness } from "../ingest/index.js";

/** 樣式 → SQL LIKE：* 任意多字元、? 單一字元；其餘字面比對 */
export function patternToLike(pattern) {
  const p = String(pattern || "").trim().toUpperCase();
  if (!p) return null;
  if (!/^[A-Z0-9*?-]{1,10}$/.test(p)) return null;
  return p.replace(/\*/g, "%").replace(/\?/g, "_");
}

export async function searchPlates(request, env, url) {
  const db = env.DB;
  const user = await getUser(request, db);
  const ent = await resolveEntitlements(db, user?.id);

  // 免費層配額
  const key = quotaKey(ent, user?.id, clientIp(request), taipeiDate());
  let quotaLeft = -1;
  if (key) {
    const limit = quotaLimit(ent, user?.id);
    const used = await bumpCounter(db, key);
    if (used > limit) return err(429, "quota_exceeded", "今日免費查詢次數已用完，升級方案即可不限次查詢");
    quotaLeft = limit - used;
  }

  const like = patternToLike(url.searchParams.get("pattern"));
  const region = url.searchParams.get("region");
  const vehicleType = url.searchParams.get("vehicle_type");

  const wheres = ["status = 'available'"];
  const binds = [];
  if (like) { wheres.push("plate_no LIKE ?"); binds.push(like); }
  if (region) { wheres.push("region = ?"); binds.push(region); }
  if (vehicleType) { wheres.push("vehicle_type = ?"); binds.push(vehicleType); }

  const cap = ent.features.results_cap === -1 ? 500 : ent.features.results_cap;
  const rows = await db
    .prepare(
      `SELECT region, plate_no, vehicle_type, fetched_at
         FROM plate_cache WHERE ${wheres.join(" AND ")}
        ORDER BY plate_no LIMIT ?`
    )
    .bind(...binds, cap)
    .all();

  const fresh = await freshness(db);
  return json({
    results: rows.results,
    capped: rows.results.length === cap && ent.tier === "free",
    quota_left: quotaLeft,
    tier: ent.tier,
    ...fresh,
  });
}

export async function listAuctions(request, env, url) {
  const db = env.DB;
  const phase = url.searchParams.get("phase") || "announced";
  if (!["announced", "bidding", "closed"].includes(phase)) return err(400, "bad_phase", "phase 無效");
  const rows = await db
    .prepare(
      `SELECT region, plate_no, vehicle_type, base_price_twd, current_bid_twd,
              bid_count, announce_start, bid_start, bid_end, phase, fetched_at
         FROM auction_announcements WHERE phase = ?
        ORDER BY COALESCE(bid_end, bid_start) DESC LIMIT 300`
    )
    .bind(phase)
    .all();
  const fresh = await freshness(db);
  return json({ results: rows.results, ...fresh });
}

/** 查詢頁的下拉選單資料（現有快取中的實際值） */
export async function listFacets(_request, env) {
  const db = env.DB;
  const regions = await db.prepare("SELECT DISTINCT region FROM plate_cache ORDER BY region").all();
  const types = await db.prepare("SELECT DISTINCT vehicle_type FROM plate_cache ORDER BY vehicle_type").all();
  return json({
    regions: regions.results.map((r) => r.region),
    vehicle_types: types.results.map((r) => r.vehicle_type),
  });
}
