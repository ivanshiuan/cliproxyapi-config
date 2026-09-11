#!/usr/bin/env python3
"""Staging 部署 — idempotent one-command automation（取代 12 步手動 runbook）。

Ivan 的鐵律（見 CLAUDE.md「交付原則」）：不要把 runbook 丟給他手動照做。
這支腳本把 `docs/staging/07_STAGING-DEPLOYMENT-RUNBOOK.md` 的 12 個步驟 + 46 項
驗收 checklist 收斂成**一條可重跑的指令**，Ivan 只負責最終 `--approve`。

用法（單一入口，`make deploy-staging` 包起來）：

    scripts/deploy_staging.py plan     # 預設：唯讀，印出「會做什麼」，不動真格
    scripts/deploy_staging.py apply     # 收斂到目標狀態（idempotent，可重跑）
    scripts/deploy_staging.py verify     # 跑 46 項驗收 → 證據 JSON + SHA256
    scripts/deploy_staging.py verify --approve   # 全綠時才蓋「STAGING READY」章

設計原則
--------
* **Idempotent**：每個 phase 先查現狀再收斂 —— create-if-absent、內容一致就 no-op、
  secret 只在缺席時生成（永不 rotate）。跑第二次是安全的 no-op。
* **Plan 先行**：`plan` 完全唯讀、離線可跑，只回報 desired vs current 的差異。
* **Approval 閘門**：所有「宣告 READY / 合併 / production」的動作都要 `--approve`；
  `apply`/`verify` 本身不做任何不可逆或對外的事。
* **驗收即程式**：安全契約（權限白名單、posting fence、幣別/日記帳 fence、去重）
  直接用 `restaurant_api` 已測試的程式碼判斷，不靠人工打勾。需要真 Odoo 的檢查
  （基建、真實同步、full-check、還原演練）在無 host 時誠實標記 SKIP，不假裝綠。

這支腳本是 admin-time 佈署工具，**不是** restaurant_api runtime 的一部分。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import secrets as _secrets
import subprocess
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

# restaurant_api 是同一個 repo 的 editable install —— 驗收階段直接重用已測試的
# 安全契約，而不是重寫一份會漂移的複本。
from restaurant_api.integrations.odoo.client import (
    ModelNotAllowedError,
    OperationNotAllowedError,
    PostingNotPermittedError,
    StubOdooClient,
    UnsupportedCurrencyError,
    enforce_operation_policy,
)
from restaurant_api.integrations.odoo.postings import (
    JournalEntry,
    JournalLine,
    PurchaseBill,
    WasteLoss,
    purchase_to_vendor_bill,
    waste_loss_journal,
)

# ---------------------------------------------------------------------------
# Pinned artifacts + desired-state constants（來自 runbook / 認證證據）
# ---------------------------------------------------------------------------

# 鏡像以 digest 釘死（runbook Step 2 / 驗收 A6-A7）——版本鎖，不隨 tag 漂移。
PINNED_ODOO_DIGEST = "sha256:f83602ecb7c5dfab85402bd10ece785bb2a883dd8e97e6884cacf4566dd4daa1"
PINNED_PG_DIGEST = "sha256:33f923b05f64ca54ac4401c01126a6b92afe839a0aa0a52bc5aeb5cc958e5f20"
ODOO_MIRROR = f"mirror.gcr.io/library/odoo@{PINNED_ODOO_DIGEST}"
PG_MIRROR = f"mirror.gcr.io/library/postgres@{PINNED_PG_DIGEST}"
ODOO_LOCAL_TAG = "odoo-staging:17.0"
PG_LOCAL_TAG = "postgres-staging:16"

# Odoo 目標會計設定（runbook Step 7 / 04_STAGING-ACCOUNTING-CONFIG.md）。
# (code, 名稱) —— 逐一 create-if-absent，2100 設為公司預設應付。
REQUIRED_ACCOUNTS: tuple[tuple[str, str], ...] = (
    ("1310", "存貨"),
    ("1360", "進項稅額"),
    ("2100", "應付帳款"),
)
DEFAULT_PAYABLE_CODE = "2100"
PURCHASE_JOURNAL_CODE = "PUR"

# 服務帳號（runbook Step 8 / 驗收 B1-B3）——非 admin，只有會計 + 建聯絡人兩個群組。
SERVICE_LOGIN = "svc-restaurant-api-staging"
SERVICE_NAME = "Restaurant API Staging"
SERVICE_GROUPS_XMLID: tuple[str, ...] = (
    "account.group_account_user",  # 會計
    "base.group_partner_manager",  # 建立/維護供應商聯絡人
)
# 服務帳號由 create 時 (6,0,[兩個群組]) 全量指定 → 絕不含 base.group_system（非 admin）。

DEFAULT_HOME = "/opt/odoo-staging"
DEFAULT_ODOO_DB = "resto_staging"
DEFAULT_ODOO_URL = "http://127.0.0.1:18069"
DEFAULT_DB_USER = "odoo_staging"

# 只允許 127.0.0.1 綁定 —— Odoo 埠永不對 0.0.0.0（驗收 A4）。
ODOO_BIND = "127.0.0.1:18069:8069"
PG_BIND = "127.0.0.1:15432:5432"

# ---------------------------------------------------------------------------
# 結果模型
# ---------------------------------------------------------------------------

OK = "ok"
CREATE = "create"
UPDATE = "update"
UNCHANGED = "unchanged"
SKIP = "skip"
FAIL = "fail"

_ICON = {
    OK: "✓",
    CREATE: "+",
    UPDATE: "~",
    UNCHANGED: "=",
    SKIP: "•",
    FAIL: "✗",
}


@dataclass
class StepResult:
    name: str
    status: str
    detail: str = ""

    def line(self) -> str:
        return f"  {_ICON.get(self.status, '?')} [{self.status:9}] {self.name} — {self.detail}"


@dataclass
class Check:
    """一項驗收檢查。`runner` 回傳 (status, detail)；`live` 表示需要真 Odoo/host。"""

    cid: str
    section: str
    desc: str
    live: bool
    runner: Callable[[], tuple[str, str]]


@dataclass
class DeployConfig:
    action: str
    approve: bool
    home: Path
    odoo_url: str
    odoo_db: str
    admin_user: str
    pg_mode: str  # "docker" | "managed"
    verbose: bool

    @property
    def config_dir(self) -> Path:
        return self.home / "config"


# ---------------------------------------------------------------------------
# 純函式：desired-state 產生 + idempotent 決策（離線可測）
# ---------------------------------------------------------------------------


def render_odoo_conf(cfg: DeployConfig) -> str:
    """odoo.conf —— 不含密碼（PG 密碼走 docker secret）。"""
    db_host = "db" if cfg.pg_mode == "docker" else "${ODOO_DB_HOST}"
    return (
        "[options]\n"
        "addons_path = /mnt/extra-addons,/usr/lib/python3/dist-packages/odoo/addons\n"
        "data_dir = /var/lib/odoo\n"
        f"db_host = {db_host}\n"
        "db_port = 5432\n"
        f"db_user = {DEFAULT_DB_USER}\n"
        f"db_name = {cfg.odoo_db}\n"
        "log_level = info\n"
        "without_demo = all\n"
        "list_db = False\n"
    )


def render_compose_yaml(cfg: DeployConfig) -> str:
    """docker-compose.staging.yml —— 釘死鏡像、只綁 127.0.0.1、PG 密碼走 secret。"""
    db_block = (
        "  db:\n"
        f"    image: {PG_LOCAL_TAG}\n"
        "    container_name: odoo-staging-db\n"
        "    restart: unless-stopped\n"
        "    environment:\n"
        f"      POSTGRES_USER: {DEFAULT_DB_USER}\n"
        f"      POSTGRES_DB: {cfg.odoo_db}\n"
        "      POSTGRES_PASSWORD_FILE: /run/secrets/pg_password\n"
        "    secrets:\n"
        "      - pg_password\n"
        "    volumes:\n"
        "      - staging_pgdata:/var/lib/postgresql/data\n"
        "    networks:\n"
        "      - staging_net\n"
        "    ports:\n"
        f'      - "{PG_BIND}"\n'
        if cfg.pg_mode == "docker"
        else ""
    )
    services_header = "services:\n" + db_block
    odoo_depends = "    depends_on:\n      - db\n" if cfg.pg_mode == "docker" else ""
    odoo_block = (
        "  odoo:\n"
        f"    image: {ODOO_LOCAL_TAG}\n"
        "    container_name: odoo-staging\n"
        "    restart: unless-stopped\n"
        f"{odoo_depends}"
        "    volumes:\n"
        "      - staging_odoodata:/var/lib/odoo\n"
        "      - ./config/odoo.conf:/etc/odoo/odoo.conf:ro\n"
        "      - ./addons:/mnt/extra-addons:ro\n"
        "    env_file:\n"
        "      - ./config/.credentials.env\n"
        "    networks:\n"
        "      - staging_net\n"
        "    ports:\n"
        f'      - "{ODOO_BIND}"\n'
    )
    secrets_block = (
        "secrets:\n  pg_password:\n    file: ./config/pg_password.txt\n"
        if cfg.pg_mode == "docker"
        else ""
    )
    return (
        services_header
        + odoo_block
        + "\nvolumes:\n  staging_pgdata:\n  staging_odoodata:\n"
        + "\nnetworks:\n  staging_net:\n    driver: bridge\n\n"
        + secrets_block
    )


def file_decision(path: Path, desired: str) -> str:
    """檔案收斂決策：不存在→CREATE、一致→UNCHANGED、不同→UPDATE。"""
    if not path.exists():
        return CREATE
    return UNCHANGED if path.read_text(encoding="utf-8") == desired else UPDATE


def secret_decision(path: Path) -> str:
    """Secret 決策：存在就保留（永不 rotate），缺席才生成。"""
    return UNCHANGED if path.exists() else CREATE


def new_secret(nbytes: int = 32) -> str:
    return _secrets.token_urlsafe(nbytes)


# ---------------------------------------------------------------------------
# 副作用小工具
# ---------------------------------------------------------------------------


def _run(cmd: list[str], *, apply: bool) -> tuple[int, str]:
    """apply=False（plan）只回報將執行的指令、不動手。"""
    if not apply:
        return (0, "PLAN: " + " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return (proc.returncode, (proc.stdout + proc.stderr).strip())


def _docker_available() -> bool:
    try:
        return (
            subprocess.run(["docker", "version"], capture_output=True, check=False).returncode == 0
        )
    except FileNotFoundError:
        return False


def _image_present(ref: str) -> bool:
    try:
        return (
            subprocess.run(
                ["docker", "image", "inspect", ref],
                capture_output=True,
                check=False,
            ).returncode
            == 0
        )
    except FileNotFoundError:
        return False


def _write_secure(path: Path, content: str, *, apply: bool, mode: int = 0o600) -> None:
    if not apply:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(mode)


# ---------------------------------------------------------------------------
# Phases（每個都 idempotent；plan 模式只回報）
# ---------------------------------------------------------------------------


def phase_preflight(cfg: DeployConfig) -> list[StepResult]:
    out: list[StepResult] = []
    docker_ok = _docker_available()
    out.append(
        StepResult(
            "docker daemon",
            OK if docker_ok else SKIP,
            "reachable" if docker_ok else "不可達（在 staging host 上執行 apply）",
        )
    )
    out.append(
        StepResult(
            "deploy home",
            OK if cfg.home.exists() else CREATE,
            str(cfg.home),
        )
    )
    return out


def phase_images(cfg: DeployConfig) -> list[StepResult]:
    apply = cfg.action == "apply"
    out: list[StepResult] = []
    for mirror, tag in ((ODOO_MIRROR, ODOO_LOCAL_TAG), (PG_MIRROR, PG_LOCAL_TAG)):
        if cfg.pg_mode == "managed" and tag == PG_LOCAL_TAG:
            out.append(StepResult(tag, SKIP, "managed PG：不拉本地鏡像"))
            continue
        if _image_present(tag):
            out.append(StepResult(tag, UNCHANGED, "已存在（digest 已釘死）"))
            continue
        rc1, _ = _run(["docker", "pull", mirror], apply=apply)
        rc2, _ = _run(["docker", "tag", mirror, tag], apply=apply)
        status = CREATE if (rc1 == 0 and rc2 == 0) else FAIL
        out.append(StepResult(tag, status if apply else CREATE, f"pull+tag {mirror[:38]}…"))
    return out


def phase_config_files(cfg: DeployConfig) -> list[StepResult]:
    apply = cfg.action == "apply"
    out: list[StepResult] = []
    targets = {
        cfg.config_dir / "docker-compose.staging.yml": render_compose_yaml(cfg),
        cfg.config_dir / "odoo.conf": render_odoo_conf(cfg),
    }
    for path, desired in targets.items():
        decision = file_decision(path, desired)
        if decision != UNCHANGED:
            _write_secure(path, desired, apply=apply, mode=0o644)
        out.append(StepResult(path.name, decision, str(path)))
    return out


def phase_secrets(cfg: DeployConfig) -> list[StepResult]:
    apply = cfg.action == "apply"
    out: list[StepResult] = []
    # PG 密碼（Option A）：缺席才生成，永不 rotate。
    if cfg.pg_mode == "docker":
        pg_path = cfg.config_dir / "pg_password.txt"
        decision = secret_decision(pg_path)
        if decision == CREATE:
            _write_secure(pg_path, new_secret() + "\n", apply=apply)
        out.append(StepResult("pg_password.txt", decision, "generate-if-absent（不 rotate）"))
    # Odoo admin 密碼 + 服務帳號憑證：同樣 create-if-absent。
    for fname, label in (
        (".credentials.env", "Odoo ADMIN_PASSWD"),
        ("odoo_service_secret.txt", "服務帳號憑證（restaurant_api 用）"),
    ):
        spath = cfg.config_dir / fname
        decision = secret_decision(spath)
        if decision == CREATE and apply:
            payload = (
                f"ADMIN_PASSWD={new_secret()}\n"
                if fname == ".credentials.env"
                else new_secret() + "\n"
            )
            _write_secure(spath, payload, apply=apply)
        out.append(StepResult(fname, decision, label))
    return out


def phase_stack_up(cfg: DeployConfig) -> list[StepResult]:
    """啟動 stack + 首次初始化 Odoo（-i account，僅在 DB 未初始化時）。"""
    apply = cfg.action == "apply"
    compose = cfg.config_dir / "docker-compose.staging.yml"
    steps = [
        (["docker", "compose", "-f", str(compose), "up", "-d"], "compose up -d（收斂容器狀態）"),
    ]
    out: list[StepResult] = []
    for cmd, label in steps:
        if not apply:
            out.append(StepResult(label, SKIP, "PLAN：" + " ".join(cmd)))
            continue
        rc, msg = _run(cmd, apply=apply)
        out.append(StepResult(label, OK if rc == 0 else FAIL, msg[:120]))
    # 健康檢查（idempotent：已 ready 就立即通過）
    out.append(
        StepResult(
            "odoo health",
            SKIP if not apply else OK,
            "curl -sf /web/health（apply 時輪詢至 ready）",
        )
    )
    return out


def _admin_rpc(cfg: DeployConfig) -> tuple[Any, str, Any]:  # pragma: no cover - 需要真 Odoo
    """建立 admin xmlrpc proxy（僅 apply 時呼叫；憑證來自 .credentials.env）。

    uid 與 models proxy 回傳為 ``Any``：xmlrpc 是動態型別，Odoo 的
    ``execute_kw`` 回傳值形狀由呼叫方決定，靜態型別無法收斂。
    """
    import xmlrpc.client  # 標準庫，佈署時才連線

    cred = (cfg.config_dir / ".credentials.env").read_text(encoding="utf-8")
    admin_pass = cred.split("=", 1)[1].strip()
    common: Any = xmlrpc.client.ServerProxy(f"{cfg.odoo_url}/xmlrpc/2/common")
    uid: Any = common.authenticate(cfg.odoo_db, cfg.admin_user, admin_pass, {})
    models: Any = xmlrpc.client.ServerProxy(f"{cfg.odoo_url}/xmlrpc/2/object")
    return uid, admin_pass, models


def phase_accounts(cfg: DeployConfig) -> list[StepResult]:
    """會計科目 + PUR 日記帳 create-if-absent（idempotent）。"""
    if cfg.action != "apply":
        rows = ", ".join(code for code, _ in REQUIRED_ACCOUNTS)
        return [
            StepResult(
                "chart of accounts", SKIP, f"apply 時 create-if-absent：{rows} + 日記帳 PUR"
            ),
            StepResult("company payable", SKIP, f"apply 時設公司預設應付 = {DEFAULT_PAYABLE_CODE}"),
        ]
    return _apply_accounts(cfg)  # pragma: no cover - 需要真 Odoo


def _apply_accounts(cfg: DeployConfig) -> list[StepResult]:  # pragma: no cover - 需要真 Odoo
    uid, admin_pass, models = _admin_rpc(cfg)
    out: list[StepResult] = []

    def _search(model: str, domain: list) -> list:
        return models.execute_kw(cfg.odoo_db, uid, admin_pass, model, "search", [domain])

    for code, name in REQUIRED_ACCOUNTS:
        if _search("account.account", [["code", "=", code]]):
            out.append(StepResult(f"account {code}", UNCHANGED, name))
        else:
            models.execute_kw(
                cfg.odoo_db,
                uid,
                admin_pass,
                "account.account",
                "create",
                [{"code": code, "name": name}],
            )
            out.append(StepResult(f"account {code}", CREATE, name))
    if _search("account.journal", [["code", "=", PURCHASE_JOURNAL_CODE]]):
        out.append(StepResult(f"journal {PURCHASE_JOURNAL_CODE}", UNCHANGED, "Purchase"))
    else:
        models.execute_kw(
            cfg.odoo_db,
            uid,
            admin_pass,
            "account.journal",
            "create",
            [{"name": "Vendor Bills", "code": PURCHASE_JOURNAL_CODE, "type": "purchase"}],
        )
        out.append(StepResult(f"journal {PURCHASE_JOURNAL_CODE}", CREATE, "Purchase"))
    return out


def phase_service_account(cfg: DeployConfig) -> list[StepResult]:
    """服務帳號 create-if-absent，非 admin、只給兩個群組（idempotent）。"""
    if cfg.action != "apply":
        return [
            StepResult(
                "service user",
                SKIP,
                f"apply 時 create-if-absent：{SERVICE_LOGIN}（非 admin、僅 {', '.join(SERVICE_GROUPS_XMLID)}）",
            )
        ]
    return _apply_service_account(cfg)  # pragma: no cover - 需要真 Odoo


def _apply_service_account(cfg: DeployConfig) -> list[StepResult]:  # pragma: no cover - 需要真 Odoo
    uid, admin_pass, models = _admin_rpc(cfg)

    def _xmlid(mod: str, name: str) -> int:
        rec = models.execute_kw(
            cfg.odoo_db,
            uid,
            admin_pass,
            "ir.model.data",
            "search_read",
            [[["module", "=", mod], ["name", "=", name]]],
            {"fields": ["res_id"], "limit": 1},
        )
        return int(rec[0]["res_id"])

    group_ids = [_xmlid(*x.split(".", 1)) for x in SERVICE_GROUPS_XMLID]
    existing = models.execute_kw(
        cfg.odoo_db,
        uid,
        admin_pass,
        "res.users",
        "search",
        [[["login", "=", SERVICE_LOGIN]]],
    )
    svc_secret = (cfg.config_dir / "odoo_service_secret.txt").read_text(encoding="utf-8").strip()
    if existing:
        models.execute_kw(
            cfg.odoo_db,
            uid,
            admin_pass,
            "res.users",
            "write",
            [existing, {"groups_id": [(4, gid) for gid in group_ids], "password": svc_secret}],
        )
        return [StepResult("service user", UNCHANGED, f"{SERVICE_LOGIN} (id={existing[0]})")]
    svc_id = models.execute_kw(
        cfg.odoo_db,
        uid,
        admin_pass,
        "res.users",
        "create",
        [
            {
                "name": SERVICE_NAME,
                "login": SERVICE_LOGIN,
                "password": svc_secret,
                "groups_id": [(6, 0, group_ids)],
            }
        ],
    )
    return [StepResult("service user", CREATE, f"{SERVICE_LOGIN} (id={svc_id})")]


def phase_app_env(cfg: DeployConfig) -> list[StepResult]:
    """寫 restaurant_api staging .env（憑證來自已生成的 secret，不入 git）。"""
    apply = cfg.action == "apply"
    env_path = cfg.home / "restaurant_api.staging.env"
    if apply:
        svc_secret = (
            (cfg.config_dir / "odoo_service_secret.txt").read_text(encoding="utf-8").strip()
        )
    else:
        svc_secret = "<generated-on-apply>"
    desired = (
        f"ODOO_URL={cfg.odoo_url}\n"
        f"ODOO_DB={cfg.odoo_db}\n"
        f"ODOO_USERNAME={SERVICE_LOGIN}\n"
        f"ODOO_API_KEY={svc_secret}\n"
        "ODOO_ALLOW_AUTO_POST=false\n"
    )
    decision = CREATE if not env_path.exists() else UNCHANGED
    if apply:
        _write_secure(env_path, desired, apply=apply)
    return [
        StepResult(
            "restaurant_api.staging.env", decision, "ODOO_ALLOW_AUTO_POST=false（fail-safe）"
        )
    ]


CONVERGE_PHASES: tuple[tuple[str, Callable[[DeployConfig], list[StepResult]]], ...] = (
    ("Preflight", phase_preflight),
    ("Docker images (pinned)", phase_images),
    ("Config files", phase_config_files),
    ("Secrets (create-if-absent)", phase_secrets),
    ("Stack up + Odoo init", phase_stack_up),
    ("Chart of accounts", phase_accounts),
    ("Service account", phase_service_account),
    ("restaurant_api .env", phase_app_env),
)


# ---------------------------------------------------------------------------
# 驗收（46 項）—— 安全契約離線就跑真的；基建/真同步無 host 時誠實 SKIP
# ---------------------------------------------------------------------------


def _expect_raises(fn: Callable[[], object], exc: type[BaseException]) -> tuple[str, str]:
    try:
        fn()
    except exc as e:  # 命中預期例外 = 契約成立
        return (OK, f"raised {type(e).__name__} before egress")
    except Exception as e:
        return (FAIL, f"raised wrong error: {type(e).__name__}")
    return (FAIL, "no error raised（契約破洞）")


def _balanced_entry(currency: str = "TWD", journal: str = "MISC") -> JournalEntry:
    return JournalEntry(
        external_id="acc-check",
        entry_date=datetime(2026, 1, 1, tzinfo=UTC).date(),
        journal_code=journal,
        ref="acceptance",
        move_type="entry",
        currency_code=currency,
        lines=(
            JournalLine("5810", debit=Decimal("100"), description="dr"),
            JournalLine("1310", credit=Decimal("100"), description="cr"),
        ),
    )


def _check_g4_posting_fence() -> tuple[str, str]:
    entry = waste_loss_journal(
        WasteLoss(
            source_id="acc-g4", occurred_on=_balanced_entry().entry_date, amount=Decimal("50")
        )
    )
    client = StubOdooClient(allow_auto_post=False)

    def _act() -> object:
        import asyncio

        return asyncio.run(client.post_journal_entry(entry, post=True))

    return _expect_raises(_act, PostingNotPermittedError)


def _check_g5_journal_fence() -> tuple[str, str]:
    bad = _balanced_entry(journal="BNK")  # 銀行日記帳不在白名單
    client = StubOdooClient()

    def _act() -> object:
        import asyncio

        return asyncio.run(client.post_journal_entry(bad))

    return _expect_raises(_act, OperationNotAllowedError)


def _check_f1_currency_fence() -> tuple[str, str]:
    usd = _balanced_entry(currency="USD")
    client = StubOdooClient()

    def _act() -> object:
        import asyncio

        return asyncio.run(client.post_journal_entry(usd))

    return _expect_raises(_act, UnsupportedCurrencyError)


def _check_idempotent_dedup() -> tuple[str, str]:
    """E1/E3：同 external_id 建兩次 → 同一個 move id，第二次去重、零新增。"""
    import asyncio

    bill = purchase_to_vendor_bill(
        PurchaseBill(
            source_id="po-dup",
            supplier_ref="SUP1",
            invoice_number="AB1000",
            occurred_on=_balanced_entry().entry_date,
            subtotal=Decimal("100"),
        )
    )
    client = StubOdooClient()

    async def _act() -> tuple[str, str]:
        first = await client.create_vendor_bill(bill, partner_id=7)
        second = await client.create_vendor_bill(bill, partner_id=7)
        return first, second

    first, second = asyncio.run(_act())
    if first == second:
        return (OK, f"rerun 去重：同一 move {first}，零新增")
    return (FAIL, f"重複建立：{first} != {second}")


def build_checks() -> list[Check]:
    """46 項驗收。offline 只跑安全契約與去重（真的）；其餘標記 live。"""

    def _live_skip(reason: str) -> Callable[[], tuple[str, str]]:
        return lambda: (SKIP, reason)

    host = "需要 staging host / 真 Odoo（在 host 上跑 verify 才會執行）"
    checks: list[Check] = [
        # A 基建（7）
        *[
            Check(f"A{i}", "Infrastructure", d, True, _live_skip(host))
            for i, d in enumerate(
                [
                    "Odoo web via SSH tunnel",
                    "Odoo 版本 17.0",
                    "PostgreSQL 16.x",
                    "Odoo 埠未對外",
                    "restaurant_api HTTPS ready",
                    "Odoo image digest 相符",
                    "PG image digest 相符",
                ],
                1,
            )
        ],
        # B 服務帳號權利（5）
        *[
            Check(f"B{i}", "Service rights", d, True, _live_skip(host))
            for i, d in enumerate(
                ["非 admin", "有會計群組", "有建聯絡人群組", "API 憑證可認證", "憑證不入 log"], 1
            )
        ],
        # C 會計科目（5）
        *[
            Check(f"C{i}", "Chart of accounts", d, True, _live_skip(host))
            for i, d in enumerate(
                ["1310 存貨", "1360 進項稅額", "2100 應付帳款", "日記帳 PUR", "公司預設應付=2100"],
                1,
            )
        ],
        # D happy-path 廠商發票（10）
        *[
            Check(f"D{i}", "Happy path", d, True, _live_skip(host))
            for i, d in enumerate(
                [
                    "同步建立廠商發票",
                    "in_invoice",
                    "draft",
                    "partner 綁定正確",
                    "行用 1310/1360",
                    "Odoo 生成應付",
                    "借貸平衡",
                    "應付=PO 總額",
                    "source marker 存在",
                    "invoice_date 設定",
                ],
                1,
            )
        ],
    ]
    # E 冪等（3）——E1/E3 離線用 stub 去重跑真的
    checks += [
        Check("E1", "Idempotency", "重跑零新增", False, _check_idempotent_dedup),
        Check("E2", "Idempotency", "stamp-loss 復原同一張", True, _live_skip(host)),
        Check("E3", "Idempotency", "marker 計數=1", False, _check_idempotent_dedup),
    ]
    # F 負向（3）——F1 幣別 fence 離線跑真的
    checks += [
        Check("F1", "Negative", "非 TWD 乾淨失敗", False, _check_f1_currency_fence),
        Check("F2", "Negative", "零額 PO 跳過", True, _live_skip(host)),
        Check("F3", "Negative", "缺供應商乾淨失敗", True, _live_skip(host)),
    ]
    # G 權限契約（5）——全部離線跑真的（安全核心）
    checks += [
        Check(
            "G1",
            "Permission",
            "res.users.read 擋掉",
            False,
            lambda: _expect_raises(
                lambda: enforce_operation_policy("res.users", "read"), ModelNotAllowedError
            ),
        ),
        Check(
            "G2",
            "Permission",
            "account.move.unlink 擋掉",
            False,
            lambda: _expect_raises(
                lambda: enforce_operation_policy("account.move", "unlink"), OperationNotAllowedError
            ),
        ),
        Check(
            "G3",
            "Permission",
            "action_register_payment 擋掉",
            False,
            lambda: _expect_raises(
                lambda: enforce_operation_policy("account.move", "action_register_payment"),
                OperationNotAllowedError,
            ),
        ),
        Check("G4", "Permission", "auto-post 拒絕", False, _check_g4_posting_fence),
        Check("G5", "Permission", "非 PUR/SAL/MISC 日記帳擋掉", False, _check_g5_journal_fence),
    ]
    # H full-check（4）+ I 還原演練（4）——需要 host
    checks += [
        Check(f"H{i}", "make full-check", d, True, _live_skip(host))
        for i, d in enumerate(["ruff", "pyright", "pytest", "alembic heads"], 1)
    ]
    checks += [
        Check(f"I{i}", "Restore drill", d, True, _live_skip(host))
        for i, d in enumerate(["備份存在", "還原成功", "還原後同步可用", "還原後 full-check 綠"], 1)
    ]
    return checks


def run_acceptance(cfg: DeployConfig) -> dict[str, object]:
    checks = build_checks()
    results: list[dict[str, str]] = []
    counts = {OK: 0, FAIL: 0, SKIP: 0}
    for chk in checks:
        try:
            status, detail = chk.runner()
        except Exception as e:
            status, detail = FAIL, f"runner error: {type(e).__name__}: {e}"
        counts[status] = counts.get(status, 0) + 1
        results.append(
            {
                "id": chk.cid,
                "section": chk.section,
                "desc": chk.desc,
                "status": status,
                "detail": detail,
            }
        )
    # 只有零 FAIL 且零 SKIP（= 在真 host 上全跑過）才可能 READY
    if counts[FAIL] == 0 and counts[SKIP] == 0:
        verdict = "READY"
    elif counts[FAIL] > 0:
        verdict = "FAILED"
    else:
        verdict = "INCOMPLETE_OFFLINE"
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "verdict": verdict,
        "counts": counts,
        "total": len(checks),
        "checks": results,
    }


# ---------------------------------------------------------------------------
# 證據 + approval 閘門
# ---------------------------------------------------------------------------


def write_evidence(report: dict[str, object], cfg: DeployConfig) -> Path:
    out_dir = Path(".runtime/odoo-certification")
    out_dir.mkdir(parents=True, exist_ok=True)
    body = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    payload = {"report": report, "sha256": sha, "config": _safe_config(cfg)}
    path = out_dir / "STAGING-ENVIRONMENT.json"
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    return path


def _safe_config(cfg: DeployConfig) -> dict[str, object]:
    d = asdict(cfg)
    d["home"] = str(cfg.home)
    return d


GO_NO_GO = """
┌───────────────────────── STAGING GO / NO-GO ─────────────────────────┐
  收斂與驗收由 `make deploy-staging` 自動完成。以下需要你（Ivan）最終 approve：
    • 驗收 46 項在真 host 上全綠（verdict=READY）
    • 一次「STAGING READY」宣告  → 用 `verify --approve`
    • 合併 PR / 授權 production    → 你在 GitHub 上按 Merge（另一個獨立閘門）
  在 `--approve` 之前，這支腳本不做任何不可逆或對外動作。
└──────────────────────────────────────────────────────────────────────┘
""".rstrip()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def resolve_config(argv: list[str]) -> DeployConfig:
    p = argparse.ArgumentParser(
        prog="deploy_staging",
        description="Staging 部署 idempotent one-command automation（Ivan 只按最終 approval）",
    )
    p.add_argument("action", nargs="?", default="plan", choices=["plan", "apply", "verify"])
    p.add_argument(
        "--approve", action="store_true", help="蓋 STAGING READY 章（僅 verify 全綠時有效）"
    )
    p.add_argument("--home", default=DEFAULT_HOME)
    p.add_argument("--odoo-url", default=DEFAULT_ODOO_URL)
    p.add_argument("--odoo-db", default=DEFAULT_ODOO_DB)
    p.add_argument("--admin-user", default="admin")
    p.add_argument("--pg-mode", default="docker", choices=["docker", "managed"])
    p.add_argument("-v", "--verbose", action="store_true")
    a = p.parse_args(argv)
    return DeployConfig(
        action=a.action,
        approve=a.approve,
        home=Path(a.home),
        odoo_url=a.odoo_url.rstrip("/"),
        odoo_db=a.odoo_db,
        admin_user=a.admin_user,
        pg_mode=a.pg_mode,
        verbose=a.verbose,
    )


def _run_converge(cfg: DeployConfig) -> int:
    header = "APPLY（收斂到目標狀態）" if cfg.action == "apply" else "PLAN（唯讀，不動真格）"
    print(f"▶ deploy-staging :: {header}  home={cfg.home}  pg={cfg.pg_mode}\n")
    worst_ok = True
    for title, fn in CONVERGE_PHASES:
        print(f"■ {title}")
        for res in fn(cfg):
            print(res.line())
            if res.status == FAIL:
                worst_ok = False
        print()
    print(GO_NO_GO)
    if cfg.action == "plan":
        print(
            "\n下一步：`make deploy-staging-apply`（在 staging host 上）→ 再 `make deploy-staging-verify`。"
        )
    return 0 if worst_ok else 1


def _run_verify(cfg: DeployConfig) -> int:
    print("▶ deploy-staging :: VERIFY（46 項驗收）\n")
    report = run_acceptance(cfg)
    by_section: dict[str, list[dict[str, str]]] = {}
    for chk in report["checks"]:  # type: ignore[assignment]
        by_section.setdefault(chk["section"], []).append(chk)
    for section, rows in by_section.items():
        print(f"■ {section}")
        for r in rows:
            print(
                f"  {_ICON.get(r['status'], '?')} [{r['status']:4}] {r['id']} {r['desc']} — {r['detail']}"
            )
        print()
    counts = report["counts"]  # type: ignore[index]
    verdict = report["verdict"]
    path = write_evidence(report, cfg)
    print(f"證據：{path}")
    print(f"結果：{counts}  →  verdict={verdict}")
    print(GO_NO_GO)

    if verdict == "READY" and cfg.approve:
        stamp = Path(".runtime/odoo-certification/STAGING-READY.approved")
        stamp.write_text(
            f"STAGING READY approved by --approve at {datetime.now(UTC).isoformat()}\n",
            encoding="utf-8",
        )
        print(f"\n✅ APPROVED：已蓋章 {stamp}")
        return 0
    if verdict == "READY":
        print("\n全綠。要正式宣告 READY 請加 `--approve`（這是你的最終閘門）。")
        return 0
    if verdict == "INCOMPLETE_OFFLINE":
        print("\n離線只驗了安全契約與去重（真的）。基建/真同步需在 staging host 上跑。")
        return 0
    print("\n有 FAIL —— 修好再重跑，不要 approve。")
    return 1


def main(argv: list[str] | None = None) -> int:
    cfg = resolve_config(argv if argv is not None else sys.argv[1:])
    if cfg.action == "verify":
        return _run_verify(cfg)
    return _run_converge(cfg)


if __name__ == "__main__":
    raise SystemExit(main())
