// car16 API Worker 入口：路由、CORS、cron
import { json, err } from "./util.js";
import { runIngestion } from "./ingest/index.js";
import { searchPlates, listAuctions, listFacets } from "./routes/plates.js";
import { register, login, logout, me, listWatchlist, addWatchlist, removeWatchlist } from "./routes/account.js";
import { checkout, ecpayReturn, myBilling, listPlans } from "./routes/billing.js";
import { createAgencyRequest, myAgencyRequests } from "./routes/agency.js";
import { chatMessage } from "./routes/chat.js";
import { pushIngest, probeIngest, ingestStatus } from "./routes/ingest.js";

// 路由表：`METHOD /path` 或含 :id 參數
const ROUTES = {
  "GET /api/health": async (_r, env) => {
    const row = await env.DB.prepare("SELECT COUNT(*) AS n FROM plans").first().catch(() => null);
    return json({ ok: true, db: row ? "up" : "down", plans: row?.n ?? 0 });
  },
  "GET /api/plans": listPlans,
  "GET /api/plates/search": searchPlates,
  "GET /api/plates/facets": listFacets,
  "GET /api/auctions": listAuctions,

  "POST /api/auth/register": register,
  "POST /api/auth/login": login,
  "POST /api/auth/logout": logout,
  "GET /api/me": me,
  "GET /api/me/billing": myBilling,
  "GET /api/me/watchlist": listWatchlist,
  "POST /api/me/watchlist": addWatchlist,
  "DELETE /api/me/watchlist/:id": removeWatchlist,
  "GET /api/me/agency-requests": myAgencyRequests,

  "POST /api/checkout": checkout,
  "POST /api/ecpay/return": ecpayReturn,

  "POST /api/agency-requests": createAgencyRequest,
  "POST /api/chat/message": chatMessage,

  "POST /api/ingest": pushIngest,
  "GET /api/ingest/probe": probeIngest,
  "GET /api/ingest/status": ingestStatus,
};

function matchRoute(method, pathname) {
  const direct = ROUTES[`${method} ${pathname}`];
  if (direct) return { handler: direct, params: {} };
  for (const [key, handler] of Object.entries(ROUTES)) {
    if (!key.includes("/:")) continue;
    const [m, pattern] = key.split(" ");
    if (m !== method) continue;
    const patternParts = pattern.split("/");
    const pathParts = pathname.split("/");
    if (patternParts.length !== pathParts.length) continue;
    const params = {};
    let ok = true;
    for (let i = 0; i < patternParts.length; i++) {
      if (patternParts[i].startsWith(":")) params[patternParts[i].slice(1)] = decodeURIComponent(pathParts[i]);
      else if (patternParts[i] !== pathParts[i]) { ok = false; break; }
    }
    if (ok) return { handler, params };
  }
  return null;
}

function corsHeaders(env, request) {
  // 正式站 origin + 本機開發（localhost/127.0.0.1 任意 port）
  const origin = request.headers.get("origin") || "";
  const allowed =
    origin === env.SITE_ORIGIN || /^http:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/.test(origin);
  return {
    "access-control-allow-origin": allowed ? origin : env.SITE_ORIGIN || "*",
    "access-control-allow-headers": "authorization, content-type",
    "access-control-allow-methods": "GET,POST,DELETE,OPTIONS",
    "access-control-max-age": "86400",
  };
}

export default {
  async fetch(request, env) {
    const cors = corsHeaders(env, request);
    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });

    const url = new URL(request.url);
    const match = matchRoute(request.method, url.pathname);
    if (!match) {
      const res = err(404, "not_found", `無此路徑：${request.method} ${url.pathname}`);
      return withCors(res, cors);
    }
    try {
      const res = await match.handler(request, env, url, match.params);
      return withCors(res, cors);
    } catch (e) {
      console.error("unhandled", url.pathname, e);
      return withCors(err(500, "internal", "伺服器錯誤，請稍後再試"), cors);
    }
  },

  async scheduled(_event, env, ctx) {
    ctx.waitUntil(runIngestion(env));
  },
};

function withCors(res, cors) {
  const headers = new Headers(res.headers);
  for (const [k, v] of Object.entries(cors)) headers.set(k, v);
  return new Response(res.body, { status: res.status, headers });
}
