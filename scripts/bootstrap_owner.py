"""Bootstrap the first ``owner`` user for a fresh Phase-2 auth system.

Run after ``make db-migrate`` (both auth schema + seed migrations applied)
and after at least one tenant + employee row exists (from seed data or
manual insert).

    python -m scripts.bootstrap_owner \\
        --tenant-id <UUID> \\
        --employee-id <UUID> \\
        --email owner@example.com \\
        --password-stdin

The tool:
  1. Reads password from stdin (never from argv — ps(1) would leak it).
  2. Creates a ``user_credentials`` row (argon2id hash) 1:1 with the
     supplied ``--employee-id``.
  3. Grants the seeded ``owner`` role via ``employee_roles``.

Idempotent by (tenant_id, employee_id, email):
  - If credentials already exist and email matches → returns "already ok".
  - If credentials exist but email mismatches → refuses.
  - If ``owner`` role grant already exists → skipped silently.

Usage examples:

    # First-time bootstrap
    echo 'MyStr0ngP@ss' | python -m scripts.bootstrap_owner \\
        --tenant-id 018e6600-... --employee-id 018e6601-... \\
        --email ivan@myrestaurant.tw --password-stdin

    # From a secrets manager (recommended for prod)
    aws secretsmanager get-secret-value --secret-id owner-pw \\
        --query SecretString --output text | \\
        python -m scripts.bootstrap_owner --tenant-id ... --password-stdin ...
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid

from sqlalchemy import select

from restaurant_api.database import dispose_engine, get_sessionmaker
from restaurant_api.models import (
    Employee,
    EmployeeRoleGrant,
    Role,
    Tenant,
    UserCredential,
)
from restaurant_api.security.passwords import hash_password


class BootstrapError(RuntimeError):
    """Any user-facing failure — printed with a friendly message before exit."""


def _read_password(from_stdin: bool) -> str:
    if from_stdin:
        pw = sys.stdin.readline().rstrip("\n")
    else:
        import getpass

        pw = getpass.getpass("Owner password (won't echo): ")
    if len(pw) < 8:
        raise BootstrapError("password too short (min 8 chars)")
    if not any(c.isalpha() for c in pw) or not any(c.isdigit() for c in pw):
        raise BootstrapError(
            "password must contain at least one letter and one digit"
        )
    return pw


async def bootstrap(
    *,
    tenant_id: uuid.UUID,
    employee_id: uuid.UUID,
    email: str,
    password: str,
) -> None:
    email = email.strip().lower()
    session_factory = get_sessionmaker()
    async with session_factory() as session, session.begin():
        # 1. Sanity-check the tenant + employee exist and belong together.
        tenant = (
            await session.execute(
                select(Tenant).where(Tenant.id == tenant_id)
            )
        ).scalar_one_or_none()
        if tenant is None:
            raise BootstrapError(f"tenant {tenant_id} not found")

        employee = (
            await session.execute(
                select(Employee).where(Employee.id == employee_id)
            )
        ).scalar_one_or_none()
        if employee is None:
            raise BootstrapError(f"employee {employee_id} not found")
        if employee.tenant_id != tenant_id:
            raise BootstrapError(
                f"employee {employee_id} belongs to tenant "
                f"{employee.tenant_id}, not {tenant_id}"
            )

        # 2. Locate the seeded system ``owner`` role.
        owner_role = (
            await session.execute(
                select(Role).where(
                    Role.name == "owner", Role.tenant_id.is_(None)
                )
            )
        ).scalar_one_or_none()
        if owner_role is None:
            raise BootstrapError(
                "system 'owner' role not seeded — did the "
                "auth_rbac_seed migration run?"
            )

        # 3. Idempotent credentials.
        existing = (
            await session.execute(
                select(UserCredential).where(
                    UserCredential.employee_id == employee_id
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            if existing.email != email:
                raise BootstrapError(
                    f"credential for employee {employee_id} already exists "
                    f"with email {existing.email}; refusing to change "
                    f"(use /auth/change_password instead)"
                )
            print(
                f"✓ credential already present for {email} — skipping"
            )
        else:
            session.add(
                UserCredential(
                    employee_id=employee_id,
                    email=email,
                    password_hash=hash_password(password),
                )
            )
            await session.flush()
            print(f"✓ credential created for {email}")

        # 4. Idempotent role grant.
        grant = (
            await session.execute(
                select(EmployeeRoleGrant).where(
                    EmployeeRoleGrant.employee_id == employee_id,
                    EmployeeRoleGrant.role_id == owner_role.id,
                )
            )
        ).scalar_one_or_none()
        if grant is None:
            session.add(
                EmployeeRoleGrant(
                    employee_id=employee_id,
                    role_id=owner_role.id,
                    granted_by=None,  # system grant
                )
            )
            print(f"✓ owner role granted to employee {employee_id}")
        else:
            print("✓ owner role already granted — skipping")

    await dispose_engine()


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Bootstrap the first owner login for a fresh tenant."
    )
    p.add_argument(
        "--tenant-id", type=uuid.UUID, required=True, help="Existing tenant UUID"
    )
    p.add_argument(
        "--employee-id",
        type=uuid.UUID,
        required=True,
        help="Existing employee UUID to attach the credential to",
    )
    p.add_argument("--email", type=str, required=True, help="Login email")
    p.add_argument(
        "--password-stdin",
        action="store_true",
        help="Read the password from stdin instead of prompting on the tty. "
        "Recommended for CI / secrets-manager pipes.",
    )
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        password = _read_password(from_stdin=args.password_stdin)
    except BootstrapError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        asyncio.run(
            bootstrap(
                tenant_id=args.tenant_id,
                employee_id=args.employee_id,
                email=args.email,
                password=password,
            )
        )
    except BootstrapError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print()
    print("bootstrap complete — try `curl -X POST /auth/login`")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
