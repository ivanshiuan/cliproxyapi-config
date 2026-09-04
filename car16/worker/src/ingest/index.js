// 攝取編排：cron（直抓）與 /api/ingest（備援推送）共用 upsert 與紀錄。
import * as mvdis from "./sources/mvdis.js";
import { normalizePickno, normalizeAuction } from "./normalize.js";
import { nowIso } from "../util.js";

async function logRun(db, source, kind, ok, rows, error) {
  await db
    .prepare("INSERT INTO ingest_runs (source, kind, ok, rows_upserted, error) VALUES (?, ?, ?, ?, ?)")
    .bind(source, kind, ok ? 1 : 0, rows, error ? String(error).slice(0, 500) : null)
    .run();
}

export async function upsertPicknoRows(db, rows, source) {
  const ts = nowIso();
  let n = 0;
  const stmt = db.prepare(
    `INSERT INTO plate_cache (region, plate_no, vehicle_type, plate_prefix, status, fetched_at)
     VALUES (?, ?, ?, ?, ?, ?)
     ON CONFLICT(region, vehicle_type, plate_no)
     DO UPDATE SET status = excluded.status, fetched_at = excluded.fetched_at`
  );
  const batch = [];
  for (const raw of rows) {
    const r = normalizePickno(raw);
    if (!r) continue;
    batch.push(stmt.bind(r.region, r.plate_no, r.vehicle_type, r.plate_prefix, r.status, ts));
    n++;
  }
  if (batch.length) await db.batch(batch);
  await logRun(db, source, "pickno", true, n, null);
  return n;
}

export async function upsertAuctionRows(db, rows, source, kind = "auction") {
  const ts = nowIso();
  let n = 0;
  const stmt = db.prepare(
    `INSERT INTO auction_announcements
       (region, plate_no, vehicle_type, base_price_twd, current_bid_twd, bid_count,
        announce_start, bid_start, bid_end, phase, fetched_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
     ON CONFLICT(region, plate_no, bid_start)
     DO UPDATE SET current_bid_twd = excluded.current_bid_twd,
                   bid_count = excluded.bid_count,
                   phase = excluded.phase,
                   fetched_at = excluded.fetched_at`
  );
  const batch = [];
  for (const raw of rows) {
    const r = normalizeAuction(raw);
    if (!r) continue;
    batch.push(
      stmt.bind(
        r.region, r.plate_no, r.vehicle_type, r.base_price_twd, r.current_bid_twd,
        r.bid_count, r.announce_start, r.bid_start, r.bid_end, r.phase, ts
      )
    );
    n++;
  }
  if (batch.length) await db.batch(batch);
  await logRun(db, source, kind, true, n, null);
  return n;
}

/** cron 進入點：直抓 mvdis（失敗記錄不拋出，等備援補） */
export async function runIngestion(env) {
  const db = env.DB;
  const results = {};
  for (const [kind, fn] of [
    ["auction", mvdis.fetchAuctions],
    ["bidding", mvdis.fetchBidding],
  ]) {
    try {
      const rows = await fn();
      results[kind] = await upsertAuctionRows(db, rows, "mvdis-direct", kind);
    } catch (e) {
      results[kind] = { error: String(e && e.message ? e.message : e) };
      await logRun(db, "mvdis-direct", kind, false, 0, e);
    }
  }
  return results;
}

/** 資料新鮮度：最後成功攝取時間 + 是否過期（>3 小時） */
export async function freshness(db) {
  const row = await db
    .prepare("SELECT MAX(ran_at) AS last_ok FROM ingest_runs WHERE ok = 1 AND rows_upserted > 0")
    .first();
  const lastOk = row ? row.last_ok : null;
  const stale = !lastOk || Date.now() - new Date(lastOk.replace(" ", "T") + "Z").getTime() > 3 * 3600_000;
  return { data_as_of: lastOk, stale };
}
