"""Extractor Protocol + FakeExtractor + Markitdown subprocess wrapper.

The real ``MarkitdownExtractor`` shells out to the ``markitdown`` CLI
(already the project's canonical file→markdown tool per CLAUDE.md
「檔案攝取」). Kept behind a Protocol so tests can inject a fake without
touching subprocess plumbing.
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
from pathlib import Path
from typing import Protocol

from .errors import IngestionExtractError


class Extractor(Protocol):
    """Turn raw source bytes into markdown."""

    async def extract(
        self, *, source_bytes: bytes, mime_type: str | None
    ) -> str:  # pragma: no cover — Protocol
        ...


class FakeExtractor:
    """Deterministic test extractor: assumes the input bytes ARE markdown.

    Records every call for assertions and honours a configurable
    ``fail_with`` exception (drop-in for AC-10 / AC-13).
    """

    def __init__(
        self,
        *,
        fail_with: Exception | None = None,
        delay_seconds: float = 0.0,
    ) -> None:
        self.fail_with = fail_with
        self.delay_seconds = delay_seconds
        self.calls: list[tuple[bytes, str | None]] = []

    async def extract(
        self, *, source_bytes: bytes, mime_type: str | None
    ) -> str:
        self.calls.append((source_bytes, mime_type))
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if self.fail_with is not None:
            raise self.fail_with
        text = source_bytes.decode("utf-8", errors="replace")
        if not text.strip():
            raise IngestionExtractError("empty source")
        return text


class MarkitdownExtractor:
    """Shells out to the ``markitdown`` CLI (``uv run scripts/to_md.py``).

    Kept minimal on purpose: a temp file, a 60s timeout, and a clean
    error if the binary or the conversion fails. Real production use
    should mount a persistent uv cache so the first invocation isn't slow.
    """

    def __init__(
        self,
        *,
        timeout_seconds: float = 60.0,
        binary: str | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        # ``markitdown`` under uv-run per project convention.
        self.binary = binary or shutil.which("markitdown") or "markitdown"

    async def extract(
        self, *, source_bytes: bytes, mime_type: str | None
    ) -> str:
        if not source_bytes:
            raise IngestionExtractError("empty source")

        # Write source to a tmp file with a plausible extension so
        # markitdown's sniffer picks the right adapter.
        ext = _ext_for_mime(mime_type)
        import tempfile

        with tempfile.NamedTemporaryFile(
            suffix=ext, delete=False
        ) as tmp:
            tmp.write(source_bytes)
            tmp_path = Path(tmp.name)

        try:
            proc = await asyncio.create_subprocess_exec(
                self.binary,
                str(tmp_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=self.timeout_seconds
                )
            except TimeoutError as exc:
                proc.kill()
                raise IngestionExtractError(
                    f"markitdown timed out after {self.timeout_seconds}s"
                ) from exc
            if proc.returncode != 0:
                raise IngestionExtractError(
                    f"markitdown exit {proc.returncode}: "
                    f"{stderr.decode('utf-8', errors='replace')[:500]}"
                )
            text = stdout.decode("utf-8", errors="replace")
            if not text.strip():
                raise IngestionExtractError("markitdown produced empty output")
            return text
        finally:
            with contextlib.suppress(OSError):
                tmp_path.unlink()


def _ext_for_mime(mime_type: str | None) -> str:
    if not mime_type:
        return ".bin"
    m = mime_type.lower()
    if m == "application/pdf":
        return ".pdf"
    if m in {"text/markdown", "text/x-markdown"}:
        return ".md"
    if m == "text/plain":
        return ".txt"
    if m == "text/html":
        return ".html"
    if m == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        return ".docx"
    if m == "application/vnd.openxmlformats-officedocument.presentationml.presentation":
        return ".pptx"
    if m == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
        return ".xlsx"
    return ".bin"


__all__ = ["Extractor", "FakeExtractor", "MarkitdownExtractor"]
