"""End-to-end contract test: real DB -> real service -> real HttpOdooClient
-> simulated Odoo JSON-RPC server.

This is the deterministic stand-in for a live Odoo sandbox (none exists in
this environment — classified EXTERNAL_DEPENDENCY_BLOCKED). ``FakeOdooServer``
implements enough of the Odoo JSON-RPC surface (authenticate, search_read,
create, write, action_post) statefully, so the full mission scenario runs over
the wire protocol the real backend speaks: supplier upsert, draft vendor bill,
balance, dedup on re-run, retry after timeout, and the permission blocks.

Set ``ODOO_CERT_EVIDENCE_PATH`` to make the main scenario dump the created
record ids as JSON evidence.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import ClassVar

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from restaurant_api.integrations.odoo import (
    HttpOdooClient,
    ModelNotAllowedError,
    OperationNotAllowedError,
)
from restaurant_api.models import (
    Ingredient,
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseOrderStatus,
    Store,
    Supplier,
    Tenant,
)
from restaurant_api.services.odoo_sync_service import sync_purchase_orders

pytestmark = pytest.mark.asyncio


class _LegacyPayloadRejected(Exception):
    """The fake Odoo refuses a payload real Odoo 17 would reject (e.g. a vendor
    bill sent as raw journal ``line_ids`` instead of ``invoice_line_ids``)."""


class FakeOdooServer:
    """Stateful in-process Odoo JSON-RPC double.

    Speaks the same wire shapes HttpOdooClient sends. Journals/accounts are
    pre-seeded; partners and moves accumulate in dicts so re-runs and dedup
    are observable. ``fail_creates_with_timeout`` makes the next N create
    calls raise a transport timeout, to exercise the retry path.
    """

    JOURNALS: ClassVar[dict[str, int]] = {"PUR": 3, "SAL": 4, "MISC": 5}
    ACCOUNTS: ClassVar[dict[str, int]] = {
        "1310": 11,
        "1360": 12,
        "2100": 13,
        "1100": 14,
        "4100": 15,
        "2260": 16,
    }

    def __init__(self) -> None:
        self.partners: dict[int, dict[str, object]] = {}
        self.moves: dict[int, dict[str, object]] = {}
        self.move_create_payloads: list[dict[str, object]] = []
        self.posted_move_ids: list[int] = []
        self.request_log: list[tuple[str, str]] = []
        self.fail_creates_with_timeout = 0
        # account.move-specific failure modes for the retry-recovery contract:
        # fail BEFORE creating (server never committed) vs. create-then-drop
        # the response (server committed, client never saw the id).
        self.fail_move_creates_with_timeout = 0
        self.drop_move_create_response = 0
        self._next = 500

    def _nid(self) -> int:
        self._next += 1
        return self._next

    def _materialize_move(self, payload: dict[str, object]) -> dict[str, object]:
        """Model Odoo's ``account.move`` create semantics.

        - ``in_invoice``: expand ``invoice_line_ids`` into debit journal items
          and generate exactly one accounts-payable credit counterpart for the
          partner (as real Odoo does). Reject a legacy raw-``line_ids`` vendor
          bill — that shape is silently rebalanced (and then refused) by Odoo.
        - ``entry``: store the raw balanced ``line_ids`` verbatim.
        """
        move_type = payload.get("move_type")
        stored = dict(payload)
        stored["state"] = "draft"
        if move_type == "in_invoice":
            if "line_ids" in payload:
                raise _LegacyPayloadRejected(
                    "The move is not balanced: a vendor bill must use "
                    "invoice_line_ids, not raw debit/credit line_ids."
                )
            inv = [cmd[2] for cmd in payload.get("invoice_line_ids", [])]  # type: ignore[union-attr]
            items: list[dict[str, object]] = []
            debit_total = 0.0
            for ln in inv:
                amount = float(ln["price_unit"]) * float(ln.get("quantity", 1.0))
                debit_total += amount
                items.append({"account_id": ln["account_id"], "name": ln.get("name"),
                              "debit": amount, "credit": 0.0, "display_type": "product"})
            # Odoo generates ONE payable counterpart; amount == invoice debit total.
            items.append({"account_id": self.ACCOUNTS["2100"], "name": "Payable (Odoo-generated)",
                          "debit": 0.0, "credit": debit_total, "display_type": "payment_term",
                          "auto_generated": True, "partner_id": payload.get("partner_id")})
            assert debit_total == sum(i["credit"] for i in items), "fake invoice imbalance"
            stored["line_ids"] = [(0, 0, it) for it in items]
            stored["amount_total"] = debit_total
            stored["amount_residual"] = debit_total
        else:  # "entry" — raw journal items only
            if "invoice_line_ids" in payload:
                raise _LegacyPayloadRejected("a general entry must use raw line_ids")
            stored["line_ids"] = payload.get("line_ids", [])
        return stored

    def handler(self, request: httpx.Request) -> httpx.Response:
        params = json.loads(request.content)["params"]
        if params["service"] == "common":
            return httpx.Response(200, json={"result": 7})
        # execute_kw args: [db, uid, key, model, method, args, kwargs]
        _, _, _, model, method, args, _kwargs = params["args"]
        self.request_log.append((model, method))

        if model == "account.journal" and method == "search_read":
            code = args[0][0][2]
            jid = self.JOURNALS.get(code)
            return httpx.Response(200, json={"result": [{"id": jid}] if jid else []})
        if model == "account.account" and method == "search_read":
            code = args[0][0][2]
            aid = self.ACCOUNTS.get(code)
            return httpx.Response(200, json={"result": [{"id": aid}] if aid else []})
        if model == "res.partner":
            if method == "search_read":
                ref = args[0][0][2]
                hits = [{"id": pid} for pid, v in self.partners.items() if v.get("ref") == ref]
                return httpx.Response(200, json={"result": hits[:1]})
            if method == "create":
                if self.fail_creates_with_timeout > 0:
                    self.fail_creates_with_timeout -= 1
                    raise httpx.ConnectTimeout("simulated odoo outage")
                pid = self._nid()
                self.partners[pid] = dict(args[0])
                return httpx.Response(200, json={"result": pid})
            if method == "write":
                pid = args[0][0]
                self.partners[pid].update(args[1])
                return httpx.Response(200, json={"result": True})
        if model == "account.move":
            if method == "search_read":
                needle = args[0][0][2]
                hits = [
                    {"id": mid} for mid, v in self.moves.items() if needle in str(v.get("ref", ""))
                ]
                return httpx.Response(200, json={"result": hits[:1]})
            if method == "create":
                if self.fail_creates_with_timeout > 0:
                    self.fail_creates_with_timeout -= 1
                    raise httpx.ConnectTimeout("simulated odoo outage")
                if self.fail_move_creates_with_timeout > 0:
                    self.fail_move_creates_with_timeout -= 1
                    raise httpx.ConnectTimeout("simulated odoo outage (move create)")
                payload = dict(args[0])
                self.move_create_payloads.append(payload)
                try:
                    stored = self._materialize_move(payload)
                except _LegacyPayloadRejected as exc:
                    # Odoo 17 refuses this shape (unbalanced invoice) — JSON-RPC error.
                    return httpx.Response(200, json={"error": {"data": {"message": str(exc)}}})
                mid = self._nid()
                self.moves[mid] = stored
                if self.drop_move_create_response > 0:
                    # the move IS committed server-side; only the response dies
                    self.drop_move_create_response -= 1
                    raise httpx.ReadTimeout("response lost after server-side create")
                return httpx.Response(200, json={"result": mid})
            if method == "action_post":
                self.posted_move_ids.extend(args[0])
                return httpx.Response(200, json={"result": True})
        return httpx.Response(200, json={"result": False})

    def client(self, **overrides: object) -> HttpOdooClient:
        kwargs: dict[str, object] = {
            "url": "https://odoo-sandbox.example.com",
            "db": "resto_sandbox",
            "username": "svc-restaurant-api",
            "api_key": "sandbox-key",
            "retry_backoff_seconds": 0.0,
            "transport": httpx.MockTransport(self.handler),
        }
        kwargs.update(overrides)
        return HttpOdooClient(**kwargs)  # type: ignore[arg-type]


async def _scenario(
    session: AsyncSession, tenant: Tenant, store: Store
) -> tuple[Supplier, PurchaseOrder]:
    """Mission scenario: 1 supplier, 1 PO with 2 lines, received."""
    sup = Supplier(
        tenant_id=tenant.id,
        code="SUP-E2E",
        name="E2E 食材供應商",
        tax_id="24567890",
        payment_terms="月結30",
    )
    session.add(sup)
    ing1 = Ingredient(tenant_id=tenant.id, name="雞胸肉", unit="kg")
    ing2 = Ingredient(tenant_id=tenant.id, name="太白粉", unit="kg")
    session.add_all([ing1, ing2])
    await session.flush()
    po = PurchaseOrder(
        tenant_id=tenant.id,
        store_id=store.id,
        supplier_id=sup.id,
        po_number=f"PO-E2E-{uuid.uuid4().hex[:6]}",
        ordered_at=datetime.now(UTC),
        received_at=datetime.now(UTC),
        status=PurchaseOrderStatus.RECEIVED,
        subtotal=Decimal("2400"),
        tax_amount=Decimal("120"),
        total=Decimal("2520"),
        invoice_number="AB-87654321",
    )
    session.add(po)
    await session.flush()
    session.add_all(
        [
            PurchaseOrderLine(
                tenant_id=tenant.id,
                store_id=store.id,
                purchase_order_id=po.id,
                ingredient_id=ing1.id,
                quantity=Decimal("10"),
                unit="kg",
                unit_price=Decimal("180"),
                line_total=Decimal("1800"),
            ),
            PurchaseOrderLine(
                tenant_id=tenant.id,
                store_id=store.id,
                purchase_order_id=po.id,
                ingredient_id=ing2.id,
                quantity=Decimal("20"),
                unit="kg",
                unit_price=Decimal("30"),
                line_total=Decimal("600"),
            ),
        ]
    )
    await session.flush()
    return sup, po


async def test_e2e_receipt_to_draft_vendor_bill_with_rerun_and_crash_replay(
    db_session: AsyncSession, seed_tenant: Tenant, seed_store: Store
) -> None:
    fake = FakeOdooServer()
    odoo = fake.client()
    sup, po = await _scenario(db_session, seed_tenant, seed_store)

    report = await sync_purchase_orders(tenant_id=seed_tenant.id, session=db_session, odoo=odoo)
    assert report.pushed == 1 and report.failed == 0

    # 1. supplier upserted with ref + 統編
    partner_ids = [pid for pid, v in fake.partners.items() if v.get("ref") == "SUP-E2E"]
    assert len(partner_ids) == 1
    assert fake.partners[partner_ids[0]]["vat"] == "24567890"

    # 2. vendor bill exists and REMAINS DRAFT (never posted)
    assert len(fake.moves) == 1
    move_id, move = next(iter(fake.moves.items()))
    assert move["state"] == "draft"
    assert fake.posted_move_ids == []
    assert move["move_type"] == "in_invoice"
    # 2b. the bill is bound to the upserted supplier partner and carries the
    #     controlled invoice date (the local receiving date)
    assert move["partner_id"] == partner_ids[0]
    assert move["invoice_date"] == move["date"]

    # 3. debits equal credits on the wire
    lines = [cmd[2] for cmd in move["line_ids"]]
    assert sum(ln["debit"] for ln in lines) == sum(ln["credit"] for ln in lines) == 2520.0

    # 4. journal is the purchase journal
    assert move["journal_id"] == FakeOdooServer.JOURNALS["PUR"]

    # 5. tax carried as an explicit input-VAT line (Odoo tax objects unconfigured)
    assert any(
        ln["debit"] == 120.0 and ln_acct == FakeOdooServer.ACCOUNTS["1360"]
        for ln, ln_acct in ((ln, ln["account_id"]) for ln in lines)
    )

    # 5b. the wire payload is Odoo-native invoice lines — NOT raw journal items,
    #     and NO manual payable line; Odoo generated the AP counterpart.
    payload = fake.move_create_payloads[0]
    assert "invoice_line_ids" in payload and "line_ids" not in payload
    inv_lines = [cmd[2] for cmd in payload["invoice_line_ids"]]
    assert all(ln["price_unit"] > 0 for ln in inv_lines)
    assert FakeOdooServer.ACCOUNTS["2100"] not in [ln["account_id"] for ln in inv_lines]
    assert sum(ln["price_unit"] * ln["quantity"] for ln in inv_lines) == 2520.0
    payable = [cmd[2] for cmd in move["line_ids"] if cmd[2].get("auto_generated")]
    assert len(payable) == 1 and payable[0]["credit"] == 2520.0  # Odoo-generated

    # 6. source linkage recorded on both sides
    assert po.odoo_move_id == str(move_id)
    assert po.odoo_synced_at is not None
    # tenant-qualified marker — a PO number alone could repeat across tenants
    assert f"[src:purchase_order:{seed_tenant.id}:{po.id}]" in str(move["ref"])

    # 7a. plain re-run: nothing new pushed
    report2 = await sync_purchase_orders(tenant_id=seed_tenant.id, session=db_session, odoo=odoo)
    assert report2.considered == 0
    assert len(fake.moves) == 1

    # 7b. crash replay: stamp lost (crash between Odoo-create and DB-commit)
    #     -> client-side find-before-create dedups, still exactly one move
    po.odoo_move_id = None
    po.odoo_synced_at = None
    await db_session.flush()
    report3 = await sync_purchase_orders(tenant_id=seed_tenant.id, session=db_session, odoo=odoo)
    assert report3.pushed == 1
    assert len(fake.moves) == 1  # NO duplicate draft
    assert po.odoo_move_id == str(move_id)  # re-stamped with the SAME move

    await odoo.aclose()

    # optional evidence dump for the certification run
    evidence_path = os.environ.get("ODOO_CERT_EVIDENCE_PATH")
    if evidence_path:
        with open(evidence_path, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "mode": "SIMULATED_ODOO_JSONRPC_CONTRACT",
                    "live_odoo": "EXTERNAL_DEPENDENCY_BLOCKED (no instance in environment)",
                    "tenant_id": str(seed_tenant.id),
                    "store_id": str(seed_store.id),
                    "supplier_id": str(sup.id),
                    "supplier_ref": "SUP-E2E",
                    "purchase_order_id": str(po.id),
                    "po_number": po.po_number,
                    "po_lines": 2,
                    "odoo_partner_id": partner_ids[0],
                    "odoo_move_id": move_id,
                    "move_state": "draft",
                    "move_ref": str(move["ref"]),
                    "balanced_total": 2520.0,
                    "journal_id": move["journal_id"],
                    "rerun_duplicates": 0,
                    "posted_moves": 0,
                },
                fh,
                indent=2,
            )


async def test_e2e_timeout_retry_creates_no_duplicate(
    db_session: AsyncSession, seed_tenant: Tenant, seed_store: Store
) -> None:
    fake = FakeOdooServer()
    odoo = fake.client()
    await _scenario(db_session, seed_tenant, seed_store)

    fake.fail_creates_with_timeout = 1  # first create times out, retry succeeds
    report = await sync_purchase_orders(tenant_id=seed_tenant.id, session=db_session, odoo=odoo)
    assert report.pushed == 1 and report.failed == 0
    assert len(fake.moves) == 1  # retried safely, exactly one draft
    await odoo.aclose()


async def test_e2e_move_created_but_response_lost_recovers_without_duplicate(
    db_session: AsyncSession, seed_tenant: Tenant, seed_store: Store
) -> None:
    """MANDATORY variant: Odoo commits the vendor bill but the response dies.

    The client must NOT blind-recreate: it searches the source marker, finds
    the server-committed move, and the local PO is stamped with that exact id.
    """
    fake = FakeOdooServer()
    odoo = fake.client()
    _, po = await _scenario(db_session, seed_tenant, seed_store)

    fake.drop_move_create_response = 1  # commit server-side, then lose response
    report = await sync_purchase_orders(tenant_id=seed_tenant.id, session=db_session, odoo=odoo)
    assert report.pushed == 1 and report.failed == 0

    move_creates = [r for r in fake.request_log if r == ("account.move", "create")]
    assert len(move_creates) == 1  # create was NEVER re-sent
    assert len(fake.moves) == 1  # exactly one stored move
    stored_move_id = next(iter(fake.moves))
    assert po.odoo_move_id == str(stored_move_id)  # recovered id == stored id
    assert fake.moves[stored_move_id]["state"] == "draft"
    assert fake.posted_move_ids == []
    await odoo.aclose()


async def test_e2e_persistent_move_timeout_fails_bounded_into_retry_path(
    db_session: AsyncSession, seed_tenant: Tenant, seed_store: Store
) -> None:
    """Create times out every attempt and the server never commits: the client
    fails after a bounded number of creates, and the PO takes one failure
    attempt toward dead-letter — no unbounded retry, no phantom move."""
    fake = FakeOdooServer()
    odoo = fake.client()
    _, po = await _scenario(db_session, seed_tenant, seed_store)

    fake.fail_move_creates_with_timeout = 99  # every account.move create dies
    report = await sync_purchase_orders(tenant_id=seed_tenant.id, session=db_session, odoo=odoo)
    assert report.pushed == 0 and report.failed == 1

    move_creates = [r for r in fake.request_log if r == ("account.move", "create")]
    assert len(move_creates) == 4  # default max_retries=3 -> 4 bounded attempts
    assert len(fake.moves) == 0
    assert po.odoo_synced_at is None
    assert po.odoo_sync_attempts == 1  # one failure attempt toward dead-letter
    assert po.odoo_last_sync_error is not None
    await odoo.aclose()


async def test_e2e_forbidden_operations_never_reach_the_server(
    db_session: AsyncSession, seed_tenant: Tenant, seed_store: Store
) -> None:
    fake = FakeOdooServer()
    odoo = fake.client()

    with pytest.raises(OperationNotAllowedError):
        await odoo._execute_kw("account.move", "unlink", [[1]])
    with pytest.raises(OperationNotAllowedError):
        await odoo._execute_kw("account.payment", "create", [{}])
    with pytest.raises(OperationNotAllowedError):
        await odoo._execute_kw("account.move", "action_register_payment", [[1]])
    with pytest.raises(ModelNotAllowedError):
        await odoo._execute_kw("res.users", "search_read", [[]])
    with pytest.raises(ModelNotAllowedError):
        await odoo._execute_kw("ir.config_parameter", "search_read", [[]])

    # none of the denied operations produced network traffic
    assert fake.request_log == []
    await odoo.aclose()


async def test_e2e_secrets_absent_from_logs(
    db_session: AsyncSession, seed_tenant: Tenant, seed_store: Store, caplog
) -> None:
    fake = FakeOdooServer()
    secret = "sandbox-key"
    odoo = fake.client(api_key=secret)
    await _scenario(db_session, seed_tenant, seed_store)
    with caplog.at_level("DEBUG"):
        await sync_purchase_orders(tenant_id=seed_tenant.id, session=db_session, odoo=odoo)
    assert secret not in caplog.text
    assert secret not in repr(odoo)
    await odoo.aclose()


async def test_fake_odoo_rejects_legacy_raw_vendor_bill_payload() -> None:
    """The fake models Odoo 17: a vendor bill sent as raw journal ``line_ids``
    (the pre-remediation payload) is refused as unbalanced, not silently
    accepted. This guards against a regression back to the broken shape."""
    fake = FakeOdooServer()
    legacy_payload = {
        "move_type": "in_invoice",
        "partner_id": 501,
        "journal_id": FakeOdooServer.JOURNALS["PUR"],
        "ref": "進貨 AB-1 [src:purchase_order:t:po]",
        "line_ids": [
            (0, 0, {"account_id": 11, "name": "存貨", "debit": 1000.0, "credit": 0.0}),
            (0, 0, {"account_id": 13, "name": "應付", "debit": 0.0, "credit": 1000.0}),
        ],
    }
    request = httpx.Request(
        "POST", "https://odoo-sandbox.example.com/jsonrpc",
        json={"params": {"service": "object", "method": "execute_kw",
                         "args": ["db", 7, "k", "account.move", "create", [legacy_payload], {}]}},
    )
    resp = fake.handler(request)
    body = resp.json()
    assert "error" in body  # rejected, not accepted
    assert fake.moves == {}  # nothing stored
