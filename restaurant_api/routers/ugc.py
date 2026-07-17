"""``/ugc`` router — 打卡 / 評論換獎 submission + staff review queue.

Flow: a member POSTs a submission (pending) → staff GET the pending queue →
staff approve/reject. Approval grants points + pushes a LINE thank-you.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status

from ..api.deps import DbSession, Messenger, TenantId
from ..models import UgcStatus
from ..schemas.ugc import UgcReviewRequest, UgcSubmissionResponse, UgcSubmitRequest
from ..services import ugc_service

_Q_STATUS = Query(default=None)
_Q_LIMIT = Query(default=200, ge=1, le=500)

router = APIRouter(prefix="/ugc", tags=["ugc"])


@router.post(
    "/submissions",
    response_model=UgcSubmissionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="會員提交打卡/評論 (進入待審佇列)",
)
async def submit_ugc(
    payload: UgcSubmitRequest,
    session: DbSession,
    tenant_id: TenantId,
) -> UgcSubmissionResponse:
    return await ugc_service.submit(session, payload, tenant_id=tenant_id)


@router.get(
    "/submissions",
    response_model=list[UgcSubmissionResponse],
    summary="UGC 佇列 (可依 status 篩選; newest first)",
)
async def list_ugc(
    session: DbSession,
    tenant_id: TenantId,
    ugc_status: UgcStatus | None = _Q_STATUS,
    limit: int = _Q_LIMIT,
) -> list[UgcSubmissionResponse]:
    return await ugc_service.list_submissions(
        session, tenant_id=tenant_id, status=ugc_status, limit=limit
    )


@router.post(
    "/submissions/{submission_id}/approve",
    response_model=UgcSubmissionResponse,
    summary="店員審核通過 (發點 + LINE 道謝; 已審回 409)",
)
async def approve_ugc(
    submission_id: uuid.UUID,
    payload: UgcReviewRequest,
    session: DbSession,
    tenant_id: TenantId,
    messenger: Messenger,
) -> UgcSubmissionResponse:
    return await ugc_service.approve(
        session, submission_id, payload, tenant_id=tenant_id, messenger=messenger
    )


@router.post(
    "/submissions/{submission_id}/reject",
    response_model=UgcSubmissionResponse,
    summary="店員審核拒絕 (不發點; 已審回 409)",
)
async def reject_ugc(
    submission_id: uuid.UUID,
    payload: UgcReviewRequest,
    session: DbSession,
    tenant_id: TenantId,
) -> UgcSubmissionResponse:
    return await ugc_service.reject(
        session, submission_id, payload, tenant_id=tenant_id
    )


__all__ = ["router"]
