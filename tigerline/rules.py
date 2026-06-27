"""YAML rule loader — cached + Pydantic-validated.

All three downstream modules (classifier / corridor / stake) read their rules
from YAML so Ivan can tune thresholds without code changes. The YAML files live
under ``tigerline/config/`` and are bundled with the package.

We validate every file shape on load — a typo in YAML fails fast with a clear
error rather than producing silent mis-classification at midnight.
"""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

import yaml
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from tigerline.models import ScenarioType, StakeLevel, TeamNeedEnum

CONFIG_DIR = Path(__file__).parent / "config"


def _to_decimal(v: Any) -> Any:
    if isinstance(v, float):
        return Decimal(str(v))
    return v


YamlDecimal = Annotated[Decimal, BeforeValidator(_to_decimal)]


# ──────────────────────────────────────────────────────────────────────────
# Classification rules
# ──────────────────────────────────────────────────────────────────────────


class WhenClause(BaseModel):
    """Predicate clause. Every populated field must hold for the rule to fire."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    handicap_abs_gte: YamlDecimal | None = None
    handicap_abs_lte: YamlDecimal | None = None
    totals_gte: YamlDecimal | None = None
    totals_lte: YamlDecimal | None = None
    favorite_need: TeamNeedEnum | None = None
    favorite_need_in: list[TeamNeedEnum] | None = None
    underdog_need: TeamNeedEnum | None = None
    underdog_need_in: list[TeamNeedEnum] | None = None
    either_need_in: list[TeamNeedEnum] | None = None
    final_round: bool | None = None
    same_time_kickoff: bool | None = None


class ClassificationRule(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    scenario: ScenarioType
    when: WhenClause
    reason: str = Field(min_length=1)


# ──────────────────────────────────────────────────────────────────────────
# Corridor templates
# ──────────────────────────────────────────────────────────────────────────


class CorridorTemplate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    scenario: ScenarioType
    primary: tuple[int, int]
    scores: list[tuple[int, int]] = Field(min_length=1, max_length=8)


# ──────────────────────────────────────────────────────────────────────────
# Stake levels
# ──────────────────────────────────────────────────────────────────────────


class StakeBand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    level: StakeLevel
    min_pct: YamlDecimal = Field(ge=Decimal("0"), le=Decimal("0.30"))
    max_pct: YamlDecimal = Field(ge=Decimal("0"), le=Decimal("0.30"))
    default_pct: YamlDecimal = Field(ge=Decimal("0"), le=Decimal("0.30"))


# ──────────────────────────────────────────────────────────────────────────
# Loaders
# ──────────────────────────────────────────────────────────────────────────


def _read_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def load_classification_rules() -> tuple[ClassificationRule, ...]:
    raw = _read_yaml(CONFIG_DIR / "classification_rules.yaml")
    return tuple(ClassificationRule.model_validate(r) for r in raw)


@lru_cache(maxsize=1)
def load_corridor_templates() -> dict[ScenarioType, CorridorTemplate]:
    raw = _read_yaml(CONFIG_DIR / "corridor_templates.yaml")
    templates = [CorridorTemplate.model_validate(r) for r in raw]
    by_scenario: dict[ScenarioType, CorridorTemplate] = {}
    for t in templates:
        if t.scenario in by_scenario:
            raise ValueError(f"duplicate corridor template for scenario={t.scenario}")
        by_scenario[t.scenario] = t
    return by_scenario


@lru_cache(maxsize=1)
def load_stake_bands() -> dict[StakeLevel, StakeBand]:
    raw = _read_yaml(CONFIG_DIR / "stake_levels.yaml")
    bands = [StakeBand.model_validate(r) for r in raw]
    by_level: dict[StakeLevel, StakeBand] = {}
    for b in bands:
        if b.level in by_level:
            raise ValueError(f"duplicate stake band for level={b.level}")
        if b.min_pct > b.max_pct:
            raise ValueError(f"stake band {b.level}: min_pct > max_pct")
        if not (b.min_pct <= b.default_pct <= b.max_pct):
            raise ValueError(f"stake band {b.level}: default_pct outside [min, max]")
        by_level[b.level] = b
    return by_level


def reset_cache() -> None:
    """Drop all rule caches — used by tests and by ``tiger rules show``."""
    load_classification_rules.cache_clear()
    load_corridor_templates.cache_clear()
    load_stake_bands.cache_clear()
