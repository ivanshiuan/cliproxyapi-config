"""產生選牌通 demo 資料 — 模擬監理服務網撈回的號牌資料格式。

正式上線後由 mvdis_client.py 抓真實資料，輸出格式與本檔一致，
前端不需要改任何一行。

用法：
    python plate_service/scraper/generate_demo_data.py
"""

from __future__ import annotations

import json
import random
from datetime import date, timedelta
from pathlib import Path

random.seed(1688)

OUT_DIR = Path(__file__).resolve().parent.parent / "web" / "data"

# 公路局主要監理所/站（demo 用清單；正式版由 mvdis_client 動態取得）
STATIONS = [
    {"code": "TPE-C", "name": "臺北市區監理所", "region": "北部"},
    {"code": "TPE-R", "name": "臺北區監理所（樹林）", "region": "北部"},
    {"code": "KEE", "name": "基隆監理站", "region": "北部"},
    {"code": "TAO", "name": "桃園監理站", "region": "北部"},
    {"code": "HSC", "name": "新竹區監理所", "region": "北部"},
    {"code": "MIA", "name": "苗栗監理站", "region": "中部"},
    {"code": "TXG", "name": "臺中區監理所", "region": "中部"},
    {"code": "CHW", "name": "彰化監理站", "region": "中部"},
    {"code": "NAN", "name": "南投監理站", "region": "中部"},
    {"code": "CYI", "name": "嘉義區監理所", "region": "南部"},
    {"code": "TNN", "name": "臺南監理站", "region": "南部"},
    {"code": "KHH-C", "name": "高雄市區監理所", "region": "南部"},
    {"code": "KHH-R", "name": "高雄區監理所", "region": "南部"},
    {"code": "PIF", "name": "屏東監理站", "region": "南部"},
    {"code": "ILA", "name": "宜蘭監理站", "region": "東部"},
    {"code": "HUN", "name": "花蓮監理站", "region": "東部"},
    {"code": "TTT", "name": "臺東監理站", "region": "東部"},
    {"code": "PEH", "name": "澎湖監理站", "region": "離島"},
]

VEHICLE_TYPES = {
    "car": {"label": "自用小客車", "fee": 2000},
    "taxi": {"label": "營業小客車", "fee": 2000},
    "ev": {"label": "電動小客車", "fee": 2000},
    "moto": {"label": "普通重型機車", "fee": 1000},
    "moto_yellow": {"label": "大型重機（黃牌）", "fee": 1000},
    "moto_red": {"label": "大型重機（紅牌）", "fee": 1000},
}

LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"  # 車牌不用 I、O


def _letters(prefix: str = "") -> str:
    s = prefix
    while len(s) < 3:
        s += random.choice(LETTERS)
    return s


def _digits(lucky_bias: float) -> str:
    """產生 4 碼數字；lucky_bias 越高越容易出吉祥組合。"""
    if random.random() < lucky_bias:
        return random.choice(
            [
                "8888", "6666", "9999", "1688", "1888", "5888", "6688",
                "8899", "0168", "1314", "5200", "0520", "2266", "6789",
                "1234", "5678", "0001", "0009", "1111", "2222", "3333",
                "7777", "9527", "1668", "8168", "6668", "8886", "0066",
            ]
        )
    return f"{random.randint(0, 9999):04d}"


def lucky_score(digits: str) -> int:
    """吉祥度 0-100：8/6 加分、4 扣分、疊字/順子/對稱加分。"""
    score = 50
    score += digits.count("8") * 10
    score += digits.count("6") * 6
    score += digits.count("9") * 3
    score -= digits.count("4") * 15
    if len(set(digits)) == 1:
        score += 25  # 四疊字
    elif any(digits.count(d) == 3 for d in set(digits)):
        score += 15  # 三疊字
    if digits in ("1234", "2345", "3456", "4567", "5678", "6789", "0123"):
        score += 15  # 順子
    if digits == digits[::-1]:
        score += 8  # 對稱
    if digits in ("1314", "5200", "0520", "9527", "0168", "1688"):
        score += 12  # 特殊寓意
    return max(0, min(100, score))


def make_plates(n: int = 320) -> list[dict]:
    plates: list[dict] = []
    seen: set[str] = set()
    today = date(2026, 7, 6)
    weights = {
        "car": 0.42, "taxi": 0.08, "ev": 0.14,
        "moto": 0.22, "moto_yellow": 0.08, "moto_red": 0.06,
    }
    while len(plates) < n:
        vtype = random.choices(list(weights), weights=list(weights.values()))[0]
        prefix = "E" if vtype == "ev" else ""
        number = f"{_letters(prefix)}-{_digits(lucky_bias=0.35)}"
        if number in seen:
            continue
        seen.add(number)
        st = random.choice(STATIONS)
        digits = number.split("-")[1]
        plates.append(
            {
                "plate": number,
                "type": vtype,
                "typeLabel": VEHICLE_TYPES[vtype]["label"],
                "station": st["name"],
                "stationCode": st["code"],
                "region": st["region"],
                "openDate": (today + timedelta(days=random.randint(0, 13))).isoformat(),
                "fee": VEHICLE_TYPES[vtype]["fee"],
                "luckyScore": lucky_score(digits),
            }
        )
    plates.sort(key=lambda p: (-p["luckyScore"], p["plate"]))
    return plates


def make_auctions() -> list[dict]:
    today = date(2026, 7, 6)
    premium = [
        ("BGT-8888", "car", 9000), ("BJK-6666", "car", 9000),
        ("EAK-8888", "ev", 9000), ("BLM-9999", "car", 9000),
        ("BNP-1688", "car", 5000), ("TCA-8899", "taxi", 5000),
        ("BQR-5200", "car", 3000), ("MKL-8888", "moto", 4000),
        ("BSU-6789", "car", 5000), ("EBC-1314", "ev", 3000),
        ("BVW-7777", "car", 9000), ("BXY-1111", "car", 9000),
        ("RBA-8686", "moto_red", 4000), ("BZA-0001", "car", 5000),
        ("YCD-6688", "moto_yellow", 4000), ("BAC-2266", "car", 3000),
    ]
    auctions = []
    for i, (plate, vtype, base) in enumerate(premium):
        st = random.choice(STATIONS)
        end = today + timedelta(days=random.randint(1, 12))
        hot = plate.split("-")[1] in ("8888", "9999", "6666")
        bids = random.randint(9, 30) if hot else random.randint(0, 8)
        current = base + bids * random.choice([1000, 2000, 3000]) * (4 if hot else 1)
        auctions.append(
            {
                "plate": plate,
                "type": vtype,
                "typeLabel": VEHICLE_TYPES[vtype]["label"],
                "station": st["name"],
                "region": st["region"],
                "basePrice": base,
                "currentBid": current if bids else base,
                "bidCount": bids,
                "endDate": end.isoformat(),
                "status": "競標中" if i % 4 else "即將截止",
                "luckyScore": lucky_score(plate.split("-")[1]),
            }
        )
    auctions.sort(key=lambda a: a["endDate"])
    return auctions


def make_records() -> list[dict]:
    """歷史成交行情（demo 參考值，非真實成交價）。"""
    return [
        {"plate": "BBB-8888", "station": "臺北市區監理所", "closeDate": "2026-05-12", "finalPrice": 352000, "bidCount": 41},
        {"plate": "AKV-6666", "station": "臺中區監理所", "closeDate": "2026-05-28", "finalPrice": 128000, "bidCount": 22},
        {"plate": "EAF-8888", "station": "高雄市區監理所", "closeDate": "2026-06-02", "finalPrice": 96000, "bidCount": 18},
        {"plate": "BPD-9999", "station": "新竹區監理所", "closeDate": "2026-06-09", "finalPrice": 88000, "bidCount": 15},
        {"plate": "BRF-1688", "station": "桃園監理站", "closeDate": "2026-06-16", "finalPrice": 36000, "bidCount": 11},
        {"plate": "MQA-8888", "station": "臺南監理站", "closeDate": "2026-06-20", "finalPrice": 25000, "bidCount": 9},
        {"plate": "BTG-5200", "station": "彰化監理站", "closeDate": "2026-06-24", "finalPrice": 15000, "bidCount": 6},
        {"plate": "BUH-6789", "station": "嘉義區監理所", "closeDate": "2026-06-30", "finalPrice": 21000, "bidCount": 8},
    ]


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    datasets = {
        "stations.json": STATIONS,
        "plates.json": make_plates(),
        "auctions.json": make_auctions(),
        "records.json": make_records(),
    }
    for name, data in datasets.items():
        path = OUT_DIR / name
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        print(f"wrote {path} ({len(data)} items)")


if __name__ == "__main__":
    main()
