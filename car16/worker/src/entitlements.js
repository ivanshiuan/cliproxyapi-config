// 方案權益單一真相來源。gating 一律在伺服器端做，前端只拿來畫鎖頭。
// -1 = 無限制。

export const FREE_TIER = {
  daily_queries: 10, // 已登入免費用戶
  anon_daily_queries: 3, // 未登入（IP 計）
  results_cap: 20,
  watchlist: 0,
  export: false,
  batch: false,
  priority_agency: false,
};

/** 用戶目前有效方案；無有效訂閱 → { tier:'free', features:FREE_TIER } */
export async function resolveEntitlements(db, userId) {
  if (!userId) return { tier: "free", features: FREE_TIER, expires_at: null };
  const row = await db
    .prepare(
      `SELECT s.plan_id, s.expires_at, p.features_json
         FROM subscriptions s JOIN plans p ON p.id = s.plan_id
        WHERE s.user_id = ? AND s.status = 'active' AND s.expires_at > datetime('now')
        ORDER BY s.expires_at DESC LIMIT 1`
    )
    .bind(userId)
    .first();
  if (!row) return { tier: "free", features: FREE_TIER, expires_at: null };
  return {
    tier: row.plan_id,
    features: { ...FREE_TIER, ...JSON.parse(row.features_json) },
    expires_at: row.expires_at,
  };
}

/** 免費層查詢配額 key（付費 tier 回 null = 不設限） */
export function quotaKey(ent, userId, ip, taipeiDay) {
  if (ent.tier !== "free") return null;
  return userId ? `q:u${userId}:${taipeiDay}` : `q:ip${ip}:${taipeiDay}`;
}

export function quotaLimit(ent, userId) {
  return userId ? ent.features.daily_queries : FREE_TIER.anon_daily_queries;
}
