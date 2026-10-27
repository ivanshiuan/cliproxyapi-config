"""Restaurant API settings — Pydantic Settings v2 binding.

All settings read from environment (and `.env` if present, loaded by FastAPI lifespan).
"""

from __future__ import annotations

from functools import lru_cache
from typing import NamedTuple

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="RESTO_",
        extra="ignore",
        case_sensitive=False,
    )

    # ─── App ────────────────────────────────────────────────────────────
    env: str = "dev"  # dev | staging | prod
    debug: bool = False
    app_name: str = "Restaurant API"

    # ─── Database ───────────────────────────────────────────────────────
    db_host: str = "localhost"
    db_port: int = 5432
    db_user: str = "resto"
    db_password: str = "resto_dev_password"
    db_name: str = "resto_dev"
    db_echo: bool = False  # log SQL when True
    db_pool_size: int = 10
    db_max_overflow: int = 20

    # ─── Redis ──────────────────────────────────────────────────────────
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0

    # ─── Multi-tenancy ──────────────────────────────────────────────────
    # When False (MVP), every request uses the same tenant. When True, the
    # tenant_id is resolved from JWT/header and applied via Postgres RLS.
    multi_tenant_enabled: bool = False
    default_tenant_id: str = "00000000-0000-0000-0000-000000000000"

    # ─── Admin console auth ─────────────────────────────────────────────
    # 店長後台 (/demo/admin.html) is gated by a single shared passcode that
    # mints a short-lived HMAC-signed session cookie. Both MUST be overridden
    # in production (RESTO_ADMIN_PASSCODE / RESTO_SESSION_SECRET); the defaults
    # below are dev-only and intentionally obvious.
    admin_passcode: str = "changeme-admin"
    session_secret: str = "dev-insecure-session-secret-change-me"
    admin_session_ttl_seconds: int = Field(default=43_200, ge=60)  # 12h

    # ─── LINE Messaging API ─────────────────────────────────────────────
    # When the channel access token is set, the app switches from the
    # in-memory stub to the real LINE Messaging API (see
    # integrations/line/messenger.py::get_messenger). These use the LINE
    # community-standard env names (no RESTO_ prefix) via validation_alias,
    # so dropping LINE_CHANNEL_ACCESS_TOKEN into .env "just works".
    # The secret is only needed for inbound webhook signature validation;
    # outbound push/reply/multicast need the token alone.
    line_channel_access_token: str = Field(
        default="", validation_alias="LINE_CHANNEL_ACCESS_TOKEN"
    )
    line_channel_secret: str = Field(
        default="", validation_alias="LINE_CHANNEL_SECRET"
    )

    # ─── Locale ─────────────────────────────────────────────────────────
    default_timezone: str = "Asia/Taipei"
    default_currency: str = "TWD"

    # ─── Loyalty ────────────────────────────────────────────────────────
    # Earned points expire this many days after the closing order. 365 is
    # the F&B convention (Starbucks TW / FamiMart / 全家 all use 12 months).
    # Set 0 to mint points that never expire (legacy mode).
    points_expiry_days: int = Field(default=365, ge=0)

    # ─── Logging ────────────────────────────────────────────────────────
    log_level: str = Field(default="INFO", pattern=r"^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$")

    @property
    def database_url(self) -> str:
        """asyncpg DSN."""
        return (
            f"postgresql+asyncpg://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    @property
    def database_url_sync(self) -> str:
        """Sync DSN for Alembic migrations (psycopg or asyncpg-with-sync-driver).

        We standardize on psycopg2-style URL because Alembic's `run_migrations_online`
        helper expects a sync engine. The `+psycopg` driver is in alembic env.py.
        """
        return (
            f"postgresql+psycopg://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


# ─── Refuse to start with the built-in dev admin credentials ────────────────────────────
#
# RESTO_ADMIN_PASSCODE and RESTO_SESSION_SECRET default to obvious dev values that sit in a public
# repository. With the default session secret anyone can forge an admin cookie, and with the default
# passcode anyone can log in: the admin console (campaigns, the daily P&L, ...) would be open to the
# internet. So outside a developer's machine the API refuses to start until both are really set.

# RESTO_ENV values that plainly mean "a developer's machine or a test run". Anything else (prod,
# production, staging, or a typo) is treated as internet-facing: the check fails closed.
_LOCAL_ENVS = frozenset({"dev", "development", "local", "test", "testing", "ci"})
# Substrings that mark a value as copied from the defaults or from .env.example.
_PLACEHOLDER_MARKERS = ("change-me", "changeme", "insecure")
MIN_ADMIN_PASSCODE_LEN = 8
MIN_SESSION_SECRET_LEN = 32


class InsecureConfigurationError(RuntimeError):
    """An internet-facing deployment is configured with default or weak admin credentials."""


class CredentialProblem(NamedTuple):
    env_var: str
    reason: str


def is_local_env(env: str) -> bool:
    return env.strip().lower() in _LOCAL_ENVS


def admin_credential_problems(settings: Settings) -> list[CredentialProblem]:
    """What is wrong with the admin passcode / session secret: which variable and why.

    Never includes the values themselves: the result goes into logs.
    """
    checks = (
        ("admin_passcode", "RESTO_ADMIN_PASSCODE", MIN_ADMIN_PASSCODE_LEN, "pick your own store passcode"),
        ("session_secret", "RESTO_SESSION_SECRET", MIN_SESSION_SECRET_LEN, "generate one with: openssl rand -hex 32"),
    )
    problems: list[CredentialProblem] = []
    for field, env_var, min_len, how in checks:
        value: str = getattr(settings, field)
        lowered = value.strip().lower()
        if value == Settings.model_fields[field].default or any(m in lowered for m in _PLACEHOLDER_MARKERS):
            problems.append(CredentialProblem(env_var, f"is still a default or example value ({how})"))
        elif len(value.strip()) < min_len:
            problems.append(CredentialProblem(env_var, f"is too short, at least {min_len} characters ({how})"))
    return problems


def enforce_production_secrets(settings: Settings) -> None:
    """Raise unless a non-local environment has real admin credentials. A developer's machine is exempt."""
    if is_local_env(settings.env):
        return
    problems = admin_credential_problems(settings)
    if problems:
        which = " 與 ".join(p.env_var for p in problems)
        raise InsecureConfigurationError(
            f"RESTO_ENV={settings.env!r} is not a developer environment, but the admin credentials are unsafe, "
            "so the API refuses to start:\n  - "
            + "\n  - ".join(f"{p.env_var} {p.reason}" for p in problems)
            + "\nSet them in .env.production and restart. (On a dev machine use RESTO_ENV=dev.)"
            + f"\n(zh) 正式環境不能用預設或太弱的管理員密碼: 請在 .env.production 設定 {which} 後重啟。"
        )
