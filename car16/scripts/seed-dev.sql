-- 本地開發假資料（make db-seed-local）。正式環境不要跑。
INSERT OR IGNORE INTO plate_cache (region, plate_no, vehicle_type, plate_prefix, status, fetched_at) VALUES
  ('臺北市區監理所', 'BXP-8888', '自用小客車', 'BXP', 'available', datetime('now')),
  ('臺北市區監理所', 'BXP-1688', '自用小客車', 'BXP', 'available', datetime('now')),
  ('臺北市區監理所', 'BXP-5566', '自用小客車', 'BXP', 'available', datetime('now')),
  ('臺北區監理所',   'BYA-0999', '自用小客車', 'BYA', 'available', datetime('now')),
  ('臺中區監理所',   'BMW-1314', '自用小客車', 'BMW', 'available', datetime('now')),
  ('高雄市區監理所', 'MAB-168',  '自用重型機車', 'MAB', 'available', datetime('now')),
  ('高雄市區監理所', 'MAB-888',  '自用重型機車', 'MAB', 'available', datetime('now'));

INSERT OR IGNORE INTO auction_announcements
  (region, plate_no, vehicle_type, base_price_twd, current_bid_twd, bid_count, announce_start, bid_start, bid_end, phase, fetched_at) VALUES
  ('臺北市區監理所', 'BXX-8888', '自用小客車', 3000, 152000, 23, '2026-07-01', '2026-07-08', '2026-07-15', 'bidding', datetime('now')),
  ('臺中區監理所',   'BWW-9999', '自用小客車', 3000, NULL, 0, '2026-07-10', '2026-07-20', '2026-07-27', 'announced', datetime('now')),
  ('高雄市區監理所', 'AAA-6666', '自用小客車', 3000, 88000, 12, '2026-06-20', '2026-06-27', '2026-07-04', 'closed', datetime('now'));

INSERT INTO ingest_runs (source, kind, ok, rows_upserted) VALUES ('external-push', 'auction', 1, 3);
