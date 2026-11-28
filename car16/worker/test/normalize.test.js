import { test } from "node:test";
import assert from "node:assert/strict";
import {
  isValidPlate,
  platePrefix,
  toIsoDate,
  toAmountTwd,
  normalizePickno,
  normalizeAuction,
  extractPlatesFromHtml,
} from "../src/ingest/normalize.js";

test("isValidPlate：常見台灣車牌格式", () => {
  assert.equal(isValidPlate("ABC-1234"), true); // 新式汽車
  assert.equal(isValidPlate("BXX-8888"), true);
  assert.equal(isValidPlate("MAB-123"), true); // 機車
  assert.equal(isValidPlate("123-ABC"), true);
  assert.equal(isValidPlate("abc-1234"), true); // 自動轉大寫
  assert.equal(isValidPlate("\n"), false);
  assert.equal(isValidPlate("ABCDE"), false);
  assert.equal(isValidPlate("A-1"), false);
});

test("platePrefix：抽字軌", () => {
  assert.equal(platePrefix("BXX-8888"), "BXX");
  assert.equal(platePrefix("123-ABC"), null);
});

test("toIsoDate：西元與民國都吃", () => {
  assert.equal(toIsoDate("2026/07/12"), "2026-07-12");
  assert.equal(toIsoDate("2026-7-2"), "2026-07-02");
  assert.equal(toIsoDate("115/07/12"), "2026-07-12"); // 民國 115
  assert.equal(toIsoDate("garbage"), null);
  assert.equal(toIsoDate(null), null);
});

test("toAmountTwd：千分位/元字樣", () => {
  assert.equal(toAmountTwd("300,000"), 300000);
  assert.equal(toAmountTwd("300,000元"), 300000);
  assert.equal(toAmountTwd(149), 149);
  assert.equal(toAmountTwd(""), null);
  assert.equal(toAmountTwd("N/A"), null);
});

test("normalizePickno：合法列標準化、垃圾列回 null", () => {
  const ok = normalizePickno({ plate_no: "bxx-8888", region: "臺北市區監理所", vehicle_type: "自用小客車" });
  assert.deepEqual(ok, {
    region: "臺北市區監理所",
    plate_no: "BXX-8888",
    vehicle_type: "自用小客車",
    plate_prefix: "BXX",
    status: "available",
  });
  assert.equal(normalizePickno({ plate_no: "bad", region: "x", vehicle_type: "y" }), null);
  assert.equal(normalizePickno(null), null);
  // camelCase 別名也吃（備援腳本可能用不同欄位名）
  assert.ok(normalizePickno({ plateNo: "ABC-1234", deptName: "臺中區監理所", carType: "自用小客車" }));
});

test("normalizeAuction：金額/日期/phase 清洗", () => {
  const r = normalizeAuction({
    plate_no: "AAA-8888",
    region: "高雄市區監理所",
    base_price_twd: "3,000元",
    current_bid_twd: 15000,
    bid_count: "7",
    bid_start: "115/07/01",
    bid_end: "2026/07/15",
    phase: "bidding",
  });
  assert.equal(r.base_price_twd, 3000);
  assert.equal(r.current_bid_twd, 15000);
  assert.equal(r.bid_count, 7);
  assert.equal(r.bid_start, "2026-07-01");
  assert.equal(r.bid_end, "2026-07-15");
  assert.equal(r.phase, "bidding");
  // 未知 phase → announced；bid_start 缺 → 空字串（UNIQUE key 需要非 NULL）
  const d = normalizeAuction({ plate_no: "BBB-6666", region: "x監理所", phase: "whatever" });
  assert.equal(d.phase, "announced");
  assert.equal(d.bid_start, "");
});

test("extractPlatesFromHtml：從 HTML 抽車牌、去重", () => {
  const html = `<table><tr><td>BXP-8888</td></tr><tr><td>BXP-8888</td><td>MAB-168</td></tr>
    <td>not-a-plate ZZZZZ 12345</td></table>`;
  const plates = extractPlatesFromHtml(html);
  assert.deepEqual(plates.sort(), ["BXP-8888", "MAB-168"]);
});
