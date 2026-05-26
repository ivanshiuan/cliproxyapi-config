"""End-to-end DB smoke: insert a tenant + store + employee, read them back.

Requires a live Postgres reachable per `.env` (default `localhost:5432/resto_dev`)
with the initial Alembic migration applied (`alembic upgrade head`).

Usage:
    python scripts/smoke_db.py

Exits 0 on success, 1 on failure. Safe to re-run — uses tenant slug as
idempotency key and only inserts what's missing.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import date
from decimal import Decimal

from sqlalchemy import select

from restaurant_api.database import dispose_engine, get_sessionmaker
from restaurant_api.models import Employee, EmployeeRole, Store, Tenant


_SLUG = "smoke-test-tenant"


async def main() -> int:
    SessionLocal = get_sessionmaker()

    async with SessionLocal() as session:
        # Idempotent tenant
        existing = (
            await session.execute(select(Tenant).where(Tenant.slug == _SLUG))
        ).scalar_one_or_none()

        if existing is None:
            tenant = Tenant(name="Smoke Test Tenant", slug=_SLUG)
            session.add(tenant)
            await session.flush()
            print(f"  + tenant: {tenant.id}")

            store = Store(
                tenant_id=tenant.id,
                name="Smoke Test Store",
                address="Taipei",
                phone="02-0000-0000",
                opened_on=date(2026, 1, 1),
                is_active=True,
            )
            session.add(store)
            await session.flush()
            print(f"  + store:  {store.id}")

            emp = Employee(
                tenant_id=tenant.id,
                store_id=store.id,
                full_name="Smoke Tester",
                role=EmployeeRole.STAFF,
                hourly_wage=Decimal("200.00"),
                hired_on=date(2026, 5, 26),
                is_active=True,
            )
            session.add(emp)
            await session.flush()
            print(f"  + employee: {emp.id}")

            await session.commit()
        else:
            print(f"  · tenant already exists: {existing.id} (idempotent)")

        # Read-back assertions
        tenant = (
            await session.execute(select(Tenant).where(Tenant.slug == _SLUG))
        ).scalar_one()
        store_count = len(
            (
                await session.execute(select(Store).where(Store.tenant_id == tenant.id))
            ).scalars().all()
        )
        emp_count = len(
            (
                await session.execute(select(Employee).where(Employee.tenant_id == tenant.id))
            ).scalars().all()
        )

        print()
        print(f"  tenant.name          = {tenant.name!r}")
        print(f"  stores under tenant  = {store_count}")
        print(f"  employees under tenant = {emp_count}")

        ok = store_count >= 1 and emp_count >= 1
        if not ok:
            print("FAIL: expected ≥1 store and ≥1 employee", file=sys.stderr)
            return 1

    await dispose_engine()
    print()
    print("✅ DB smoke passed: insert + select + FK relationships work end-to-end")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except Exception as exc:
        print(f"❌ DB smoke failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
