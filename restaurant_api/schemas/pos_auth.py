"""Pydantic v2 schemas for POS employee auth (/pos-auth) + manager override.

PINs are always 4-6 digits, validated at the edge; the raw PIN never leaves the
request — the service hashes it immediately.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ..models import EmployeeRole

_PIN_PATTERN = r"^\d{4,6}$"


class SetPinRequest(BaseModel):
    """Admin sets/rotates an employee's POS PIN (店長後台動作)."""

    model_config = ConfigDict(frozen=True)
    employee_id: UUID
    pin: str = Field(pattern=_PIN_PATTERN, description="4-6 位數字 PIN")


class PosLoginRequest(BaseModel):
    model_config = ConfigDict(frozen=True)
    employee_id: UUID
    pin: str = Field(pattern=_PIN_PATTERN)


class ManagerOverride(BaseModel):
    """A manager authorizes a sensitive action inline by entering their PIN."""

    model_config = ConfigDict(frozen=True)
    employee_id: UUID
    pin: str = Field(pattern=_PIN_PATTERN)


class PosLoginResponse(BaseModel):
    model_config = ConfigDict(frozen=True)
    token: str
    employee_id: UUID
    full_name: str
    role: EmployeeRole
    permissions: list[str]


class PosMe(BaseModel):
    model_config = ConfigDict(frozen=True)
    employee_id: UUID
    full_name: str
    role: EmployeeRole
    store_id: UUID
    permissions: list[str]


class PosEmployeeOption(BaseModel):
    """Pre-login picker row: who can log in at this store. No secrets."""

    model_config = ConfigDict(from_attributes=True)
    id: UUID
    full_name: str
    role: EmployeeRole
    has_pin: bool


__all__ = [
    "ManagerOverride",
    "PosEmployeeOption",
    "PosLoginRequest",
    "PosLoginResponse",
    "PosMe",
    "SetPinRequest",
]
