"""來源抓取 — httpx（帶瀏覽器 headers）+ 本地 inbox 攝取。

網路被擋 / 反爬 403 時不炸整條 pipeline：每個來源回傳
FetchOutcome，失敗的來源標記 error，其餘照走。
搞不定的頁面（如個人化行銷連結）走 inbox 路線：
瀏覽器另存新檔丟進 data/subsidies/inbox/，pipeline 一樣吃得下。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict

# 部分政府網站 / 行銷平台會擋預設 UA，一律帶桌面瀏覽器 headers
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
}

_RETRY_DELAYS = (2, 4, 8)  # 秒；比照專案 git push 的 exponential backoff 慣例


class FetchOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_id: str
    url: str
    ok: bool
    status_code: int | None = None
    snapshot_path: str | None = None
    error: str | None = None


def _snapshot_name(source_id: str, now: datetime) -> str:
    return f"{source_id}-{now.strftime('%Y%m%d-%H%M%S')}.html"


async def fetch_one(
    source_id: str,
    url: str,
    raw_dir: Path,
    client: httpx.AsyncClient | None = None,
) -> FetchOutcome:
    """抓單一來源；成功就落一份 HTML 快照到 raw_dir。"""
    own_client = client is None
    if client is None:
        client = httpx.AsyncClient(
            headers=BROWSER_HEADERS, follow_redirects=True, timeout=30.0
        )
    try:
        last_error = ""
        for attempt, delay in enumerate((0, *_RETRY_DELAYS)):
            if delay:
                await asyncio.sleep(delay)
            try:
                resp = await client.get(url)
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                continue
            if resp.status_code >= 500 and attempt < len(_RETRY_DELAYS):
                last_error = f"HTTP {resp.status_code}"
                continue
            if resp.status_code != 200:
                return FetchOutcome(
                    source_id=source_id,
                    url=url,
                    ok=False,
                    status_code=resp.status_code,
                    error=f"HTTP {resp.status_code}（反爬或需瀏覽器 — 可改走 inbox 路線）",
                )
            raw_dir.mkdir(parents=True, exist_ok=True)
            path = raw_dir / _snapshot_name(source_id, datetime.now(UTC))
            path.write_text(resp.text, encoding="utf-8")
            return FetchOutcome(
                source_id=source_id,
                url=url,
                ok=True,
                status_code=200,
                snapshot_path=str(path),
            )
        return FetchOutcome(source_id=source_id, url=url, ok=False, error=last_error)
    finally:
        if own_client:
            await client.aclose()


async def fetch_all(
    sources: list[tuple[str, str]],
    raw_dir: Path,
) -> list[FetchOutcome]:
    """並行抓多個來源；單一失敗不影響其他。"""
    async with httpx.AsyncClient(
        headers=BROWSER_HEADERS, follow_redirects=True, timeout=30.0
    ) as client:
        return list(
            await asyncio.gather(
                *(fetch_one(sid, url, raw_dir, client) for sid, url in sources)
            )
        )


_INBOX_SUFFIXES = {".html", ".htm", ".txt", ".md"}


def list_inbox(inbox_dir: Path) -> list[Path]:
    """inbox 內可攝取的檔案（PDF/Office 請先用 scripts/to_md.py 轉）。"""
    if not inbox_dir.is_dir():
        return []
    return sorted(
        p
        for p in inbox_dir.iterdir()
        if p.is_file() and p.suffix.lower() in _INBOX_SUFFIXES
    )


def list_snapshots(raw_dir: Path) -> list[Path]:
    if not raw_dir.is_dir():
        return []
    return sorted(p for p in raw_dir.glob("*.html") if p.is_file())
