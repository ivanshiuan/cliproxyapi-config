import { test } from "node:test";
import assert from "node:assert/strict";
import { FREE_TIER, quotaKey, quotaLimit } from "../src/entitlements.js";
import { patternToLike } from "../src/routes/plates.js";

test("patternToLike：* → %、? → _、非法字元拒絕", () => {
  assert.equal(patternToLike("*-8888"), "%-8888");
  assert.equal(patternToLike("BX?-16*"), "BX_-16%");
  assert.equal(patternToLike("abc-123"), "ABC-123");
  assert.equal(patternToLike("'; DROP TABLE"), null);
  assert.equal(patternToLike(""), null);
});

test("quotaKey：免費層才有 key，付費 tier 回 null", () => {
  const free = { tier: "free", features: FREE_TIER };
  const paid = { tier: "pro", features: { ...FREE_TIER, daily_queries: -1 } };
  assert.equal(quotaKey(free, 7, "1.2.3.4", "2026-07-12"), "q:u7:2026-07-12");
  assert.equal(quotaKey(free, null, "1.2.3.4", "2026-07-12"), "q:ip1.2.3.4:2026-07-12");
  assert.equal(quotaKey(paid, 7, "1.2.3.4", "2026-07-12"), null);
});

test("quotaLimit：登入用 daily_queries、匿名用 anon_daily_queries", () => {
  const free = { tier: "free", features: FREE_TIER };
  assert.equal(quotaLimit(free, 7), FREE_TIER.daily_queries);
  assert.equal(quotaLimit(free, null), FREE_TIER.anon_daily_queries);
});
