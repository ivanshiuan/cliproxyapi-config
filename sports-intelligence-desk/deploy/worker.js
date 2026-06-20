/* =====================================================================
 * SID Live Proxy — Cloudflare Worker
 * 解 CORS + 隱藏 API key + 限制可代理的賽事 API host（避免變開放代理）。
 *
 * 部署：
 *   1) npm i -g wrangler && wrangler login
 *   2) wrangler secret put APIFOOTBALL_KEY   （貼上你的 key）
 *   3) wrangler deploy
 *   4) App 設定頁 proxy 填：https://<worker>.workers.dev/?u=
 * ===================================================================== */

const ALLOW_HOSTS = new Set([
  "v3.football.api-sports.io",
  "api.football-data.org",
  "www.thesportsdb.com",
]);

export default {
  async fetch(request, env) {
    const cors = {
      "access-control-allow-origin": "*",
      "access-control-allow-headers": "*",
      "access-control-allow-methods": "GET,OPTIONS",
    };
    if (request.method === "OPTIONS") return new Response(null, { headers: cors });

    const target = new URL(request.url).searchParams.get("u");
    if (!target) return json({ error: "missing ?u=" }, 400, cors);

    let t;
    try { t = new URL(target); } catch { return json({ error: "bad url" }, 400, cors); }
    if (!ALLOW_HOSTS.has(t.hostname)) return json({ error: "host not allowed: " + t.hostname }, 403, cors);

    // 依目標注入對應 key（key 存在 Worker secret，前端永遠看不到）
    const headers = {};
    if (t.hostname.endsWith("api-sports.io")) headers["x-apisports-key"] = env.APIFOOTBALL_KEY || "";
    if (t.hostname.endsWith("football-data.org")) headers["X-Auth-Token"] = env.FOOTBALLDATA_KEY || "";

    const upstream = await fetch(t.toString(), { headers });
    const body = await upstream.text();
    return new Response(body, {
      status: upstream.status,
      headers: { ...cors, "content-type": "application/json", "cache-control": "public, max-age=60" },
    });
  },
};

function json(obj, status, cors) {
  return new Response(JSON.stringify(obj), { status, headers: { ...cors, "content-type": "application/json" } });
}
