"""Restaurant API settings — Pydantic Settings v2 binding.

All settings read from environment (and `.env` if present, loaded by FastAPI lifespan).
"""

from __future__ import annotations

from functools import lru_cache

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

    # ─── Auth / RBAC (Phase 2, see specs/auth_rbac_system.md) ───────────
    # JWT signing secret. MUST be ≥32 chars in prod; dev default is
    # intentionally obvious so a misconfigured deploy fails loudly.
    jwt_secret: str = Field(
        default="dev-insecure-jwt-secret-change-me-min-32-chars",
        validation_alias="JWT_SECRET",
        min_length=32,
    )
    jwt_algorithm: str = "HS256"
    jwt_access_ttl_seconds: int = Field(default=900, ge=60)         # 15 min
    jwt_refresh_ttl_seconds: int = Field(default=2_592_000, ge=300) # 30 days
    # Failed-login lockout policy.
    auth_max_failed_logins: int = Field(default=5, ge=1)
    auth_lockout_seconds: int = Field(default=900, ge=60)           # 15 min
    # Transition mode for existing routers — see spec §Global Dependencies.
    # off:     no auth check (Phase 1 behaviour, still header-tenant)
    # warn:    parse JWT if present, log a warning if absent, but don't 401
    # enforce: full Phase 2 behaviour, 401 on missing/invalid token
    # Default flipped from "warn" → "enforce" in PR-C — existing router
    # tests deliberately fail here; fixture migration in PR-D re-greens
    # them. This is the intended breaking-change slice per spec §PR-C.
    auth_enforcement: str = Field(
        default="enforce", pattern=r"^(off|warn|enforce)$"
    )

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
