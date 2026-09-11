// 備援攝取推送 + 直抓探測（皆需 INGEST_TOKEN）
import { json, err } from "../util.js";
import { upsertPicknoRows, upsertAuctionRows } from "../ingest/index.js";
import { probe } from "../ingest/sources/mvdis.js";

function authorized(request, env) {
  if (!env.INGEST_TOKEN) return false; // 未設 token 前此路徑關閉
  const auth = request.headers.get("authorization") || "";
  return auth === `Bearer ${env.INGEST_TOKEN}`;
}

/**
 * POST /api/ingest
 * body: { kind: 'pickno'|'auction'|'bidding', rows: [...標準列（見 normalize.js 註解）] }
 */
export async function pushIngest(request, env) {
  if (!authorized(request, env)) return err(401, "unauthorized", "需要 INGEST_TOKEN");
  const body = await request.json().catch(() => null);
  if (!body || !Array.isArray(body.rows)) return err(400, "bad_body", "需要 { kind, rows: [] }");
  if (body.rows.length > 5000) return err(400, "too_many_rows", "單次最多 5000 列");

  let n;
  if (body.kind === "pickno") n = await upsertPicknoRows(env.DB, body.rows, "external-push");
  else if (body.kind === "auction" || body.kind === "bidding")
    n = await upsertAuctionRows(env.DB, body.rows, "external-push", body.kind);
  else return err(400, "bad_kind", "kind 必須是 pickno | auction | bidding");
  return json({ ok: true, upserted: n });
}

/** GET /api/ingest/probe — 部署後校準 mvdis 直抓可行性（不入庫） */
export async function probeIngest(request, env) {
  if (!authorized(request, env)) return err(401, "unauthorized", "需要 INGEST_TOKEN");
  return json(await probe());
}

/** GET /api/ingest/status — 最近攝取紀錄（公開，僅統計） */
export async function ingestStatus(_request, env) {
  const rows = await env.DB
    .prepare("SELECT source, kind, ok, rows_upserted, error, ran_at FROM ingest_runs ORDER BY id DESC LIMIT 20")
    .all();
  return json({ runs: rows.results });
}
