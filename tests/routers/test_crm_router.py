"""Integration tests for the ``/crm`` router (CRM 關係經營層).

Covers notes / tasks / tags / interactions CRUD, the cross-customer task
list, tenant guards on employees + tags, audit trail, and the unified
customer timeline (type coverage, ordering, Decimal amounts, cursor paging).

All reads are scoped to the seeded tenant / customer — no whole-table scans.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from itertools import pairwise
from typing import Any

import httpx
import pytest
import pytest_asyncio  # type: ignore[import-not-found]
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from restaurant_api.api.deps import get_current_tenant_id, get_db
from restaurant_api.main import app
from restaurant_api.models import (
    AuditLog,
    Customer,
    CustomerInteraction,
    CustomerNote,
    CustomerPointsLedger,
    CustomerStoredValueLedger,
    CustomerTag,
    CustomerTask,
    Employee,
    EmployeeRole,
    InteractionChannel,
    InteractionDirection,
    MenuItem,
    Order,
    OrderLine,
    Referral,
    ReferralStatus,
    Store,
    Tenant,
)
from restaurant_api.routers import crm as crm_router

pytestmark = pytest.mark.asyncio

T0 = datetime(2026, 11, 20, 2, 0, tzinfo=UTC)  # 10:00 Asia/Taipei


@pytest_asyncio.fixture
async def client(
    db_session: AsyncSession, seed_tenant: Tenant
) -> AsyncIterator[httpx.AsyncClient]:
    crm_router._mount()

    async def _override_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    def _override_tenant() -> uuid.UUID:
        return seed_tenant.id

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_tenant_id] = _override_tenant
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def customer(db_session: AsyncSession, seed_tenant: Tenant) -> Customer:
    c = Customer(tenant_id=seed_tenant.id, display_name="王大明")
    db_session.add(c)
    await db_session.flush()
    return c


@pytest_asyncio.fixture
async def other_tenant(db_session: AsyncSession) -> Tenant:
    t = Tenant(name="Other Tenant", slug=f"other-{uuid.uuid4().hex[:8]}")
    db_session.add(t)
    await db_session.flush()
    return t


async def _post(client: httpx.AsyncClient, url: str, body: dict[str, Any]) -> dict[str, Any]:
    resp = await client.post(url, json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ──────────────────────────────────────────────────────────────────────────
# Notes
# ──────────────────────────────────────────────────────────────────────────


async def test_create_note_returns_201_in_taipei_time(
    client: httpx.AsyncClient, customer: Customer, seed_employee: Employee
) -> None:
    body = await _post(
        client,
        f"/crm/customers/{customer.id}/notes",
        {"body": "對花生過敏", "author_employee_id": str(seed_employee.id)},
    )
    assert body["body"] == "對花生過敏"
    assert body["author_employee_id"] == str(seed_employee.id)
    assert body["created_at"].endswith("+08:00")


async def test_note_empty_body_returns_422(
    client: httpx.AsyncClient, customer: Customer
) -> None:
    resp = await client.post(f"/crm/customers/{customer.id}/notes", json={"body": "   "})
    assert resp.status_code == 422


async def test_note_patch_updates_body(
    client: httpx.AsyncClient, customer: Customer
) -> None:
    note = await _post(client, f"/crm/customers/{customer.id}/notes", {"body": "v1"})
    resp = await client.patch(f"/crm/notes/{note['id']}", json={"body": "v2"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["body"] == "v2"


async def test_soft_deleted_note_disappears_from_list(
    client: httpx.AsyncClient, customer: Customer
) -> None:
    keep = await _post(client, f"/crm/customers/{customer.id}/notes", {"body": "keep"})
    gone = await _post(client, f"/crm/customers/{customer.id}/notes", {"body": "gone"})
    resp = await client.delete(f"/crm/notes/{gone['id']}")
    assert resp.status_code == 204
    listed = (await client.get(f"/crm/customers/{customer.id}/notes")).json()
    assert [n["id"] for n in listed] == [keep["id"]]
    # Deleted note can no longer be patched.
    again = await client.patch(f"/crm/notes/{gone['id']}", json={"body": "x"})
    assert again.status_code == 404


async def test_notes_list_newest_first(
    client: httpx.AsyncClient, customer: Customer
) -> None:
    first = await _post(client, f"/crm/customers/{customer.id}/notes", {"body": "1"})
    second = await _post(client, f"/crm/customers/{customer.id}/notes", {"body": "2"})
    listed = (await client.get(f"/crm/customers/{customer.id}/notes")).json()
    assert [n["id"] for n in listed] == [second["id"], first["id"]]


async def test_note_on_unknown_customer_returns_404(client: httpx.AsyncClient) -> None:
    resp = await client.post(f"/crm/customers/{uuid.uuid4()}/notes", json={"body": "hi"})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


# ──────────────────────────────────────────────────────────────────────────
# Tasks
# ──────────────────────────────────────────────────────────────────────────


async def test_create_task_defaults_open_with_assignee(
    client: httpx.AsyncClient, customer: Customer, seed_employee: Employee
) -> None:
    body = await _post(
        client,
        f"/crm/customers/{customer.id}/tasks",
        {
            "title": "回電關心客訴",
            "assignee_employee_id": str(seed_employee.id),
            "due_at": "2026-11-26T10:00:00+08:00",
        },
    )
    assert body["status"] == "open"
    assert body["completed_at"] is None
    assert body["assignee_employee_id"] == str(seed_employee.id)
    assert body["due_at"] == "2026-11-26T10:00:00+08:00"


async def test_task_naive_due_at_returns_422(
    client: httpx.AsyncClient, customer: Customer
) -> None:
    resp = await client.post(
        f"/crm/customers/{customer.id}/tasks",
        json={"title": "t", "due_at": "2026-11-26T10:00:00"},
    )
    assert resp.status_code == 422


async def test_task_unknown_assignee_returns_404(
    client: httpx.AsyncClient, customer: Customer
) -> None:
    resp = await client.post(
        f"/crm/customers/{customer.id}/tasks",
        json={"title": "t", "assignee_employee_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 404


async def test_task_cross_tenant_assignee_returns_422(
    client: httpx.AsyncClient,
    customer: Customer,
    db_session: AsyncSession,
    other_tenant: Tenant,
) -> None:
    emp = Employee(
        tenant_id=other_tenant.id,
        full_name="Outsider",
        role=EmployeeRole.STAFF,
        hourly_wage=Decimal("200.00"),
        hired_on=date(2026, 1, 1),
        is_active=True,
    )
    db_session.add(emp)
    await db_session.flush()
    resp = await client.post(
        f"/crm/customers/{customer.id}/tasks",
        json={"title": "t", "assignee_employee_id": str(emp.id)},
    )
    assert resp.status_code == 422


async def test_task_done_sets_completed_at_and_reopen_clears(
    client: httpx.AsyncClient, customer: Customer
) -> None:
    task = await _post(client, f"/crm/customers/{customer.id}/tasks", {"title": "生日禮待取"})
    done = await client.patch(f"/crm/tasks/{task['id']}", json={"status": "done"})
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "done"
    assert done.json()["completed_at"] is not None

    reopened = await client.patch(f"/crm/tasks/{task['id']}", json={"status": "open"})
    assert reopened.json()["status"] == "open"
    assert reopened.json()["completed_at"] is None


async def test_task_cancelled_has_no_completed_at(
    client: httpx.AsyncClient, customer: Customer
) -> None:
    task = await _post(client, f"/crm/customers/{customer.id}/tasks", {"title": "x"})
    resp = await client.patch(f"/crm/tasks/{task['id']}", json={"status": "cancelled"})
    assert resp.json()["status"] == "cancelled"
    assert resp.json()["completed_at"] is None


async def test_task_patch_null_title_returns_422(
    client: httpx.AsyncClient, customer: Customer
) -> None:
    task = await _post(client, f"/crm/customers/{customer.id}/tasks", {"title": "x"})
    resp = await client.patch(f"/crm/tasks/{task['id']}", json={"title": None})
    assert resp.status_code == 422


async def test_customer_tasks_filter_by_status(
    client: httpx.AsyncClient, customer: Customer
) -> None:
    a = await _post(client, f"/crm/customers/{customer.id}/tasks", {"title": "a"})
    b = await _post(client, f"/crm/customers/{customer.id}/tasks", {"title": "b"})
    await client.patch(f"/crm/tasks/{b['id']}", json={"status": "done"})
    resp = await client.get(f"/crm/customers/{customer.id}/tasks", params={"status": "open"})
    assert [t["id"] for t in resp.json()] == [a["id"]]


async def test_cross_customer_task_list_filters_and_orders_by_due(
    client: httpx.AsyncClient,
    customer: Customer,
    seed_employee: Employee,
    db_session: AsyncSession,
    seed_tenant: Tenant,
) -> None:
    other = Customer(tenant_id=seed_tenant.id, display_name="李小華")
    db_session.add(other)
    await db_session.flush()
    emp = str(seed_employee.id)

    late = await _post(
        client,
        f"/crm/customers/{customer.id}/tasks",
        {"title": "late", "assignee_employee_id": emp, "due_at": "2026-12-05T09:00:00+08:00"},
    )
    no_due = await _post(
        client, f"/crm/customers/{other.id}/tasks", {"title": "nodue", "assignee_employee_id": emp}
    )
    soon = await _post(
        client,
        f"/crm/customers/{other.id}/tasks",
        {"title": "soon", "assignee_employee_id": emp, "due_at": "2026-11-27T09:00:00+08:00"},
    )
    done = await _post(
        client,
        f"/crm/customers/{customer.id}/tasks",
        {"title": "done", "assignee_employee_id": emp, "due_at": "2026-11-26T09:00:00+08:00"},
    )
    await client.patch(f"/crm/tasks/{done['id']}", json={"status": "done"})
    # Unassigned task must not show in the assignee filter.
    await _post(client, f"/crm/customers/{customer.id}/tasks", {"title": "unassigned"})

    resp = await client.get(
        "/crm/tasks", params={"assignee_employee_id": emp, "status": "open"}
    )
    assert resp.status_code == 200, resp.text
    assert [t["id"] for t in resp.json()] == [soon["id"], late["id"], no_due["id"]]


async def test_cross_customer_task_list_due_before(
    client: httpx.AsyncClient, customer: Customer, seed_employee: Employee
) -> None:
    emp = str(seed_employee.id)
    early = await _post(
        client,
        f"/crm/customers/{customer.id}/tasks",
        {"title": "e", "assignee_employee_id": emp, "due_at": "2026-11-27T09:00:00+08:00"},
    )
    await _post(
        client,
        f"/crm/customers/{customer.id}/tasks",
        {"title": "l", "assignee_employee_id": emp, "due_at": "2026-12-27T09:00:00+08:00"},
    )
    resp = await client.get(
        "/crm/tasks",
        params={"assignee_employee_id": emp, "due_before": "2026-12-01T00:00:00+08:00"},
    )
    assert [t["id"] for t in resp.json()] == [early["id"]]


async def test_soft_deleted_task_hidden(
    client: httpx.AsyncClient, customer: Customer
) -> None:
    task = await _post(client, f"/crm/customers/{customer.id}/tasks", {"title": "x"})
    assert (await client.delete(f"/crm/tasks/{task['id']}")).status_code == 204
    listed = (await client.get(f"/crm/customers/{customer.id}/tasks")).json()
    assert listed == []


# ──────────────────────────────────────────────────────────────────────────
# Tags
# ──────────────────────────────────────────────────────────────────────────


async def test_create_tag_strips_name(client: httpx.AsyncClient) -> None:
    body = await _post(client, "/crm/tags", {"name": "  VIP  ", "color": "#E53E3E"})
    assert body["name"] == "VIP"
    assert body["color"] == "#E53E3E"


async def test_duplicate_live_tag_name_returns_409(client: httpx.AsyncClient) -> None:
    await _post(client, "/crm/tags", {"name": "素食"})
    resp = await client.post("/crm/tags", json={"name": "素食"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "CONFLICT"


async def test_soft_deleted_tag_name_can_be_recreated(client: httpx.AsyncClient) -> None:
    tag = await _post(client, "/crm/tags", {"name": "生日月"})
    assert (await client.delete(f"/crm/tags/{tag['id']}")).status_code == 204
    again = await client.post("/crm/tags", json={"name": "生日月"})
    assert again.status_code == 201
    assert again.json()["id"] != tag["id"]
    names = [t["name"] for t in (await client.get("/crm/tags")).json()]
    assert names == ["生日月"]


async def test_patch_tag_rename_collision_returns_409(client: httpx.AsyncClient) -> None:
    await _post(client, "/crm/tags", {"name": "A"})
    b = await _post(client, "/crm/tags", {"name": "B"})
    resp = await client.patch(f"/crm/tags/{b['id']}", json={"name": "A"})
    assert resp.status_code == 409


async def test_attach_and_list_customer_tags(
    client: httpx.AsyncClient, customer: Customer
) -> None:
    tag = await _post(client, "/crm/tags", {"name": "VIP"})
    link = await _post(client, f"/crm/customers/{customer.id}/tags", {"tag_id": tag["id"]})
    assert link["tag"]["id"] == tag["id"]
    listed = (await client.get(f"/crm/customers/{customer.id}/tags")).json()
    assert [t["tag"]["name"] for t in listed] == ["VIP"]


async def test_attach_same_tag_twice_returns_409(
    client: httpx.AsyncClient, customer: Customer
) -> None:
    tag = await _post(client, "/crm/tags", {"name": "VIP"})
    await _post(client, f"/crm/customers/{customer.id}/tags", {"tag_id": tag["id"]})
    resp = await client.post(f"/crm/customers/{customer.id}/tags", json={"tag_id": tag["id"]})
    assert resp.status_code == 409


async def test_attach_cross_tenant_tag_returns_422(
    client: httpx.AsyncClient,
    customer: Customer,
    db_session: AsyncSession,
    other_tenant: Tenant,
) -> None:
    foreign = CustomerTag(tenant_id=other_tenant.id, name="外部")
    db_session.add(foreign)
    await db_session.flush()
    resp = await client.post(
        f"/crm/customers/{customer.id}/tags", json={"tag_id": str(foreign.id)}
    )
    assert resp.status_code == 422


async def test_detach_tag_then_detach_again_404(
    client: httpx.AsyncClient, customer: Customer
) -> None:
    tag = await _post(client, "/crm/tags", {"name": "VIP"})
    await _post(client, f"/crm/customers/{customer.id}/tags", {"tag_id": tag["id"]})
    first = await client.delete(f"/crm/customers/{customer.id}/tags/{tag['id']}")
    assert first.status_code == 204
    assert (await client.get(f"/crm/customers/{customer.id}/tags")).json() == []
    second = await client.delete(f"/crm/customers/{customer.id}/tags/{tag['id']}")
    assert second.status_code == 404


# ──────────────────────────────────────────────────────────────────────────
# Interactions
# ──────────────────────────────────────────────────────────────────────────


async def test_create_interaction_defaults_occurred_at(
    client: httpx.AsyncClient, customer: Customer, seed_employee: Employee
) -> None:
    body = await _post(
        client,
        f"/crm/customers/{customer.id}/interactions",
        {
            "channel": "line",
            "direction": "inbound",
            "summary": "LINE 詢問訂位",
            "employee_id": str(seed_employee.id),
        },
    )
    assert body["channel"] == "line"
    assert body["direction"] == "inbound"
    assert body["occurred_at"].endswith("+08:00")


async def test_interactions_listed_by_occurred_at_desc(
    client: httpx.AsyncClient, customer: Customer
) -> None:
    older = await _post(
        client,
        f"/crm/customers/{customer.id}/interactions",
        {
            "channel": "phone",
            "direction": "outbound",
            "summary": "回訪",
            "occurred_at": "2026-11-20T10:00:00+08:00",
        },
    )
    newer = await _post(
        client,
        f"/crm/customers/{customer.id}/interactions",
        {
            "channel": "in_person",
            "direction": "inbound",
            "summary": "臨櫃",
            "occurred_at": "2026-11-22T10:00:00+08:00",
        },
    )
    listed = (await client.get(f"/crm/customers/{customer.id}/interactions")).json()
    assert [i["id"] for i in listed] == [newer["id"], older["id"]]


async def test_interaction_invalid_channel_returns_422(
    client: httpx.AsyncClient, customer: Customer
) -> None:
    resp = await client.post(
        f"/crm/customers/{customer.id}/interactions",
        json={"channel": "fax", "direction": "inbound", "summary": "x"},
    )
    assert resp.status_code == 422


# ──────────────────────────────────────────────────────────────────────────
# Audit
# ──────────────────────────────────────────────────────────────────────────


async def test_writes_leave_crm_audit_rows(
    client: httpx.AsyncClient,
    customer: Customer,
    db_session: AsyncSession,
    seed_tenant: Tenant,
) -> None:
    note = await _post(client, f"/crm/customers/{customer.id}/notes", {"body": "hi"})
    await client.delete(f"/crm/notes/{note['id']}")
    rows = (
        await db_session.execute(
            select(AuditLog.action).where(
                AuditLog.tenant_id == seed_tenant.id,
                AuditLog.target_id == uuid.UUID(note["id"]),
            )
        )
    ).scalars().all()
    assert set(rows) == {"crm.note.created", "crm.note.deleted"}


# ──────────────────────────────────────────────────────────────────────────
# Timeline
# ──────────────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def timeline_seed(
    db_session: AsyncSession,
    seed_tenant: Tenant,
    seed_store: Store,
    seed_menu_item: MenuItem,
    customer: Customer,
) -> dict[str, Any]:
    """One row per source, each 1h apart: order(T0+1h) … interaction(T0+6h)."""
    tid = seed_tenant.id
    order = Order(
        tenant_id=tid,
        store_id=seed_store.id,
        customer_id=customer.id,
        order_no=f"T{uuid.uuid4().hex[:10]}",
        business_date=date(2026, 11, 20),
        opened_at=T0 + timedelta(hours=1),
    )
    db_session.add(order)
    await db_session.flush()
    db_session.add(
        OrderLine(
            tenant_id=tid,
            order_id=order.id,
            menu_item_id=seed_menu_item.id,
            qty=Decimal("2"),
            unit_price=Decimal("250.00"),
            line_total=Decimal("500.00"),
        )
    )
    db_session.add_all(
        [
            CustomerPointsLedger(
                tenant_id=tid,
                customer_id=customer.id,
                delta=Decimal("50"),
                reason="order.earn",
                created_at=T0 + timedelta(hours=2),
            ),
            CustomerStoredValueLedger(
                tenant_id=tid,
                customer_id=customer.id,
                delta=Decimal("1000"),
                reason="topup",
                created_at=T0 + timedelta(hours=3),
            ),
            CustomerNote(
                tenant_id=tid,
                customer_id=customer.id,
                body="喜歡靠窗",
                created_at=T0 + timedelta(hours=4),
            ),
            CustomerTask(
                tenant_id=tid,
                customer_id=customer.id,
                title="寄生日券",
                due_at=T0 + timedelta(days=3),
                created_at=T0 + timedelta(hours=5),
            ),
            CustomerInteraction(
                tenant_id=tid,
                customer_id=customer.id,
                channel=InteractionChannel.PHONE,
                direction=InteractionDirection.OUTBOUND,
                summary="電話回訪",
                occurred_at=T0 + timedelta(hours=6),
            ),
        ]
    )
    await db_session.flush()
    return {"order_id": str(order.id)}


async def test_timeline_includes_all_source_types_desc(
    client: httpx.AsyncClient, customer: Customer, timeline_seed: dict[str, Any]
) -> None:
    resp = await client.get(f"/crm/customers/{customer.id}/timeline")
    assert resp.status_code == 200, resp.text
    events = resp.json()["events"]
    assert [e["type"] for e in events] == [
        "interaction",
        "task",
        "note",
        "stored_value",
        "points",
        "order",
    ]
    times = [datetime.fromisoformat(e["occurred_at"]) for e in events]
    assert all(a > b for a, b in pairwise(times))
    assert all(e["occurred_at"].endswith("+08:00") for e in events)
    assert resp.json()["next_before"] is None


async def test_timeline_order_amount_is_line_total_sum(
    client: httpx.AsyncClient, customer: Customer, timeline_seed: dict[str, Any]
) -> None:
    events = (await client.get(f"/crm/customers/{customer.id}/timeline")).json()["events"]
    order = next(e for e in events if e["type"] == "order")
    assert order["ref_id"] == timeline_seed["order_id"]
    assert Decimal(order["amount"]) == Decimal("500.00")
    points = next(e for e in events if e["type"] == "points")
    assert Decimal(points["amount"]) == Decimal("50")
    assert points["detail"] == "order.earn"


async def test_timeline_before_cursor_paginates(
    client: httpx.AsyncClient, customer: Customer, timeline_seed: dict[str, Any]
) -> None:
    url = f"/crm/customers/{customer.id}/timeline"
    page1 = (await client.get(url, params={"limit": 3})).json()
    assert [e["type"] for e in page1["events"]] == ["interaction", "task", "note"]
    assert page1["next_before"] == page1["events"][-1]["occurred_at"]

    page2 = (await client.get(url, params={"limit": 3, "before": page1["next_before"]})).json()
    assert [e["type"] for e in page2["events"]] == ["stored_value", "points", "order"]
    assert page2["next_before"] is not None

    page3 = (await client.get(url, params={"limit": 3, "before": page2["next_before"]})).json()
    assert page3 == {"events": [], "next_before": None}


async def test_timeline_referral_emits_created_and_qualified_events(
    client: httpx.AsyncClient,
    customer: Customer,
    db_session: AsyncSession,
    seed_tenant: Tenant,
) -> None:
    friend = Customer(tenant_id=seed_tenant.id, display_name="被推薦人")
    db_session.add(friend)
    await db_session.flush()
    db_session.add(
        Referral(
            tenant_id=seed_tenant.id,
            referrer_id=customer.id,
            referred_id=friend.id,
            status=ReferralStatus.QUALIFIED,
            referrer_reward_points=Decimal("100"),
            created_at=T0,
            qualified_at=T0 + timedelta(days=1),
        )
    )
    await db_session.flush()
    events = (await client.get(f"/crm/customers/{customer.id}/timeline")).json()["events"]
    refs = [e for e in events if e["type"] == "referral"]
    assert [e["title"] for e in refs] == ["推薦達標", "推薦了新會員"]
    assert Decimal(refs[0]["amount"]) == Decimal("100")


async def test_timeline_unknown_customer_returns_404(client: httpx.AsyncClient) -> None:
    resp = await client.get(f"/crm/customers/{uuid.uuid4()}/timeline")
    assert resp.status_code == 404
