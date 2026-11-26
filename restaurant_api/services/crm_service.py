"""CRM relationship layer business logic — notes / tasks / tags / interactions.

Boundary rules (same contract as every other service in this repo):
- ``flush()`` only, never ``commit()`` — the transaction boundary lives in
  ``api/deps.get_db``.
- Domain errors raise ``DomainError`` subclasses; the router never
  translates exceptions.
- ``tenant_id`` comes from the ``TenantId`` DI seam (header / settings
  default), exactly like ``customers_service`` / ``tables_service``.
- Every write goes through ``audit_service.audit`` with a ``crm.*`` action.

Reference-integrity rules:
- The customer must exist under the request tenant and not be soft-deleted
  → otherwise 404.
- A referenced employee / tag that does not exist (or is soft-deleted) → 404;
  one that exists but belongs to another tenant → 422.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.errors import ConflictError, NotFoundError, ValidationError
from ..models import (
    Customer,
    CustomerInteraction,
    CustomerNote,
    CustomerTag,
    CustomerTagLink,
    CustomerTask,
    CustomerTaskStatus,
    Employee,
)
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
)
from .audit_service import audit

logger = logging.getLogger("restaurant_api.services.crm")


# ──────────────────────────────────────────────────────────────────────────
# Shared loaders / guards
# ──────────────────────────────────────────────────────────────────────────


def _jsonable(v: Any) -> Any:
    """Audit-payload projection: enums → value, uuid/datetime → str."""
    if v is None or isinstance(v, (str, int, bool)):
        return v
    if isinstance(v, CustomerTaskStatus):
        return v.value
    if isinstance(v, datetime):
        return v.isoformat()
    return str(v)


async def load_customer(
    session: AsyncSession, customer_id: uuid.UUID, *, tenant_id: uuid.UUID
) -> Customer:
    """Live customer under this tenant, or 404."""
    row = (
        await session.execute(
            select(Customer).where(
                Customer.id == customer_id,
                Customer.tenant_id == tenant_id,
                Customer.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(
            f"customer {customer_id} not found",
            details={"customer_id": str(customer_id)},
        )
    return row


async def _require_employee(
    session: AsyncSession,
    employee_id: uuid.UUID | None,
    *,
    tenant_id: uuid.UUID,
    field: str,
) -> None:
    """404 if missing/soft-deleted, 422 if it belongs to another tenant."""
    if employee_id is None:
        return
    emp = await session.get(Employee, employee_id)
    if emp is None or emp.deleted_at is not None:
        raise NotFoundError(
            f"employee {employee_id} not found",
            details={field: str(employee_id)},
        )
    if emp.tenant_id != tenant_id:
        raise ValidationError(
            "employee belongs to a different tenant",
            details={field: str(employee_id)},
        )


async def _load_one[T](stmt: Select[T], session: AsyncSession, what: str, ident: uuid.UUID) -> T:
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise NotFoundError(f"{what} {ident} not found", details={f"{what}_id": str(ident)})
    return row


# ──────────────────────────────────────────────────────────────────────────
# Notes
# ──────────────────────────────────────────────────────────────────────────


async def _load_note(
    session: AsyncSession, note_id: uuid.UUID, *, tenant_id: uuid.UUID
) -> CustomerNote:
    return await _load_one(
        select(CustomerNote).where(
            CustomerNote.id == note_id,
            CustomerNote.tenant_id == tenant_id,
            CustomerNote.deleted_at.is_(None),
        ),
        session,
        "note",
        note_id,
    )


async def create_note(
    session: AsyncSession,
    customer_id: uuid.UUID,
    payload: NoteCreateRequest,
    *,
    tenant_id: uuid.UUID,
) -> NoteResponse:
    await load_customer(session, customer_id, tenant_id=tenant_id)
    await _require_employee(
        session, payload.author_employee_id, tenant_id=tenant_id, field="author_employee_id"
    )
    row = CustomerNote(
        tenant_id=tenant_id,
        customer_id=customer_id,
        author_employee_id=payload.author_employee_id,
        body=payload.body,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)

    await audit(
        session,
        action="crm.note.created",
        tenant_id=tenant_id,
        actor_id=payload.author_employee_id,
        target=("customer_notes", row.id),
        after={"customer_id": str(customer_id), "body_len": len(row.body)},
    )
    logger.info("crm.note.created note_id=%s customer_id=%s", row.id, customer_id)
    return NoteResponse.model_validate(row)


async def list_notes(
    session: AsyncSession,
    customer_id: uuid.UUID,
    *,
    tenant_id: uuid.UUID,
    limit: int = 200,
) -> list[NoteResponse]:
    await load_customer(session, customer_id, tenant_id=tenant_id)
    rows = (
        await session.execute(
            select(CustomerNote)
            .where(
                CustomerNote.customer_id == customer_id,
                CustomerNote.tenant_id == tenant_id,
                CustomerNote.deleted_at.is_(None),
            )
            # uuid7 id breaks created_at ties (same-transaction now()).
            .order_by(CustomerNote.created_at.desc(), CustomerNote.id.desc())
            .limit(limit)
        )
    ).scalars().all()
    return [NoteResponse.model_validate(r) for r in rows]


async def patch_note(
    session: AsyncSession,
    note_id: uuid.UUID,
    payload: NotePatchRequest,
    *,
    tenant_id: uuid.UUID,
) -> NoteResponse:
    row = await _load_note(session, note_id, tenant_id=tenant_id)
    if payload.body == row.body:
        return NoteResponse.model_validate(row)
    before = {"body": row.body}
    row.body = payload.body
    await session.flush()
    await session.refresh(row)

    await audit(
        session,
        action="crm.note.updated",
        tenant_id=tenant_id,
        target=("customer_notes", row.id),
        before=before,
        after={"body": row.body},
    )
    logger.info("crm.note.updated note_id=%s", row.id)
    return NoteResponse.model_validate(row)


async def delete_note(
    session: AsyncSession, note_id: uuid.UUID, *, tenant_id: uuid.UUID
) -> None:
    row = await _load_note(session, note_id, tenant_id=tenant_id)
    deleted_at = datetime.now(UTC)
    row.deleted_at = deleted_at
    await session.flush()

    await audit(
        session,
        action="crm.note.deleted",
        tenant_id=tenant_id,
        target=("customer_notes", row.id),
        after={"deleted_at": deleted_at.isoformat()},
    )
    logger.info("crm.note.deleted note_id=%s", row.id)


# ──────────────────────────────────────────────────────────────────────────
# Tasks
# ──────────────────────────────────────────────────────────────────────────


async def _load_task(
    session: AsyncSession, task_id: uuid.UUID, *, tenant_id: uuid.UUID
) -> CustomerTask:
    return await _load_one(
        select(CustomerTask).where(
            CustomerTask.id == task_id,
            CustomerTask.tenant_id == tenant_id,
            CustomerTask.deleted_at.is_(None),
        ),
        session,
        "task",
        task_id,
    )


async def create_task(
    session: AsyncSession,
    customer_id: uuid.UUID,
    payload: TaskCreateRequest,
    *,
    tenant_id: uuid.UUID,
) -> TaskResponse:
    await load_customer(session, customer_id, tenant_id=tenant_id)
    await _require_employee(
        session, payload.assignee_employee_id, tenant_id=tenant_id, field="assignee_employee_id"
    )
    await _require_employee(
        session,
        payload.created_by_employee_id,
        tenant_id=tenant_id,
        field="created_by_employee_id",
    )
    row = CustomerTask(
        tenant_id=tenant_id,
        customer_id=customer_id,
        title=payload.title,
        body=payload.body,
        assignee_employee_id=payload.assignee_employee_id,
        created_by_employee_id=payload.created_by_employee_id,
        due_at=payload.due_at,
        status=CustomerTaskStatus.OPEN,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)

    await audit(
        session,
        action="crm.task.created",
        tenant_id=tenant_id,
        actor_id=payload.created_by_employee_id,
        target=("customer_tasks", row.id),
        after={
            "customer_id": str(customer_id),
            "title": row.title,
            "assignee_employee_id": _jsonable(row.assignee_employee_id),
            "due_at": _jsonable(row.due_at),
            "status": row.status.value,
        },
    )
    logger.info("crm.task.created task_id=%s customer_id=%s", row.id, customer_id)
    return TaskResponse.model_validate(row)


async def list_customer_tasks(
    session: AsyncSession,
    customer_id: uuid.UUID,
    *,
    tenant_id: uuid.UUID,
    status: CustomerTaskStatus | None = None,
    limit: int = 200,
) -> list[TaskResponse]:
    await load_customer(session, customer_id, tenant_id=tenant_id)
    stmt = select(CustomerTask).where(
        CustomerTask.customer_id == customer_id,
        CustomerTask.tenant_id == tenant_id,
        CustomerTask.deleted_at.is_(None),
    )
    if status is not None:
        stmt = stmt.where(CustomerTask.status == status)
    stmt = stmt.order_by(CustomerTask.created_at.desc(), CustomerTask.id.desc()).limit(limit)
    rows = (await session.execute(stmt)).scalars().all()
    return [TaskResponse.model_validate(r) for r in rows]


async def list_tasks(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    assignee_employee_id: uuid.UUID | None = None,
    status: CustomerTaskStatus | None = None,
    due_before: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[TaskResponse]:
    """Cross-customer to-do list (店長 view), tenant-scoped.

    Ordered ``due_at ASC NULLS LAST`` (most urgent first), then
    ``created_at``/``id`` for a stable page order. Tasks on soft-deleted
    customers are excluded.
    """
    stmt = (
        select(CustomerTask)
        .join(Customer, Customer.id == CustomerTask.customer_id)
        .where(
            CustomerTask.tenant_id == tenant_id,
            CustomerTask.deleted_at.is_(None),
            Customer.deleted_at.is_(None),
        )
    )
    if assignee_employee_id is not None:
        stmt = stmt.where(CustomerTask.assignee_employee_id == assignee_employee_id)
    if status is not None:
        stmt = stmt.where(CustomerTask.status == status)
    if due_before is not None:
        stmt = stmt.where(CustomerTask.due_at < due_before)
    stmt = (
        stmt.order_by(
            CustomerTask.due_at.asc().nulls_last(),
            CustomerTask.created_at.asc(),
            CustomerTask.id.asc(),
        )
        .limit(limit)
        .offset(offset)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [TaskResponse.model_validate(r) for r in rows]


async def patch_task(
    session: AsyncSession,
    task_id: uuid.UUID,
    payload: TaskPatchRequest,
    *,
    tenant_id: uuid.UUID,
) -> TaskResponse:
    """Partial update. Status side effects:

    - ``* → done``      sets ``completed_at = now``
    - ``done → open``   clears ``completed_at``
    - ``* → cancelled`` leaves ``completed_at`` null (cleared if previously done)
    """
    row = await _load_task(session, task_id, tenant_id=tenant_id)
    changes = payload.model_dump(exclude_unset=True)
    if "assignee_employee_id" in changes:
        await _require_employee(
            session,
            changes["assignee_employee_id"],
            tenant_id=tenant_id,
            field="assignee_employee_id",
        )

    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    for field, value in changes.items():
        old = getattr(row, field)
        if old == value:
            continue
        before[field] = _jsonable(old)
        after[field] = _jsonable(value)
        setattr(row, field, value)

    if "status" in after:
        new_status = row.status
        before["completed_at"] = _jsonable(row.completed_at)
        if new_status is CustomerTaskStatus.DONE:
            row.completed_at = datetime.now(UTC)
        else:
            row.completed_at = None
        after["completed_at"] = _jsonable(row.completed_at)

    if not after:
        return TaskResponse.model_validate(row)

    await session.flush()
    await session.refresh(row)

    await audit(
        session,
        action="crm.task.updated",
        tenant_id=tenant_id,
        target=("customer_tasks", row.id),
        before=before,
        after=after,
    )
    if "status" in after:
        logger.info(
            "crm.task.status_changed task_id=%s from=%s to=%s",
            row.id,
            before["status"],
            after["status"],
        )
    else:
        logger.info("crm.task.updated task_id=%s fields=%s", row.id, sorted(after))
    return TaskResponse.model_validate(row)


async def delete_task(
    session: AsyncSession, task_id: uuid.UUID, *, tenant_id: uuid.UUID
) -> None:
    row = await _load_task(session, task_id, tenant_id=tenant_id)
    deleted_at = datetime.now(UTC)
    row.deleted_at = deleted_at
    await session.flush()

    await audit(
        session,
        action="crm.task.deleted",
        tenant_id=tenant_id,
        target=("customer_tasks", row.id),
        after={"deleted_at": deleted_at.isoformat()},
    )
    logger.info("crm.task.deleted task_id=%s", row.id)


# ──────────────────────────────────────────────────────────────────────────
# Tags
# ──────────────────────────────────────────────────────────────────────────


async def _tag_name_taken(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    name: str,
    exclude_id: uuid.UUID | None = None,
) -> bool:
    stmt = select(CustomerTag.id).where(
        CustomerTag.tenant_id == tenant_id,
        CustomerTag.name == name,
        CustomerTag.deleted_at.is_(None),
    )
    if exclude_id is not None:
        stmt = stmt.where(CustomerTag.id != exclude_id)
    return (await session.execute(stmt.limit(1))).scalar_one_or_none() is not None


async def _load_tag(
    session: AsyncSession, tag_id: uuid.UUID, *, tenant_id: uuid.UUID
) -> CustomerTag:
    return await _load_one(
        select(CustomerTag).where(
            CustomerTag.id == tag_id,
            CustomerTag.tenant_id == tenant_id,
            CustomerTag.deleted_at.is_(None),
        ),
        session,
        "tag",
        tag_id,
    )


def _tag_conflict(name: str) -> ConflictError:
    return ConflictError(f"tag name {name!r} already exists", details={"name": name})


async def create_tag(
    session: AsyncSession, payload: TagCreateRequest, *, tenant_id: uuid.UUID
) -> TagResponse:
    if await _tag_name_taken(session, tenant_id=tenant_id, name=payload.name):
        raise _tag_conflict(payload.name)
    row = CustomerTag(tenant_id=tenant_id, name=payload.name, color=payload.color)
    session.add(row)
    await session.flush()
    await session.refresh(row)

    await audit(
        session,
        action="crm.tag.created",
        tenant_id=tenant_id,
        target=("customer_tags", row.id),
        after={"name": row.name, "color": row.color},
    )
    logger.info("crm.tag.created tag_id=%s name=%s", row.id, row.name)
    return TagResponse.model_validate(row)


async def list_tags(
    session: AsyncSession, *, tenant_id: uuid.UUID, limit: int = 500
) -> list[TagResponse]:
    rows = (
        await session.execute(
            select(CustomerTag)
            .where(CustomerTag.tenant_id == tenant_id, CustomerTag.deleted_at.is_(None))
            .order_by(CustomerTag.name, CustomerTag.id)
            .limit(limit)
        )
    ).scalars().all()
    return [TagResponse.model_validate(r) for r in rows]


async def patch_tag(
    session: AsyncSession,
    tag_id: uuid.UUID,
    payload: TagPatchRequest,
    *,
    tenant_id: uuid.UUID,
) -> TagResponse:
    row = await _load_tag(session, tag_id, tenant_id=tenant_id)
    changes = {
        k: v for k, v in payload.model_dump(exclude_unset=True).items() if getattr(row, k) != v
    }
    if not changes:
        return TagResponse.model_validate(row)
    new_name = changes.get("name")
    if new_name is not None and await _tag_name_taken(
        session, tenant_id=tenant_id, name=new_name, exclude_id=row.id
    ):
        raise _tag_conflict(new_name)

    before = {k: getattr(row, k) for k in changes}
    for k, v in changes.items():
        setattr(row, k, v)
    await session.flush()
    await session.refresh(row)

    await audit(
        session,
        action="crm.tag.updated",
        tenant_id=tenant_id,
        target=("customer_tags", row.id),
        before=before,
        after=changes,
    )
    logger.info("crm.tag.updated tag_id=%s fields=%s", row.id, sorted(changes))
    return TagResponse.model_validate(row)


async def delete_tag(
    session: AsyncSession, tag_id: uuid.UUID, *, tenant_id: uuid.UUID
) -> None:
    """Soft delete — frees the name. Existing links stay in the table but
    are hidden (customer tag lists only show live tags)."""
    row = await _load_tag(session, tag_id, tenant_id=tenant_id)
    deleted_at = datetime.now(UTC)
    row.deleted_at = deleted_at
    await session.flush()

    await audit(
        session,
        action="crm.tag.deleted",
        tenant_id=tenant_id,
        target=("customer_tags", row.id),
        before={"name": row.name},
        after={"deleted_at": deleted_at.isoformat()},
    )
    logger.info("crm.tag.deleted tag_id=%s name=%s", row.id, row.name)


def _link_response(link: CustomerTagLink, tag: CustomerTag) -> CustomerTagResponse:
    return CustomerTagResponse(
        link_id=link.id,
        customer_id=link.customer_id,
        tag=TagResponse.model_validate(tag),
        attached_at=link.created_at,
    )


async def attach_tag(
    session: AsyncSession,
    customer_id: uuid.UUID,
    payload: TagAttachRequest,
    *,
    tenant_id: uuid.UUID,
) -> CustomerTagResponse:
    customer = await load_customer(session, customer_id, tenant_id=tenant_id)
    tag = await session.get(CustomerTag, payload.tag_id)
    if tag is None or tag.deleted_at is not None:
        raise NotFoundError(
            f"tag {payload.tag_id} not found", details={"tag_id": str(payload.tag_id)}
        )
    if tag.tenant_id != customer.tenant_id:
        raise ValidationError(
            "tag and customer belong to different tenants",
            details={"tag_id": str(tag.id), "customer_id": str(customer_id)},
        )
    existing = (
        await session.execute(
            select(CustomerTagLink.id).where(
                CustomerTagLink.customer_id == customer_id,
                CustomerTagLink.tag_id == tag.id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise ConflictError(
            "tag already attached to this customer",
            details={"tag_id": str(tag.id), "customer_id": str(customer_id)},
        )

    link = CustomerTagLink(tenant_id=tenant_id, customer_id=customer_id, tag_id=tag.id)
    session.add(link)
    await session.flush()
    await session.refresh(link)

    await audit(
        session,
        action="crm.tag.attached",
        tenant_id=tenant_id,
        target=("customer_tag_links", link.id),
        after={"customer_id": str(customer_id), "tag_id": str(tag.id), "tag_name": tag.name},
    )
    logger.info("crm.tag.attached customer_id=%s tag_id=%s", customer_id, tag.id)
    return _link_response(link, tag)


async def detach_tag(
    session: AsyncSession,
    customer_id: uuid.UUID,
    tag_id: uuid.UUID,
    *,
    tenant_id: uuid.UUID,
) -> None:
    """Hard-delete the link row (links carry no soft-delete column; the
    audit row preserves the history)."""
    await load_customer(session, customer_id, tenant_id=tenant_id)
    link = (
        await session.execute(
            select(CustomerTagLink).where(
                CustomerTagLink.customer_id == customer_id,
                CustomerTagLink.tag_id == tag_id,
                CustomerTagLink.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if link is None:
        raise NotFoundError(
            "tag is not attached to this customer",
            details={"tag_id": str(tag_id), "customer_id": str(customer_id)},
        )
    link_id = link.id
    await session.delete(link)
    await session.flush()

    await audit(
        session,
        action="crm.tag.detached",
        tenant_id=tenant_id,
        target=("customer_tag_links", link_id),
        before={"customer_id": str(customer_id), "tag_id": str(tag_id)},
    )
    logger.info("crm.tag.detached customer_id=%s tag_id=%s", customer_id, tag_id)


async def list_customer_tags(
    session: AsyncSession, customer_id: uuid.UUID, *, tenant_id: uuid.UUID
) -> list[CustomerTagResponse]:
    await load_customer(session, customer_id, tenant_id=tenant_id)
    rows = (
        await session.execute(
            select(CustomerTagLink, CustomerTag)
            .join(CustomerTag, CustomerTag.id == CustomerTagLink.tag_id)
            .where(
                CustomerTagLink.customer_id == customer_id,
                CustomerTagLink.tenant_id == tenant_id,
                CustomerTag.deleted_at.is_(None),
            )
            .order_by(CustomerTag.name, CustomerTag.id)
        )
    ).all()
    return [_link_response(link, tag) for link, tag in rows]


# ──────────────────────────────────────────────────────────────────────────
# Interactions
# ──────────────────────────────────────────────────────────────────────────


async def create_interaction(
    session: AsyncSession,
    customer_id: uuid.UUID,
    payload: InteractionCreateRequest,
    *,
    tenant_id: uuid.UUID,
) -> InteractionResponse:
    await load_customer(session, customer_id, tenant_id=tenant_id)
    await _require_employee(
        session, payload.employee_id, tenant_id=tenant_id, field="employee_id"
    )
    occurred_at = (payload.occurred_at or datetime.now(UTC)).astimezone(UTC)
    row = CustomerInteraction(
        tenant_id=tenant_id,
        customer_id=customer_id,
        channel=payload.channel,
        direction=payload.direction,
        summary=payload.summary,
        employee_id=payload.employee_id,
        occurred_at=occurred_at,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)

    await audit(
        session,
        action="crm.interaction.created",
        tenant_id=tenant_id,
        actor_id=payload.employee_id,
        target=("customer_interactions", row.id),
        after={
            "customer_id": str(customer_id),
            "channel": row.channel.value,
            "direction": row.direction.value,
            "occurred_at": occurred_at.isoformat(),
        },
    )
    logger.info(
        "crm.interaction.created interaction_id=%s customer_id=%s channel=%s",
        row.id,
        customer_id,
        row.channel.value,
    )
    return InteractionResponse.model_validate(row)


async def list_interactions(
    session: AsyncSession,
    customer_id: uuid.UUID,
    *,
    tenant_id: uuid.UUID,
    limit: int = 200,
) -> list[InteractionResponse]:
    await load_customer(session, customer_id, tenant_id=tenant_id)
    rows = (
        await session.execute(
            select(CustomerInteraction)
            .where(
                CustomerInteraction.customer_id == customer_id,
                CustomerInteraction.tenant_id == tenant_id,
            )
            .order_by(CustomerInteraction.occurred_at.desc(), CustomerInteraction.id.desc())
            .limit(limit)
        )
    ).scalars().all()
    return [InteractionResponse.model_validate(r) for r in rows]


__all__ = [
    "attach_tag",
    "create_interaction",
    "create_note",
    "create_tag",
    "create_task",
    "delete_note",
    "delete_tag",
    "delete_task",
    "detach_tag",
    "list_customer_tags",
    "list_customer_tasks",
    "list_interactions",
    "list_notes",
    "list_tags",
    "list_tasks",
    "load_customer",
    "patch_note",
    "patch_tag",
    "patch_task",
]
