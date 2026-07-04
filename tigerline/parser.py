"""JSON → ``MatchInput`` loader.

Thin layer over Pydantic — exists so the CLI, tests, and the shared-data adapter
all go through one validated entry point.
"""

from __future__ import annotations

import json
from pathlib import Path

from tigerline.models import MatchInput


def parse_match(payload: str | bytes | dict) -> MatchInput:
    """Validate a raw JSON payload into a ``MatchInput``."""
    data = json.loads(payload) if isinstance(payload, (str, bytes)) else payload
    return MatchInput.model_validate(data)


def load_match(path: str | Path) -> MatchInput:
    """Read a JSON file from disk and validate it."""
    return parse_match(Path(path).read_bytes())
