"""FastAPI router — ``/crm`` (CRM 關係經營層, 對標 Twenty CRM).

Thin HTTP layer over ``services/crm_service`` (notes / tasks / tags /
interactions CRUD) and ``services/crm_timeline_service`` (unified customer
timeline). Tenant comes from the ``TenantId`` DI seam like every other
router; all errors are ``DomainError`` envelopes raised by the services.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime

from ..api.deps import DbSession, TenantId
from ..api.errors import DomainError, ErrorBody, domain_error_handler
from ..models import CustomerTaskStatus
from ..schemas.crm import (
    CustomerTagResponse,
    InteractionCreateRequest,
    InteractionResponse,
    NoteCreateRequest,
    NotePatchRequest,
    NoteResponse,
    TagAttachRequest,
    TagCreateRequest,
    TagPatchRequest,
    TagResponse,
    TaskCreateRequest,
    TaskPatchRequest,
    TaskResponse,
    TimelineResponse,
)
from ..services import crm_service, crm_timeline_service

router = APIRouter(prefix="/crm", tags=["crm"])

# Module-level Query() singletons (ruff B008 avoidance, FastAPI metadata kept).
_Q_STATUS = Query(default=None)
_Q_ASSIGNEE = Query(default=None)
_Q_DUE_BEFORE = Query(default=None)
_Q_BEFORE = Query(default=None)
_Q_LIMIT_50 = Query(default=50, ge=1, le=200)
_Q_OFFSET = Query(default=0, ge=0)


# ──────────────────────────────────────────────────────────────────────────
# Notes
# ──────────────────────────────────────────────────────────────────────────


@router.post(
    "/customers/{customer_id}/notes",
    response_model=NoteResponse,
    status_code=status.HTTP_201_CREATED,
    summary="新增客戶備註",
)
async def create_note_endpoint(
    customer_id: uuid.UUID,
    payload: NoteCreateRequest,
    session: DbSession,
    tenant_id: TenantId,
) -> NoteResponse:
    return await crm_service.create_note(session, customer_id, payload, tenant_id=tenant_id)


@router.get(
    "/customers/{customer_id}/notes",
    response_model=list[NoteResponse],
    summary="列出客戶備註 (未軟刪, created_at desc)",
)
async def list_notes_endpoint(
    customer_id: uuid.UUID, session: DbSession, tenant_id: TenantId
) -> list[NoteResponse]:
    return await crm_service.list_notes(session, customer_id, tenant_id=tenant_id)


@router.patch("/notes/{note_id}", response_model=NoteResponse, summary="修改備註內容")
async def patch_note_endpoint(
    note_id: uuid.UUID,
    payload: NotePatchRequest,
    session: DbSession,
    tenant_id: TenantId,
) -> NoteResponse:
    return await crm_service.patch_note(session, note_id, payload, tenant_id=tenant_id)


@router.delete(
    "/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT, summary="軟刪備註"
)
async def delete_note_endpoint(
    note_id: uuid.UUID, session: DbSession, tenant_id: TenantId
) -> None:
    await crm_service.delete_note(session, note_id, tenant_id=tenant_id)


# ──────────────────────────────────────────────────────────────────────────
# Tasks
# ──────────────────────────────────────────────────────────────────────────


@router.post(
    "/customers/{customer_id}/tasks",
    response_model=TaskResponse,
    status_code=status.HTTP_201_CREATED,
    summary="建立客戶跟進任務 (status=open)",
)
async def create_task_endpoint(
    customer_id: uuid.UUID,
    payload: TaskCreateRequest,
    session: DbSession,
    tenant_id: TenantId,
) -> TaskResponse:
    return await crm_service.create_task(session, customer_id, payload, tenant_id=tenant_id)


@router.get(
    "/customers/{customer_id}/tasks",
    response_model=list[TaskResponse],
    summary="列出單一客戶的任務 (可 ?status=)",
)
async def list_customer_tasks_endpoint(
    customer_id: uuid.UUID,
    session: DbSession,
    tenant_id: TenantId,
    status: CustomerTaskStatus | None = _Q_STATUS,
) -> list[TaskResponse]:
    return await crm_service.list_customer_tasks(
        session, customer_id, tenant_id=tenant_id, status=status
    )


@router.get(
    "/tasks",
    response_model=list[TaskResponse],
    summary="跨客戶待辦清單 (店長用; due_at asc nulls last)",
)
async def list_tasks_endpoint(
    session: DbSession,
    tenant_id: TenantId,
    assignee_employee_id: uuid.UUID | None = _Q_ASSIGNEE,
    status: CustomerTaskStatus | None = _Q_STATUS,
    due_before: AwareDatetime | None = _Q_DUE_BEFORE,
    limit: int = _Q_LIMIT_50,
    offset: int = _Q_OFFSET,
) -> list[TaskResponse]:
    return await crm_service.list_tasks(
        session,
        tenant_id=tenant_id,
        assignee_employee_id=assignee_employee_id,
        status=status,
        due_before=due_before,
        limit=limit,
        offset=offset,
    )


@router.patch(
    "/tasks/{task_id}",
    response_model=TaskResponse,
    summary="更新任務 (done 寫 completed_at; done→open 清空)",
)
async def patch_task_endpoint(
    task_id: uuid.UUID,
    payload: TaskPatchRequest,
    session: DbSession,
    tenant_id: TenantId,
) -> TaskResponse:
    return await crm_service.patch_task(session, task_id, payload, tenant_id=tenant_id)


@router.delete(
    "/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT, summary="軟刪任務"
)
async def delete_task_endpoint(
    task_id: uuid.UUID, session: DbSession, tenant_id: TenantId
) -> None:
    await crm_service.delete_task(session, task_id, tenant_id=tenant_id)


# ──────────────────────────────────────────────────────────────────────────
# Tags
# ──────────────────────────────────────────────────────────────────────────


@router.post(
    "/tags",
    response_model=TagResponse,
    status_code=status.HTTP_201_CREATED,
    summary="建立標籤 (同 tenant 活標籤同名 → 409)",
)
async def create_tag_endpoint(
    payload: TagCreateRequest, session: DbSession, tenant_id: TenantId
) -> TagResponse:
    return await crm_service.create_tag(session, payload, tenant_id=tenant_id)


@router.get("/tags", response_model=list[TagResponse], summary="列出標籤")
async def list_tags_endpoint(session: DbSession, tenant_id: TenantId) -> list[TagResponse]:
    return await crm_service.list_tags(session, tenant_id=tenant_id)


@router.patch("/tags/{tag_id}", response_model=TagResponse, summary="更新標籤")
async def patch_tag_endpoint(
    tag_id: uuid.UUID,
    payload: TagPatchRequest,
    session: DbSession,
    tenant_id: TenantId,
) -> TagResponse:
    return await crm_service.patch_tag(session, tag_id, payload, tenant_id=tenant_id)


@router.delete(
    "/tags/{tag_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="軟刪標籤 (同名可重建)",
)
async def delete_tag_endpoint(
    tag_id: uuid.UUID, session: DbSession, tenant_id: TenantId
) -> None:
    await crm_service.delete_tag(session, tag_id, tenant_id=tenant_id)


@router.post(
    "/customers/{customer_id}/tags",
    response_model=CustomerTagResponse,
    status_code=status.HTTP_201_CREATED,
    summary="替客戶貼標籤 (重複 → 409; 跨 tenant → 422)",
)
async def attach_tag_endpoint(
    customer_id: uuid.UUID,
    payload: TagAttachRequest,
    session: DbSession,
    tenant_id: TenantId,
) -> CustomerTagResponse:
    return await crm_service.attach_tag(session, customer_id, payload, tenant_id=tenant_id)


@router.delete(
    "/customers/{customer_id}/tags/{tag_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="撕掉客戶標籤 (沒貼過 → 404)",
)
async def detach_tag_endpoint(
    customer_id: uuid.UUID,
    tag_id: uuid.UUID,
    session: DbSession,
    tenant_id: TenantId,
) -> None:
    await crm_service.detach_tag(session, customer_id, tag_id, tenant_id=tenant_id)


@router.get(
    "/customers/{customer_id}/tags",
    response_model=list[CustomerTagResponse],
    summary="列出客戶標籤",
)
async def list_customer_tags_endpoint(
    customer_id: uuid.UUID, session: DbSession, tenant_id: TenantId
) -> list[CustomerTagResponse]:
    return await crm_service.list_customer_tags(session, customer_id, tenant_id=tenant_id)


# ──────────────────────────────────────────────────────────────────────────
# Interactions
# ──────────────────────────────────────────────────────────────────────────


@router.post(
    "/customers/{customer_id}/interactions",
    response_model=InteractionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="記錄一次客戶接觸 (LINE/電話/臨櫃…)",
)
async def create_interaction_endpoint(
    customer_id: uuid.UUID,
    payload: InteractionCreateRequest,
    session: DbSession,
    tenant_id: TenantId,
) -> InteractionResponse:
    return await crm_service.create_interaction(
        session, customer_id, payload, tenant_id=tenant_id
    )


@router.get(
    "/customers/{customer_id}/interactions",
    response_model=list[InteractionResponse],
    summary="列出客戶接觸紀錄 (occurred_at desc)",
)
async def list_interactions_endpoint(
    customer_id: uuid.UUID, session: DbSession, tenant_id: TenantId
) -> list[InteractionResponse]:
    return await crm_service.list_interactions(session, customer_id, tenant_id=tenant_id)


# ──────────────────────────────────────────────────────────────────────────
# Timeline
# ──────────────────────────────────────────────────────────────────────────


@router.get(
    "/customers/{customer_id}/timeline",
    response_model=TimelineResponse,
    summary="客戶統一時間軸 (訂單/點數/儲值/推薦/備註/任務/接觸; before 為 exclusive cursor)",
)
async def timeline_endpoint(
    customer_id: uuid.UUID,
    session: DbSession,
    tenant_id: TenantId,
    before: AwareDatetime | None = _Q_BEFORE,
    limit: int = _Q_LIMIT_50,
) -> TimelineResponse:
    return await crm_timeline_service.get_timeline(
        session, customer_id, tenant_id=tenant_id, before=before, limit=limit
    )


# ──────────────────────────────────────────────────────────────────────────
# Self-mount helpers — same pattern as routers/orders.py. ``_mount()`` is NOT
# called at import time (would circular-import main); main.py / tests call it.
# ──────────────────────────────────────────────────────────────────────────


def _ensure_envelope_handler() -> None:
    """Register the DomainError handler on the shared app exactly once."""
    from ..main import app

    already = any(
        getattr(handler, "__wrapped_for_domain_error__", False)
        for handler in (
            (app.exception_handlers.get(DomainError) and [app.exception_handlers[DomainError]]) or []
        )
    )
    if already:
        return

    async def _handler(request: Request, exc: Exception) -> JSONResponse:
        if isinstance(exc, DomainError):
            return await domain_error_handler(request, exc)
        body = ErrorBody(code="INTERNAL", message=str(exc)).model_dump()
        return JSONResponse(status_code=500, content={"error": body})

    _handler.__wrapped_for_domain_error__ = True  # type: ignore[attr-defined]
    app.add_exception_handler(DomainError, _handler)


def _mount() -> None:
    """Attach this router to the shared FastAPI app, idempotently."""
    from ..main import app

    for existing_route in app.router.routes:
        if getattr(existing_route, "path", "").startswith("/crm"):
            return
    app.include_router(router)
    _ensure_envelope_handler()


__all__ = ["_mount", "router"]
