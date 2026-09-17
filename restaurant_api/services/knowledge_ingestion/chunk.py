"""Semantic chunker for markdown.

Rules (matches ``specs/knowledge_ingestion.md`` §6):

1. Split by top-level markdown headings (``#``, ``##``, ``###``); each
   segment carries a ``section_path`` of the most-recent 1..3 heading levels
   joined by `` > ``.
2. Within a segment, accumulate paragraphs (``\n\n``-delimited) up to
   ``max_tokens``. When adding the next paragraph would overflow, close
   the current chunk and start a new one.
3. The next chunk begins with a tail-overlap of ~``overlap_tokens`` from
   the end of the previous chunk, so cross-paragraph semantics survive.
4. Fenced code blocks (``` ``` ```) and markdown tables (``| … |``
   consecutive lines) are treated as **indivisible units**: they stay
   in a single chunk even if oversized, and the chunk's ``meta`` flags
   ``has_code`` / ``has_table`` accordingly.
5. If a single unit exceeds ``max_tokens`` and cannot be split by
   heading/paragraph rules (e.g. one huge run-on paragraph), the chunker
   hard-splits it and flags ``meta.forced_split=True``.

Token estimation is deliberately crude: ``max(len(text)//3, 1)``. For
CJK-heavy input that's a safe upper bound (1 char ≈ 1 token in
tiktoken); for ASCII it over-counts by ~30% which only makes chunking
more conservative. A real tokenizer can replace this without changing
the interface.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_CODE_FENCE_RE = re.compile(r"^```")
_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")


@dataclass(frozen=True)
class Chunk:
    text: str
    chunk_idx: int
    section_path: str | None
    token_count: int
    char_count: int
    text_sha256: str
    page_from: int | None = None
    page_to: int | None = None
    meta: dict[str, Any] = field(default_factory=dict)


def _tok(text: str) -> int:
    """Token count estimator — see module docstring."""
    return max(len(text) // 3, 1)


def _section_path_for(stack: list[str]) -> str | None:
    """Return `` > `` joined heading path; None if empty."""
    if not stack:
        return None
    return " > ".join(stack[-3:])


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _split_units(markdown: str) -> list[tuple[str, dict[str, Any], str | None]]:
    """First pass: turn markdown into indivisible units.

    Each unit is ``(text, meta, section_path)``. Headings do NOT produce
    their own units — they mutate the section_path used by subsequent units.
    Fenced code blocks and table blocks are single units.
    """
    lines = markdown.splitlines()
    heading_stack: list[str] = []
    units: list[tuple[str, dict[str, Any], str | None]] = []

    buf: list[str] = []
    in_code = False
    in_table = False

    def flush_buf(meta: dict[str, Any] | None = None) -> None:
        nonlocal buf
        text = "\n".join(buf).strip()
        buf = []
        if not text:
            return
        # Split paragraphs on blank lines — each paragraph becomes its own unit
        # unless the caller told us this is an atomic block (code/table).
        if meta and (meta.get("has_code") or meta.get("has_table")):
            units.append((text, meta, _section_path_for(heading_stack)))
            return
        for para in re.split(r"\n\s*\n", text):
            p = para.strip()
            if p:
                units.append((p, {}, _section_path_for(heading_stack)))

    for raw in lines:
        line = raw.rstrip()

        # Code fence toggle
        if _CODE_FENCE_RE.match(line.strip()):
            if in_code:
                # Closing fence: flush the code block as one unit
                buf.append(line)
                flush_buf({"has_code": True})
                in_code = False
                continue
            # Opening fence: flush any pending prose first
            flush_buf()
            buf.append(line)
            in_code = True
            continue

        if in_code:
            buf.append(line)
            continue

        # Table detection: two or more consecutive `| … |` lines
        is_table_row = bool(_TABLE_ROW_RE.match(line))
        if in_table:
            if is_table_row:
                buf.append(line)
                continue
            # Table ended
            flush_buf({"has_table": True})
            in_table = False
            # fall through to normal handling of this line

        if is_table_row:
            # Look-ahead not needed: mark start of table, subsequent
            # rows will accumulate.
            flush_buf()
            buf.append(line)
            in_table = True
            continue

        # Heading?
        m = _HEADING_RE.match(line)
        if m:
            flush_buf()
            level = len(m.group(1))
            title = m.group(2).strip()
            # Pop to depth-1 then push
            del heading_stack[level - 1 :]
            heading_stack.append(title)
            continue

        buf.append(line)

    # Trailing content
    if in_code:
        flush_buf({"has_code": True})
    elif in_table:
        flush_buf({"has_table": True})
    else:
        flush_buf()

    return units


def _pack_units(
    units: list[tuple[str, dict[str, Any], str | None]],
    *,
    max_tokens: int,
    overlap_tokens: int,
) -> list[Chunk]:
    """Second pass: pack units into chunks respecting max_tokens and overlap."""
    chunks: list[Chunk] = []
    current: list[str] = []
    current_meta: dict[str, Any] = {}
    current_section: str | None = None
    current_tokens = 0
    idx = 0

    def emit() -> None:
        nonlocal current, current_meta, current_tokens, idx
        if not current:
            return
        text = "\n\n".join(current)
        tok = _tok(text)
        chunks.append(
            Chunk(
                text=text,
                chunk_idx=idx,
                section_path=current_section,
                token_count=tok,
                char_count=len(text),
                text_sha256=_sha256(text),
                meta=dict(current_meta) if current_meta else {},
            )
        )
        idx += 1
        current = []
        current_meta = {}
        current_tokens = 0

    for text, meta, section in units:
        unit_tokens = _tok(text)
        atomic = bool(meta.get("has_code") or meta.get("has_table"))

        # Atomic units always emit alone if the pending chunk is non-empty.
        if atomic:
            if current:
                emit()
            hard_meta = dict(meta)
            if unit_tokens > max_tokens:
                hard_meta["forced_split"] = False  # atomic wins over forced_split
            chunks.append(
                Chunk(
                    text=text,
                    chunk_idx=idx,
                    section_path=section,
                    token_count=unit_tokens,
                    char_count=len(text),
                    text_sha256=_sha256(text),
                    meta=hard_meta,
                )
            )
            idx += 1
            current_section = section  # for the next non-atomic unit
            continue

        # Section change → close current chunk to keep section_path clean
        if current and section != current_section:
            emit()

        # Would this unit overflow?
        if current and current_tokens + unit_tokens > max_tokens:
            # Emit and start next with overlap tail
            tail = "\n\n".join(current)
            emit()
            if overlap_tokens > 0 and tail:
                # Take the last ~overlap_tokens*3 chars from tail as overlap
                overlap_chars = max(overlap_tokens * 3, 1)
                overlap_text = tail[-overlap_chars:]
                current = [overlap_text]
                current_tokens = _tok(overlap_text)
            current_section = section

        # A single unit larger than max_tokens on its own → forced hard split
        if unit_tokens > max_tokens and not current:
            step = max(max_tokens * 3, 1)
            for i in range(0, len(text), step):
                slab = text[i : i + step]
                if not slab:
                    continue
                chunks.append(
                    Chunk(
                        text=slab,
                        chunk_idx=idx,
                        section_path=section,
                        token_count=_tok(slab),
                        char_count=len(slab),
                        text_sha256=_sha256(slab),
                        meta={"forced_split": True},
                    )
                )
                idx += 1
            current_section = section
            continue

        current.append(text)
        current_tokens += unit_tokens
        current_section = section

    emit()
    return chunks


def chunk_markdown(
    markdown: str,
    *,
    max_tokens: int,
    overlap_tokens: int,
) -> list[Chunk]:
    """Public chunker entry point.

    Returns an empty list for empty / whitespace-only input.
    """
    if not markdown or not markdown.strip():
        return []
    units = _split_units(markdown)
    return _pack_units(
        units, max_tokens=max_tokens, overlap_tokens=overlap_tokens
    )


__all__ = ["Chunk", "chunk_markdown"]
