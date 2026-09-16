// 原始資料 → 標準列。這是主抓取與備援推送共用的唯一轉換層。
//
// 標準列格式（/api/ingest 收的也是這個）：
//   pickno:  { region, plate_no, vehicle_type, status? }
//   auction: { region, plate_no, vehicle_type?, base_price_twd?, current_bid_twd?,
//              bid_count?, announce_start?, bid_start?, bid_end?, phase }

const PLATE_RE = /^[A-Z0-9]{2,4}-[0-9]{2,4}$|^[0-9]{2,4}-[A-Z0-9]{2,4}$/;

export function isValidPlate(no) {
  return PLATE_RE.test(String(no || "").trim().toUpperCase());
}

export function platePrefix(no) {
  const m = String(no).toUpperCase().match(/^([A-Z]{2,3})/);
  return m ? m[1] : null;
}

/** 民國/各式日期字串 → ISO（解不出來回 null，不丟例外） */
export function toIsoDate(s) {
  if (!s) return null;
  const t = String(s).trim();
  let m = t.match(/^(\d{4})[\/\-.](\d{1,2})[\/\-.](\d{1,2})/); // 西元
  if (!m) {
    const roc = t.match(/^(\d{2,3})[\/\-.](\d{1,2})[\/\-.](\d{1,2})/); // 民國
    if (roc) m = [null, String(Number(roc[1]) + 1911), roc[2], roc[3]];
  }
  if (!m) return null;
  const [y, mo, d] = [m[1], m[2].padStart(2, "0"), m[3].padStart(2, "0")];
  return `${y}-${mo}-${d}`;
}

export function toAmountTwd(s) {
  if (s == null || s === "") return null;
  const n = Number(String(s).replace(/[,，元\s]/g, ""));
  return Number.isFinite(n) ? Math.round(n) : null;
}

/** 寬鬆吸收一筆外部列 → 標準 pickno 列（不合格回 null） */
export function normalizePickno(row) {
  if (!row) return null;
  const plate_no = String(row.plate_no || row.plateNo || row.carNo || "").trim().toUpperCase();
  const region = String(row.region || row.deptName || "").trim();
  const vehicle_type = String(row.vehicle_type || row.vehicleType || row.carType || "").trim();
  if (!isValidPlate(plate_no) || !region || !vehicle_type) return null;
  return {
    region,
    plate_no,
    vehicle_type,
    plate_prefix: platePrefix(plate_no),
    status: row.status === "sold" ? "sold" : "available",
  };
}

/** 寬鬆吸收一筆外部列 → 標準 auction 列（不合格回 null） */
export function normalizeAuction(row) {
  if (!row) return null;
  const plate_no = String(row.plate_no || row.plateNo || row.carNo || "").trim().toUpperCase();
  const region = String(row.region || row.deptName || "").trim();
  if (!isValidPlate(plate_no) || !region) return null;
  const phase = ["announced", "bidding", "closed"].includes(row.phase) ? row.phase : "announced";
  return {
    region,
    plate_no,
    vehicle_type: String(row.vehicle_type || row.vehicleType || "").trim() || null,
    base_price_twd: toAmountTwd(row.base_price_twd ?? row.basePrice),
    current_bid_twd: toAmountTwd(row.current_bid_twd ?? row.currentBid),
    bid_count: Number.isFinite(Number(row.bid_count ?? row.bidCount)) ? Number(row.bid_count ?? row.bidCount) : null,
    announce_start: toIsoDate(row.announce_start ?? row.announceStart),
    bid_start: toIsoDate(row.bid_start ?? row.bidStart) || "",
    bid_end: toIsoDate(row.bid_end ?? row.bidEnd),
    phase,
  };
}

/** 從 mvdis HTML 頁面盡力抽車牌號（直抓路徑的保底解析；上線後依實際 DOM 校準） */
export function extractPlatesFromHtml(html) {
  const found = new Set();
  const re = /\b([A-Z]{2,4}-\d{2,4}|\d{2,4}-[A-Z]{2,4}|[A-Z0-9]{3}-\d{4})\b/g;
  let m;
  while ((m = re.exec(String(html))) !== null) {
    const no = m[1].toUpperCase();
    if (isValidPlate(no)) found.add(no);
  }
  return [...found];
}
