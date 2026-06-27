"""Shared fixtures for TIGER LINE tests.

Strictly sync tests — even though the repo runs ``pytest-asyncio`` in
``auto`` mode globally, every test here is a plain ``def``.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from tigerline.models import MatchInput
from tigerline.parser import parse_match
from tigerline.storage import connect

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture
def load_fixture():
    """Return a ``(name) -> MatchInput`` loader.

    Names are file stems under ``tests/tigerline/fixtures/``.
    """

    def _load(name: str) -> MatchInput:
        path = FIXTURES / f"{name}.json"
        return parse_match(path.read_text(encoding="utf-8"))

    return _load


@pytest.fixture
def raw_fixture():
    """Return a ``(name) -> dict`` loader (no validation — for negative tests)."""

    def _load(name: str) -> dict:
        return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))

    return _load


@pytest.fixture
def db(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """Fresh SQLite db rooted under ``tmp_path``."""
    conn = connect(tmp_path / "tiger.db")
    try:
        yield conn
    finally:
        conn.close()
