"""Store listing (後台管理 門市選單).

The admin console (`/admin-ui`) needs to enumerate the tenant's stores to drive
its 門市 picker. This is a thin read-only listing scoped to the caller's tenant;
all mutating store management stays out of Phase-1 scope.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from ..api.deps import DbSession, TenantId
from ..models import Store

router = APIRouter(prefix="/stores", tags=["stores"])


class StoreListItem(BaseModel):
    """One store row for the admin 門市 picker."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    is_active: bool


@router.get("", response_model=list[StoreListItem], summary="列出門市 (後台選單用)")
async def list_stores(session: DbSession, tenant_id: TenantId) -> list[StoreListItem]:
    rows = (
        await session.execute(
            select(Store)
            .where(Store.tenant_id == tenant_id, Store.deleted_at.is_(None))
            .order_by(Store.is_active.desc(), Store.name)
        )
    ).scalars().all()
    return [
        StoreListItem(id=str(s.id), name=s.name, is_active=s.is_active) for s in rows
    ]
