"""UGC (打卡 / 評論換獎) service — human-in-the-loop acquisition reward.

Members submit proof of a Google review / IG post / check-in; staff approve or
reject from a queue. Only an approval grants points (reason ``ugc.reward``) and
pushes a LINE thank-you. The reward amount depends on the content kind — a
Google review (highest SEO value) is worth more than a check-in.

Boundaries: ``flush()`` only; the DI layer owns the transaction. The submission
row is mutable (status transitions) — not a ledger — so no append-only RULE.
"""

from __future__ import annotations

import contextlib
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.errors import ConflictError, NotFoundError
from ..integrations.line import LineMessage, LineMessenger, get_messenger
from ..models import Customer, CustomerPointsLedger, UgcKind, UgcStatus, UgcSubmission
from ..schemas.ugc import (
    UgcReviewRequest,
    UgcSubmissionResponse,
    UgcSubmitRequest,
)
from .audit_service import audit

# Reward by content kind (tunable; Google review ranks highest for discovery).
_REWARD_BY_KIND: dict[UgcKind, Decimal] = {
    UgcKind.GOOGLE_REVIEW: Decimal("100"),
    UgcKind.IG_POST: Decimal("80"),
    UgcKind.CHECKIN: Decimal("50"),
}


async def submit(
    session: AsyncSession,
    payload: UgcSubmitRequest,
    *,
    tenant_id: uuid.UUID,
) -> UgcSubmissionResponse:
    """Queue a member's UGC claim for staff review."""
    customer = (
        await session.execute(
            select(Customer).where(
                Customer.id == payload.customer_id,
                Customer.tenant_id == tenant_id,
                Customer.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if customer is None:
        raise NotFoundError(
            message=f"customer {payload.customer_id} not found",
            details={"customer_id": str(payload.customer_id)},
        )

    sub = UgcSubmission(
        tenant_id=tenant_id,
        customer_id=customer.id,
        kind=payload.kind,
        proof_url=payload.proof_url,
        status=UgcStatus.PENDING,
    )
    session.add(sub)
    await session.flush()
    await audit(
        session,
        action="ugc.submitted",
        tenant_id=tenant_id,
        target=("ugc_submissions", sub.id),
        after={"customer_id": str(customer.id), "kind": payload.kind.value},
    )
    return UgcSubmissionResponse.model_validate(sub)


async def _load_pending(
    session: AsyncSession, submission_id: uuid.UUID, tenant_id: uuid.UUID
) -> UgcSubmission:
    """Row-lock a submission and assert it's still pending (one-shot review)."""
    sub = (
        await session.execute(
            select(UgcSubmission)
            .where(
                UgcSubmission.id == submission_id,
                UgcSubmission.tenant_id == tenant_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if sub is None:
        raise NotFoundError(
            message=f"submission {submission_id} not found",
            details={"submission_id": str(submission_id)},
        )
    if sub.status != UgcStatus.PENDING:
        raise ConflictError(
            message="submission already reviewed",
            details={"status": sub.status.value},
        )
    return sub


async def approve(
    session: AsyncSession,
    submission_id: uuid.UUID,
    payload: UgcReviewRequest,
    *,
    tenant_id: uuid.UUID,
    messenger: LineMessenger | None = None,
) -> UgcSubmissionResponse:
    """Approve a pending submission: grant reward points + LINE thank-you."""
    sub = await _load_pending(session, submission_id, tenant_id)
    reward = _REWARD_BY_KIND.get(sub.kind, Decimal("0"))

    sub.status = UgcStatus.APPROVED
    sub.reward_points = reward
    sub.reviewed_by = payload.reviewed_by
    sub.reviewed_at = datetime.now(UTC)
    sub.note = payload.note

    # Lock the customer row before the cached points_balance read-modify-write
    # (two submissions for the same member approved back-to-back must not clobber
    # each other's balance bump). Tenant-scoped for safety.
    customer = (
        await session.execute(
            select(Customer)
            .where(Customer.id == sub.customer_id, Customer.tenant_id == tenant_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if customer is not None and reward > 0:
        session.add(
            CustomerPointsLedger(
                tenant_id=tenant_id,
                customer_id=customer.id,
                delta=reward,
                reason="ugc.reward",
                note=f"{sub.kind.value} approved",
            )
        )
        customer.points_balance = (customer.points_balance or Decimal("0")) + reward
    await session.flush()
    await audit(
        session,
        action="ugc.approved",
        tenant_id=tenant_id,
        actor_id=payload.reviewed_by,
        target=("ugc_submissions", sub.id),
        after={"reward_points": str(reward), "kind": sub.kind.value},
    )

    out = messenger or get_messenger()
    if customer is not None and customer.line_user_id and reward > 0:
        with contextlib.suppress(Exception):  # best-effort
            await out.push(
                customer.line_user_id,
                LineMessage(
                    kind="text",
                    text=(
                        f"感謝您的分享!\n您的貼文已通過審核 "
                        f"送您 {reward} 點回饋 期待您再次光臨 💚"
                    ),
                ),
            )
    return UgcSubmissionResponse.model_validate(sub)


async def reject(
    session: AsyncSession,
    submission_id: uuid.UUID,
    payload: UgcReviewRequest,
    *,
    tenant_id: uuid.UUID,
) -> UgcSubmissionResponse:
    """Reject a pending submission (no points granted)."""
    sub = await _load_pending(session, submission_id, tenant_id)
    sub.status = UgcStatus.REJECTED
    sub.reviewed_by = payload.reviewed_by
    sub.reviewed_at = datetime.now(UTC)
    sub.note = payload.note
    await session.flush()
    await audit(
        session,
        action="ugc.rejected",
        tenant_id=tenant_id,
        actor_id=payload.reviewed_by,
        target=("ugc_submissions", sub.id),
        after={"kind": sub.kind.value, "note": payload.note},
    )
    return UgcSubmissionResponse.model_validate(sub)


async def list_submissions(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    status: UgcStatus | None = None,
    limit: int = 200,
) -> list[UgcSubmissionResponse]:
    """List submissions (optionally filtered by status) — staff review queue."""
    stmt = select(UgcSubmission).where(UgcSubmission.tenant_id == tenant_id)
    if status is not None:
        stmt = stmt.where(UgcSubmission.status == status)
    stmt = stmt.order_by(
        UgcSubmission.created_at.desc(), UgcSubmission.id.desc()
    ).limit(limit)
    rows = (await session.execute(stmt)).scalars().all()
    return [UgcSubmissionResponse.model_validate(r) for r in rows]


__all__ = [
    "approve",
    "list_submissions",
    "reject",
    "submit",
]
