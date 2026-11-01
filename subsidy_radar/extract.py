"""HTML → 純文字（stdlib，零額外依賴）。

蒸餾層只需要「可讀文字 + 連結」，不需要完整 DOM；
用 html.parser 去殼即可，避免為此拉 beautifulsoup 進 .venv。
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urldefrag, urljoin

_SKIP_TAGS = {"script", "style", "noscript", "svg", "head", "iframe"}
_BLOCK_TAGS = {
    "p",
    "div",
    "br",
    "li",
    "tr",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "section",
    "article",
    "table",
    "ul",
    "ol",
}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._links: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if tag == "a":
            href = dict(attrs).get("href")
            if href and href.startswith("http"):
                self._links.append(href)
        if tag in _BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag in _BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and data.strip():
            self._chunks.append(data)

    @property
    def text(self) -> str:
        raw = "".join(self._chunks)
        # 連續空白壓一格、三個以上換行壓成兩個
        raw = re.sub(r"[ \t　]+", " ", raw)
        raw = re.sub(r"\n\s*\n\s*\n+", "\n\n", raw)
        return raw.strip()

    @property
    def links(self) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for url in self._links:
            if url not in seen:
                seen.add(url)
                out.append(url)
        return out


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    return parser.text


def html_links(html: str) -> list[str]:
    parser = _TextExtractor()
    parser.feed(html)
    return parser.links


class _AnchorExtractor(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self._base_url = base_url
        self._href: str | None = None
        self._title = ""
        self._text: list[str] = []
        self.anchors: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        attr = dict(attrs)
        href = (attr.get("href") or "").strip()
        skip = not href or href.startswith(("#", "javascript:", "mailto:", "tel:"))
        self._href = None if skip else href
        self._title = (attr.get("title") or "").strip()
        self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or self._href is None:
            return
        text = re.sub(r"\s+", " ", "".join(self._text)).strip() or self._title
        url = urldefrag(urljoin(self._base_url, self._href)).url
        if text and url.startswith("http"):
            self.anchors.append((text, url))
        self._href = None


def html_anchors(html: str, base_url: str) -> list[tuple[str, str]]:
    """（連結文字, 絕對網址）清單；相對路徑以 base_url 補全，文字空白時改用 title。"""
    parser = _AnchorExtractor(base_url)
    parser.feed(html)
    return parser.anchors
