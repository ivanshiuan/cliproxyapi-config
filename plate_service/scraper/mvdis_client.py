"""監理服務網（MVDIS）公開資料擷取 client — 骨架版。

資料來源（皆為公開頁面，無需登入即可查詢）：

* 可選號牌查詢   https://www.mvdis.gov.tw/m3-emv-plate/webpickno/queryPickNo
* 標牌公告       https://www.mvdis.gov.tw/m3-emv-plate/bid/announce
* 競標中號牌     https://www.mvdis.gov.tw/m3-emv-plate/bid/queryBiding
* 標售紀錄       https://www.mvdis.gov.tw/m3-emv-plate/bid/queryBid

實作備註（正式化時逐項處理）：

1. **CSRFToken**：MVDIS 表單頁會核發 per-session CSRFToken（querystring 帶
   ``CSRFToken=...``），需先 GET 一次表單頁取得 token 與 JSESSIONID cookie，
   後續 POST 查詢時帶上。
2. **驗證碼**：「可選號牌查詢」送出時需輸入圖形驗證碼 — 這正是使用者痛點，
   也是官方允許的人工查詢節奏。合規做法：低頻率排程（每站每日 1-2 次）、
   遵守 robots.txt、必要時改採人工輔助錄入或申請資料介接。
   **不要**實作自動破解驗證碼（法律與 ToS 風險，也違反本專案原則）。
3. **站所代碼**：查詢參數含 ``stationCode``（監理所）與 ``sectionCode``（站），
   完整清單可從表單頁的 <select> 解析。
4. **輸出格式**：與 ``generate_demo_data.py`` 輸出一致（plates/auctions/
   records/stations 四個 JSON），前端零改動即可切換真實資料。
5. **排程**：正式版走 APScheduler（同 restaurant_api/jobs 模式）或 cron，
   抓完寫 JSON / DB，前端只讀靜態產物，不即時打官方站。

離線／示範環境：本模組所有網路方法在 ``demo=True`` 時回傳
``generate_demo_data`` 的資料，方便測試與展示。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

BASE = "https://www.mvdis.gov.tw"
PICK_NO_URL = f"{BASE}/m3-emv-plate/webpickno/queryPickNo"
BID_ANNOUNCE_URL = f"{BASE}/m3-emv-plate/bid/announce"
BID_LIVE_URL = f"{BASE}/m3-emv-plate/bid/queryBiding"
BID_RECORD_URL = f"{BASE}/m3-emv-plate/bid/queryBid"

UA = "PlateGo/0.1 (data sync; contact: ops@platego.example)"

DATA_DIR = Path(__file__).resolve().parent.parent / "web" / "data"


@dataclass
class MvdisClient:
    """低頻、禮貌的 MVDIS 公開資料 client。

    ``demo=True``（預設）時不打真實網站，回傳本地示範資料 —
    在未取得營運決策與流量許可前，請維持 demo 模式。
    """

    demo: bool = True
    timeout: float = 20.0
    _client: httpx.Client | None = field(default=None, repr=False)

    # ---- session / token -------------------------------------------------
    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                headers={"User-Agent": UA}, timeout=self.timeout, follow_redirects=True
            )
        return self._client

    def _get_csrf_token(self, form_url: str) -> str:
        """GET 表單頁，從 HTML 解析 CSRFToken（正式化時以 selectolax/bs4 實作）。"""
        raise NotImplementedError("正式版實作：GET form → parse CSRFToken hidden input")

    # ---- public API -------------------------------------------------------
    def fetch_stations(self) -> list[dict[str, Any]]:
        """全台監理所站清單（demo：讀本地檔；正式：解析表單頁 select options）。"""
        if self.demo:
            return self._load_local("stations")
        raise NotImplementedError

    def fetch_available_plates(self, station_code: str | None = None) -> list[dict[str, Any]]:
        """可選號牌查詢。正式版須處理 CSRFToken + 圖形驗證碼（見模組 docstring #2）。"""
        if self.demo:
            plates = self._load_local("plates")
            if station_code:
                plates = [p for p in plates if p["stationCode"] == station_code]
            return plates
        raise NotImplementedError

    def fetch_live_auctions(self) -> list[dict[str, Any]]:
        """競標中號牌（bid/queryBiding — 公開頁、無驗證碼，最適合先自動化）。"""
        if self.demo:
            return self._load_local("auctions")
        raise NotImplementedError

    def fetch_auction_records(self, year: int, month: int) -> list[dict[str, Any]]:
        """歷史標售紀錄（bid/queryBid）— 累積成吉祥號估價資料庫的原料。"""
        if self.demo:
            return self._load_local("records")
        raise NotImplementedError

    # ---- helpers ----------------------------------------------------------
    @staticmethod
    def _load_local(name: str) -> list[dict[str, Any]]:
        path = DATA_DIR / f"{name}.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def close(self) -> None:
        if self._client is not None:
            self._client.close()


def sync_all(demo: bool = True) -> dict[str, int]:
    """一次抓齊四個資料集並寫入 web/data/（demo 模式等於 no-op 重寫）。"""
    client = MvdisClient(demo=demo)
    try:
        datasets = {
            "stations": client.fetch_stations(),
            "plates": client.fetch_available_plates(),
            "auctions": client.fetch_live_auctions(),
            "records": client.fetch_auction_records(2026, 6),
        }
        for name, data in datasets.items():
            (DATA_DIR / f"{name}.json").write_text(
                json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8"
            )
        return {name: len(data) for name, data in datasets.items()}
    finally:
        client.close()


if __name__ == "__main__":
    print(sync_all(demo=True))
