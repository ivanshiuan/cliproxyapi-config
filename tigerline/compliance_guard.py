"""V3.0 Sprint 5 — Compliance Guard.

Two jobs:
1. Append a fixed legal disclaimer to every report (Chinese + English).
2. Refuse to emit any report that contains "guaranteed profit" or equivalent
   forbidden phrases (we are a *decision support* tool, not a tipster).

The forbidden-phrase list lives in ``config/compliance_rules.yaml``. Both the
report generator and any CLI output path must run the guard before returning
text to the user.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

CONFIG_DIR = Path(__file__).parent / "config"

DEFAULT_DISCLAIMER_ZH = (
    "本報告僅供體育賽事資料研究與風險控管參考，不構成保證獲利承諾。"
    "投注涉及風險，請遵守所在地法律，並量力而為。"
)
DEFAULT_DISCLAIMER_EN = (
    "This report is for sports data research and risk management reference only. "
    "It does not constitute a guarantee of profit. Betting involves risk; "
    "comply with local law and bet within your means."
)

DEFAULT_FORBIDDEN_PATTERNS: tuple[str, ...] = (
    # Chinese: \b doesn't work between CJK chars, so use plain literals.
    r"保證獲利",
    r"保證賺錢",
    r"保證命中",
    r"保證穩贏",
    r"穩賺",
    r"必中",
    r"包贏",
    # English: \b is fine because ASCII tokens are space-delimited.
    r"guaranteed (profit|win|hits?|returns?)",
    r"100%\s*(win|guarantee|profit)",
    r"can'?t\s+lose",
    r"risk[-\s]?free",
)


class ComplianceConfig(BaseModel):
    """Tunable compliance rules."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    disclaimer_zh: str = DEFAULT_DISCLAIMER_ZH
    disclaimer_en: str = DEFAULT_DISCLAIMER_EN
    forbidden_patterns: list[str] = Field(default_factory=lambda: list(DEFAULT_FORBIDDEN_PATTERNS))


@lru_cache(maxsize=1)
def load_compliance_config() -> ComplianceConfig:
    path = CONFIG_DIR / "compliance_rules.yaml"
    if not path.exists():
        return ComplianceConfig()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return ComplianceConfig.model_validate(raw)


def reset_compliance_cache() -> None:
    load_compliance_config.cache_clear()


@dataclass(frozen=True)
class ComplianceViolation:
    """One forbidden phrase that fired."""

    pattern: str
    match: str
    excerpt: str


class ComplianceError(Exception):
    """Raised when ``check_text`` finds a forbidden phrase."""

    def __init__(self, violations: list[ComplianceViolation]) -> None:
        self.violations = violations
        super().__init__(
            f"compliance violations: {[v.match for v in violations]}"
        )


def check_text(text: str, config: ComplianceConfig | None = None) -> list[ComplianceViolation]:
    """Scan text for forbidden phrases; return all hits (empty if clean)."""
    cfg = config or load_compliance_config()
    out: list[ComplianceViolation] = []
    for pat in cfg.forbidden_patterns:
        for m in re.finditer(pat, text, flags=re.IGNORECASE):
            start = max(0, m.start() - 20)
            end = min(len(text), m.end() + 20)
            out.append(ComplianceViolation(pattern=pat, match=m.group(0), excerpt=text[start:end]))
    return out


def enforce(text: str, config: ComplianceConfig | None = None) -> str:
    """Raise on violations, then append the disclaimer."""
    violations = check_text(text, config=config)
    if violations:
        raise ComplianceError(violations)
    return append_disclaimer(text, config=config)


def append_disclaimer(text: str, config: ComplianceConfig | None = None) -> str:
    """Append the bilingual disclaimer (idempotent — won't double-stamp)."""
    cfg = config or load_compliance_config()
    if cfg.disclaimer_zh in text:
        return text
    sep = "\n\n---\n\n"
    return text + sep + cfg.disclaimer_zh + "\n\n" + cfg.disclaimer_en


__all__ = [
    "DEFAULT_DISCLAIMER_EN",
    "DEFAULT_DISCLAIMER_ZH",
    "DEFAULT_FORBIDDEN_PATTERNS",
    "ComplianceConfig",
    "ComplianceError",
    "ComplianceViolation",
    "append_disclaimer",
    "check_text",
    "enforce",
    "load_compliance_config",
    "reset_compliance_cache",
]
