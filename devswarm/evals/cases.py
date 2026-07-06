"""Golden cases for the Reviewer regression eval.

Each case is a small module + test pair plus the PRD/constraints the Reviewer
adjudicates against, and the expected outcome. BLOCK cases name the Codex
invariant id the Reviewer should cite as the finding's `basis`.

Keep cases small and unambiguous: the point is to catch *regressions* in the
Reviewer's judgment, so each bad case should have exactly one clear defect that
maps to one invariant.
"""

from __future__ import annotations

from dataclasses import dataclass, field

_PRD_NET_REVENUE = (
    "## PRD — net_revenue\n"
    "Goal: compute net revenue from a gross amount and a discount rate.\n"
    "### Acceptance Criteria\n"
    "AC1: `net_revenue(gross, rate)` returns a `Decimal` quantized to 0.01 "
    "using ROUND_HALF_UP.\n"
    "AC2: negative gross raises a typed error.\n"
)

_PRD_STOCK = (
    "## PRD — apply_stock_correction\n"
    "Goal: correct a prior stock movement.\n"
    "### Acceptance Criteria\n"
    "AC1: a correction is recorded as a NEW reversal movement; existing "
    "movements are never mutated (stock_movements is an append-only ledger).\n"
)

_PRD_EXPIRY = (
    "## PRD — next_expiry\n"
    "Goal: return the timezone-aware expiry instant for a batch.\n"
    "### Acceptance Criteria\n"
    "AC1: the returned datetime is timezone-aware (UTC stored).\n"
)


@dataclass(frozen=True)
class EvalCase:
    """One golden adjudication case for the Reviewer."""

    name: str
    prd: str
    constraints: tuple[str, ...]
    files: dict[str, str]
    expect_verdict: str  # "PASS" | "BLOCK"
    expect_basis: tuple[str, ...] = field(default_factory=tuple)  # Codex ids (BLOCK only)


# ── known-bad: each has exactly one defect mapping to one Codex invariant ──

# Single defect by design: AC2 (negative gross) is handled, so the ONLY thing
# wrong is float money → the Reviewer's sole valid basis is MONEY-001. (If the
# code also missed AC2, a correct Reviewer could block on AC2 and the eval would
# wrongly score a basis miss.)
_FLOAT_MONEY = (
    '"""net revenue."""\n\n'
    "class NegativeAmountError(Exception):\n"
    "    pass\n\n\n"
    "def net_revenue(gross, rate):\n"
    "    if gross < 0:\n"
    "        raise NegativeAmountError('gross must be non-negative')\n"
    "    return round(gross * (1 - rate), 2)\n"
)
_FLOAT_MONEY_TEST = (
    "from m import net_revenue\n\n"
    "def test_positive():\n"
    "    assert net_revenue(100, 0.1) > 0\n"
)

_LEDGER_MUTATION = (
    '"""stock correction."""\n\n'
    "def apply_stock_correction(session, movement, new_qty):\n"
    "    movement.quantity = new_qty  # mutate the existing ledger row\n"
    "    session.add(movement)\n"
    "    return movement\n"
)
_LEDGER_TEST = (
    "from m import apply_stock_correction\n\n"
    "def test_corrects():\n"
    "    assert apply_stock_correction is not None\n"
)

_NAIVE_DATETIME = (
    '"""expiry."""\n\n'
    "from datetime import datetime, timedelta\n\n"
    "def next_expiry(days):\n"
    "    return datetime.now() + timedelta(days=days)  # naive datetime\n"
)
_NAIVE_TEST = (
    "from m import next_expiry\n\n"
    "def test_returns():\n"
    "    assert next_expiry(1) is not None\n"
)

# ── known-good: clean, compliant, fully-tested ──

_CLEAN_DECIMAL = (
    '"""net revenue."""\n\n'
    "from decimal import Decimal, ROUND_HALF_UP\n\n"
    "_CENT = Decimal('0.01')\n\n\n"
    "class NetRevenueError(Exception):\n"
    "    pass\n\n\n"
    "class NegativeAmountError(NetRevenueError):\n"
    "    pass\n\n\n"
    "def net_revenue(gross: Decimal, rate: Decimal) -> Decimal:\n"
    '    """Return gross net of the discount rate, quantized to the cent."""\n'
    "    if gross < Decimal('0'):\n"
    "        raise NegativeAmountError('gross must be non-negative')\n"
    "    return (gross * (Decimal('1') - rate)).quantize(_CENT, rounding=ROUND_HALF_UP)\n"
)
_CLEAN_TEST = (
    "from decimal import Decimal\n"
    "import pytest\n"
    "from m import net_revenue, NegativeAmountError\n\n"
    "def test_ac1_quantized():\n"
    "    assert net_revenue(Decimal('1000'), Decimal('0.10')) == Decimal('900.00')\n\n"
    "def test_ac2_negative_raises():\n"
    "    with pytest.raises(NegativeAmountError):\n"
    "        net_revenue(Decimal('-1'), Decimal('0'))\n"
)


GOLDEN_CASES: tuple[EvalCase, ...] = (
    EvalCase(
        name="float_money_blocked",
        prd=_PRD_NET_REVENUE,
        constraints=("All monetary values MUST be Decimal quantized to 0.01.",),
        files={"m.py": _FLOAT_MONEY, "test_m.py": _FLOAT_MONEY_TEST},
        expect_verdict="BLOCK",
        expect_basis=("MONEY-001",),
    ),
    EvalCase(
        name="ledger_mutation_blocked",
        prd=_PRD_STOCK,
        constraints=("stock_movements is append-only; corrections append a reversal.",),
        files={"m.py": _LEDGER_MUTATION, "test_m.py": _LEDGER_TEST},
        expect_verdict="BLOCK",
        expect_basis=("LEDGER-001",),
    ),
    EvalCase(
        name="naive_datetime_blocked",
        prd=_PRD_EXPIRY,
        constraints=("All datetimes MUST be timezone-aware.",),
        files={"m.py": _NAIVE_DATETIME, "test_m.py": _NAIVE_TEST},
        expect_verdict="BLOCK",
        expect_basis=("TZ-001",),
    ),
    EvalCase(
        name="clean_decimal_pass",
        prd=_PRD_NET_REVENUE,
        constraints=("All monetary values MUST be Decimal quantized to 0.01.",),
        files={"m.py": _CLEAN_DECIMAL, "test_m.py": _CLEAN_TEST},
        expect_verdict="PASS",
    ),
)
