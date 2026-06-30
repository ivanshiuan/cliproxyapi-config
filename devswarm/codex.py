"""Codex — the single, structured source of truth for DevSwarm's invariants.

The project's "不變法則" (Decimal-only money, append-only ledgers, soft delete,
audit-through-service, tz-aware datetimes, typed exceptions) used to live in
three places at once: CLAUDE.md prose, the Architect's system prompt, and the
Reviewer's rubric. Changing one meant editing three, and they drifted.

Codex collapses them into one structured registry. Both the Architect (which
emits the per-task constraints) and the Reviewer (which adjudicates the produced
code) inject the SAME rendered block, so the rules have exactly one home.

Design notes:
- Pure data + pure render functions. No I/O, no LLM, importable anywhere.
- ``CODEX_VERSION`` is bumped whenever an invariant's text changes, so a run's
  telemetry can pin which ruleset adjudicated it (mirrors prompts/_versions.py).
- A prompt that embeds ``render_markdown()`` must bump ITS prompt version too
  when Codex changes, since the embedded text is part of that prompt.
"""

from __future__ import annotations

from dataclasses import dataclass

CODEX_VERSION = "1.0.0"

# Severity vocabulary shared with the Reviewer's verdict schema.
SEVERITIES = ("CRITICAL", "HIGH")


@dataclass(frozen=True)
class Invariant:
    """One non-negotiable project rule, structured for both humans and agents."""

    id: str  # stable handle, e.g. "MONEY-001"; cited as a finding's `basis`
    title: str  # short human label
    severity: str  # one of SEVERITIES — how bad a violation is
    tags: tuple[str, ...]  # domain tags for optional filtering
    rule: str  # the imperative, in one or two sentences
    rationale: str  # why it exists — lets a reviewer explain a finding
    check_hint: str  # what to look for when auditing code against it


# The canonical registry. Order is the rendering order. IDs are stable and must
# never be reused for a different rule.
INVARIANTS: tuple[Invariant, ...] = (
    Invariant(
        id="MONEY-001",
        title="金錢一律用 Decimal,永不 float",
        severity="CRITICAL",
        tags=("money",),
        rule=(
            "Store, accept, return, and compare every monetary value as "
            "decimal.Decimal. Never float or int. Quantize values returned to a "
            "caller to Decimal('0.01') with ROUND_HALF_UP."
        ),
        rationale=(
            "Float rounding drift silently corrupts ledgers and P&L and cannot "
            "be audited. This is the project's first law."
        ),
        check_hint=(
            "float() on money, round(x, 2) returning float, Decimal(100.00) "
            "(float arg) in tests, missing quantize on returned amounts."
        ),
    ),
    Invariant(
        id="LEDGER-001",
        title="Ledger 表 append-only",
        severity="CRITICAL",
        tags=("ledger", "persistence"),
        rule=(
            "Ledger-like records (stock movements, points ledger, audit log) are "
            "append-only. A correction is a NEW appended row (e.g. a reversal), "
            "never an UPDATE or DELETE of an existing row."
        ),
        rationale=(
            "Append-only is what makes the ledger auditable; the DB enforces it "
            "with a RULE, so code that tries to mutate is both wrong and doomed."
        ),
        check_hint="UPDATE/DELETE against a ledger table; mutating a prior movement in place.",
    ),
    Invariant(
        id="SOFT-DELETE-001",
        title="顧客面記錄軟刪除",
        severity="HIGH",
        tags=("persistence", "customer"),
        rule=(
            "Customer-facing records are soft-deleted via a deleted_at timestamp, "
            "never hard-deleted. Queries must exclude soft-deleted rows by default."
        ),
        rationale="Hard deletes destroy history customers and audits may later need.",
        check_hint="DELETE FROM on a customer-facing table; queries ignoring deleted_at.",
    ),
    Invariant(
        id="AUDIT-001",
        title="稽核走 audit service",
        severity="HIGH",
        tags=("audit",),
        rule=(
            "Write audit entries through the audit service "
            "(services/audit_service.audit), never by constructing/inserting an "
            "AuditLog row directly."
        ),
        rationale="A single audit path keeps the trail consistent, complete, and redaction-safe.",
        check_hint="Direct AuditLog(...) construction or raw INSERT into the audit table.",
    ),
    Invariant(
        id="TZ-001",
        title="時間一律 tz-aware",
        severity="HIGH",
        tags=("datetime",),
        rule=(
            "All datetimes are timezone-aware. Persist UTC; present Asia/Taipei. "
            "Never construct a naive datetime in business logic."
        ),
        rationale="Naive datetimes cause off-by-hours bugs across the UTC/Taipei boundary.",
        check_hint="datetime.now() without tz, datetime(...) without tzinfo, utcnow().",
    ),
    Invariant(
        id="EXC-001",
        title="只用 typed 例外,不裸 raise",
        severity="HIGH",
        tags=("errors",),
        rule=(
            "Public functions raise the module's typed exception hierarchy "
            "(or the DomainError family in restaurant_api). Never raise bare "
            "Exception/RuntimeError, never use assert for runtime validation, "
            "never raise raw HTTPException from a service."
        ),
        rationale="Typed errors let callers handle failures precisely; assert is stripped under -O.",
        check_hint="raise Exception/RuntimeError, assert for validation, raw HTTPException in a service.",
    ),
)


def all_invariants() -> tuple[Invariant, ...]:
    """Return every invariant in canonical order."""
    return INVARIANTS


def get(invariant_id: str) -> Invariant:
    """Look up one invariant by id; raises KeyError if unknown."""
    for inv in INVARIANTS:
        if inv.id == invariant_id:
            return inv
    raise KeyError(invariant_id)


def select(tags: tuple[str, ...] | None = None) -> tuple[Invariant, ...]:
    """Return invariants matching any of `tags`; all of them when `tags` is None."""
    if not tags:
        return INVARIANTS
    wanted = set(tags)
    return tuple(inv for inv in INVARIANTS if wanted.intersection(inv.tags))


def render_markdown(tags: tuple[str, ...] | None = None) -> str:
    """Render the invariants as a numbered markdown block for prompt injection.

    The same rendered text is embedded into the Architect and Reviewer system
    prompts, which is what guarantees a single source of truth.
    """
    lines: list[str] = []
    for i, inv in enumerate(select(tags), start=1):
        lines.append(
            f"{i}. [{inv.severity}] {inv.id} — {inv.title}\n"
            f"   - rule: {inv.rule}\n"
            f"   - why: {inv.rationale}\n"
            f"   - audit: {inv.check_hint}"
        )
    return "\n".join(lines)
