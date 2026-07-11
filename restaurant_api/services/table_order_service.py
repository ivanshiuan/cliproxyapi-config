"""Customer table-QR ordering (P2) — the guest's phone talks to these.

Security model: the opaque ``qr_token`` on the table card IS the credential.
It's unguessable (``secrets.token_urlsafe``), rotatable per table, and scopes
every operation to exactly one table's live seating. Tenant comes from the
table row itself — a public endpoint can't trust headers.

Guardrails:
  - Guests can browse the menu, append items to their own table's bill, and see
    that bill. Nothing else — no void, no discount, no checkout, no other table.
  - Ordering requires the table to have an *open session* (staff seats the party
    first, iCHEF-style). A photographed QR can't open tables at 3am.
  - Prices are server-snapshotted per line via ``pos_order_service.add_line`` —
    the same code path the staff POS uses, so BOM deduction, KDS routing, audit,
    and the real-time floor event all behave identically. Orders a guest opens
    are stamped ``channel=TABLE_QR`` so reports can split self-serve revenue.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.errors import ConflictError, NotFoundError
from ..models import DiningTable, MenuItem, OrderChannel
from ..schemas.pos_orders import LineAddRequest
from ..schemas.table_order import (
    CartSubmit,
    GuestBill,
    GuestLine,
    PublicMenuItem,
    TableContext,
)
from . import pos_order_service
from .table_service import _open_session_for_table


async def _resolve_table(session: AsyncSession, qr_token: str) -> DiningTable:
    row = (
        await session.execute(
            select(DiningTable).where(
                DiningTable.qr_token == qr_token,
                DiningTable.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        # Same 404 for unknown and rotated tokens — no oracle.
        raise NotFoundError(message="table not found", details={})
    return row


async def get_context(session: AsyncSession, qr_token: str) -> TableContext:
    table = await _resolve_table(session, qr_token)
    open_session = await _open_session_for_table(session, table.id)
    items = (
        await session.execute(
            select(MenuItem)
            .where(
                MenuItem.tenant_id == table.tenant_id,
                MenuItem.store_id == table.store_id,
                MenuItem.deleted_at.is_(None),
                MenuItem.is_available.is_(True),
            )
            .order_by(MenuItem.name)
        )
    ).scalars().all()
    return TableContext(
        table_name=table.name,
        zone=table.zone,
        store_id=table.store_id,
        is_open=open_session is not None,
        menu=[PublicMenuItem.model_validate(m) for m in items],
    )


async def submit_cart(
    session: AsyncSession, qr_token: str, payload: CartSubmit
) -> GuestBill:
    table = await _resolve_table(session, qr_token)
    open_session = await _open_session_for_table(session, table.id)
    if open_session is None:
        raise ConflictError(
            message="這桌還沒開桌 — 請招呼服務人員為您開桌後再點餐",
            details={"table": table.name},
        )
    # Reuse the staff add-line path per item: server price snapshot, BOM deduct,
    # audit, and the live floor event that makes the order pop on the POS.
    for item in payload.items:
        await pos_order_service.add_line(
            session,
            open_session.id,
            LineAddRequest(menu_item_id=item.menu_item_id, qty=item.qty, notes=item.notes),
            tenant_id=table.tenant_id,
            channel=OrderChannel.TABLE_QR,
        )
    return await get_bill(session, qr_token)


async def get_bill(session: AsyncSession, qr_token: str) -> GuestBill:
    table = await _resolve_table(session, qr_token)
    open_session = await _open_session_for_table(session, table.id)
    if open_session is None:
        # A closed table simply has no bill to show.
        return GuestBill(table_name=table.name, lines=[], total=Decimal("0"))
    order = await pos_order_service.get_session_order(
        session, open_session.id, tenant_id=table.tenant_id
    )
    # Map internal line shape → guest-visible lines with item names.
    ids = [ln.menu_item_id for ln in order.lines]
    names: dict = {}
    if ids:
        names = {
            row.id: str(row.name)
            for row in (
                await session.execute(
                    select(MenuItem.id, MenuItem.name).where(MenuItem.id.in_(ids))
                )
            ).all()
        }
    lines = [
        GuestLine(
            name=names.get(ln.menu_item_id),
            qty=ln.qty,
            line_total=ln.line_total,
        )
        for ln in order.lines
    ]
    total = sum((ln.line_total for ln in order.lines), Decimal("0"))
    return GuestBill(table_name=table.name, lines=lines, total=total)


__all__ = ["get_bill", "get_context", "submit_cart"]
