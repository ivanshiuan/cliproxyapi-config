"""Tests for the Odoo back-office integration.

Everything here runs against the in-memory stub or an ``httpx.MockTransport``
-- no live Odoo is needed, exactly like ``test_line_integration.py``.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import date
from decimal import Decimal
from unittest.mock import patch

import httpx
import pytest

from restaurant_api.config import get_settings
from restaurant_api.integrations.odoo import (
    AccountChart,
    DailySales,
    HttpOdooClient,
    InvalidPartnerError,
    JournalEntry,
    JournalLine,
    ModelNotAllowedError,
    OdooApiError,
    OdooClient,
    OdooTransientError,
    OperationNotAllowedError,
    PayrollAccrual,
    PostingNotPermittedError,
    PurchaseBill,
    StubOdooClient,
    SupplierRecord,
    UnbalancedEntryError,
    UnsupportedCurrencyError,
    WasteLoss,
    daily_sales_journal,
    enforce_operation_policy,
    get_odoo,
    payroll_journal,
    purchase_to_vendor_bill,
    waste_loss_journal,
)
from restaurant_api.integrations.odoo.client import reset_odoo

# ── posting builders: balance + correctness ────────────────────────────────


def test_purchase_bill_builds_balanced_vendor_bill():
    entry = purchase_to_vendor_bill(
        PurchaseBill(
            source_id="po-1",
            supplier_ref="SUP-C",
            invoice_number="AB-12345678",
            occurred_on=date(2026, 7, 28),
            subtotal=Decimal("1000"),
            tax_amount=Decimal("50"),
        )
    )
    assert entry.is_balanced()
    assert entry.total_debit() == Decimal("1050")
    assert entry.move_type == "in_invoice"
    assert entry.external_id == "po-1"  # idempotency key
    assert entry.partner_ref == "SUP-C"
    # inventory + input VAT on the debit side, AP on the credit side
    debits = {ln.account_code: ln.debit for ln in entry.lines if ln.debit > 0}
    credits = {ln.account_code: ln.credit for ln in entry.lines if ln.credit > 0}
    assert debits == {"1310": Decimal("1000"), "1360": Decimal("50")}
    assert credits == {"2100": Decimal("1050")}


def test_purchase_bill_without_tax_omits_vat_line():
    entry = purchase_to_vendor_bill(
        PurchaseBill(
            source_id="po-2",
            supplier_ref="SUP-D",
            invoice_number="ZZ-00000001",
            occurred_on=date(2026, 7, 28),
            subtotal=Decimal("500"),
        )
    )
    assert entry.is_balanced()
    assert len(entry.lines) == 2  # no zero-value VAT line


def test_daily_sales_journal_balances():
    entry = daily_sales_journal(
        DailySales(
            source_id="store-1:2026-07-28",
            occurred_on=date(2026, 7, 28),
            net_revenue=Decimal("8000"),
            tax_amount=Decimal("400"),
        )
    )
    assert entry.is_balanced()
    assert entry.total_debit() == Decimal("8400")
    assert entry.journal_code == "SAL"


def test_waste_and_payroll_journals_balance():
    waste = waste_loss_journal(
        WasteLoss(source_id="w-1", occurred_on=date(2026, 7, 28), amount=Decimal("300"))
    )
    payroll = payroll_journal(
        PayrollAccrual(
            source_id="pay-1", occurred_on=date(2026, 7, 28), gross_wages=Decimal("50000")
        )
    )
    assert waste.is_balanced() and payroll.is_balanced()
    assert waste.total_debit() == Decimal("300")
    assert payroll.total_credit() == Decimal("50000")


def test_custom_chart_overrides_account_codes():
    chart = AccountChart(inventory="X1", accounts_payable="X2")
    entry = purchase_to_vendor_bill(
        PurchaseBill(
            source_id="po-3",
            supplier_ref="S",
            invoice_number="I",
            occurred_on=date(2026, 7, 28),
            subtotal=Decimal("10"),
        ),
        chart,
    )
    codes = {ln.account_code for ln in entry.lines}
    assert codes == {"X1", "X2"}


def test_money_inputs_reject_float():
    with pytest.raises(TypeError):
        PurchaseBill(
            source_id="x",
            supplier_ref="s",
            invoice_number="i",
            occurred_on=date(2026, 7, 28),
            subtotal=1000.0,  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError):
        JournalLine("1310", debit=1.5)  # type: ignore[arg-type]


def test_journal_line_rejects_two_sided_and_empty_lines():
    with pytest.raises(ValueError):
        JournalLine("1310", debit=Decimal("1"), credit=Decimal("1"))
    with pytest.raises(ValueError):
        JournalLine("1310")  # both zero
    with pytest.raises(ValueError):
        JournalLine("1310", debit=Decimal("-1"))


def test_assert_balanced_raises_on_unbalanced_entry():
    bad = JournalEntry(
        external_id="bad",
        entry_date=date(2026, 7, 28),
        journal_code="MISC",
        ref="x",
        move_type="entry",
        lines=(
            JournalLine("1310", debit=Decimal("100")),
            JournalLine("2100", credit=Decimal("90")),
        ),
    )
    assert not bad.is_balanced()
    with pytest.raises(UnbalancedEntryError):
        bad.assert_balanced()


# ── StubOdooClient ─────────────────────────────────────────────────────────


def _bill() -> JournalEntry:
    return purchase_to_vendor_bill(
        PurchaseBill(
            source_id="po-9",
            supplier_ref="SUP-C",
            invoice_number="AB-1",
            occurred_on=date(2026, 7, 28),
            subtotal=Decimal("100"),
            tax_amount=Decimal("5"),
        )
    )


def test_stub_creates_draft_vendor_bill_by_default():
    stub = StubOdooClient()
    rec_id = asyncio.run(stub.create_vendor_bill(_bill(), partner_id=77))
    assert rec_id == "1"
    call = stub.calls[0]
    assert call["op"] == "create_vendor_bill"
    assert call["external_id"] == "po-9"
    assert call["partner_id"] == 77  # bound to the controlled supplier partner
    assert call["posted"] is False  # draft -- not posted to ledger


def test_stub_blocks_posting_without_auto_post():
    stub = StubOdooClient(allow_auto_post=False)
    with pytest.raises(PostingNotPermittedError):
        asyncio.run(stub.create_vendor_bill(_bill(), partner_id=77, post=True))


def test_stub_allows_posting_when_enabled():
    stub = StubOdooClient(allow_auto_post=True)
    asyncio.run(stub.create_vendor_bill(_bill(), partner_id=77, post=True))
    assert stub.calls[0]["posted"] is True


def test_stub_upsert_supplier_is_idempotent_on_ref():
    stub = StubOdooClient()
    a = asyncio.run(stub.upsert_supplier(SupplierRecord(ref="SUP-C", name="C 食材")))
    b = asyncio.run(stub.upsert_supplier(SupplierRecord(ref="SUP-C", name="C 食材行")))
    assert a == b  # same ref -> same Odoo id


def test_stub_ap_aging_filters_by_supplier():
    stub = StubOdooClient()
    stub.ap_rows = [
        {"partner_ref": "SUP-C", "amount_residual": 1050},
        {"partner_ref": "SUP-D", "amount_residual": 200},
    ]
    rows = asyncio.run(stub.get_ap_aging("SUP-C"))
    assert len(rows) == 1 and rows[0]["partner_ref"] == "SUP-C"


# ── HttpOdooClient: JSON-RPC mechanics (mocked transport) ───────────────────


def _http_client(handler, *, allow_auto_post: bool = False) -> HttpOdooClient:
    return HttpOdooClient(
        url="https://odoo.example.com",
        db="resto",
        username="svc@resto",
        api_key="test-key",
        allow_auto_post=allow_auto_post,
        transport=httpx.MockTransport(handler),
    )


def _odoo_handler(*, accounts=None, journals=None, created_id=42):
    """A stateful Odoo JSON-RPC mock: auth, code->id lookups, then create."""
    accounts = accounts or {"1310": 11, "1360": 12, "2100": 13}
    journals = journals or {"PUR": 3, "SAL": 4, "MISC": 5}
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        params = body["params"]
        if params["service"] == "common" and params["method"] == "authenticate":
            seen["authenticated"] = True
            return httpx.Response(200, json={"result": 7})
        # object.execute_kw: args = [db, uid, key, model, method, [args], {kwargs}]
        args = params["args"]
        model, method, model_args = args[3], args[4], args[5]
        if model == "account.journal":
            code = model_args[0][0][2]
            return httpx.Response(200, json={"result": [{"id": journals[code]}]})
        if model == "account.account":
            code = model_args[0][0][2]
            return httpx.Response(200, json={"result": [{"id": accounts[code]}]})
        if model == "account.move" and method == "create":
            seen["created_vals"] = model_args[0]
            return httpx.Response(200, json={"result": created_id})
        return httpx.Response(200, json={"result": True})

    return handler, seen


def test_http_create_vendor_bill_sends_balanced_move():
    handler, seen = _odoo_handler()

    async def _run() -> str:
        client = _http_client(handler)
        try:
            return await client.create_vendor_bill(_bill(), partner_id=13)
        finally:
            await client.aclose()

    move_id = asyncio.run(_run())
    assert move_id == "42"
    assert seen["authenticated"] is True
    vals = seen["created_vals"]
    assert vals["move_type"] == "in_invoice"
    assert vals["journal_id"] == 3
    assert vals["partner_id"] == 13  # supplier binding travels on the wire
    assert vals["invoice_date"] == "2026-07-28"  # controlled business date
    # debits equal credits on the wire too
    total_debit = sum(cmd[2]["debit"] for cmd in vals["line_ids"])
    total_credit = sum(cmd[2]["credit"] for cmd in vals["line_ids"])
    assert total_debit == total_credit == 105.0


def test_http_execute_kw_blocks_disallowed_model():
    handler, _ = _odoo_handler()
    client = _http_client(handler)

    async def _run():
        try:
            await client._execute_kw("res.users", "search", [[]])
        finally:
            await client.aclose()

    with pytest.raises(ModelNotAllowedError):
        asyncio.run(_run())


def test_http_blocks_posting_without_auto_post():
    handler, _ = _odoo_handler()
    client = _http_client(handler, allow_auto_post=False)
    with pytest.raises(PostingNotPermittedError):
        asyncio.run(client.create_vendor_bill(_bill(), partner_id=13, post=True))


def test_http_raises_on_jsonrpc_error_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"error": {"data": {"message": "Access Denied"}}},
        )

    client = _http_client(handler)

    async def _run():
        try:
            await client.get_ap_aging()
        finally:
            await client.aclose()

    with pytest.raises(OdooApiError) as excinfo:
        asyncio.run(_run())
    assert "Access Denied" in str(excinfo.value)


def test_http_raises_on_non_2xx():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    client = _http_client(handler)

    async def _run():
        try:
            await client.get_ap_aging()
        finally:
            await client.aclose()

    with pytest.raises(OdooApiError):
        asyncio.run(_run())


# ── DI switch + contract ───────────────────────────────────────────────────


def test_get_odoo_returns_stub_without_env():
    reset_odoo()
    get_settings.cache_clear()
    with patch.dict(os.environ, {}, clear=False):
        for key in ("ODOO_URL", "ODOO_DB", "ODOO_USERNAME", "ODOO_API_KEY"):
            os.environ.pop(key, None)
        assert isinstance(get_odoo(), StubOdooClient)
    reset_odoo()
    get_settings.cache_clear()


def test_get_odoo_returns_http_when_env_set():
    reset_odoo()
    get_settings.cache_clear()
    env = {
        "ODOO_URL": "https://odoo.example.com",
        "ODOO_DB": "resto",
        "ODOO_USERNAME": "svc@resto",
        "ODOO_API_KEY": "k",
    }
    with patch.dict(os.environ, env):
        client = get_odoo()
        assert isinstance(client, HttpOdooClient)
        assert client.db == "resto"
    reset_odoo()
    get_settings.cache_clear()


def test_odoo_client_is_abstract():
    with pytest.raises(TypeError):
        OdooClient()  # type: ignore[abstract]


# ── operation policy: the permission boundary, attempted adversarially ──────


def _no_egress_handler(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"policy violation reached the wire: {request.url}")


def test_policy_blocks_unlink_even_on_allowed_model():
    """unlink (delete) is always denied — before any request is sent."""
    client = _http_client(_no_egress_handler)
    with pytest.raises(OperationNotAllowedError):
        asyncio.run(client._execute_kw("account.move", "unlink", [[1]]))


def test_policy_blocks_payment_registration():
    """account.payment is read-only: create/write/action are all denied."""
    client = _http_client(_no_egress_handler)
    for method in ("create", "write", "action_register_payment"):
        with pytest.raises((OperationNotAllowedError, ModelNotAllowedError)):
            asyncio.run(client._execute_kw("account.payment", method, [{}]))


def test_policy_blocks_arbitrary_model_and_settings():
    for model in ("res.users", "ir.config_parameter", "res.groups", "ir.model.access"):
        with pytest.raises(ModelNotAllowedError):
            asyncio.run(_http_client(_no_egress_handler)._execute_kw(model, "search_read", [[]]))


def test_policy_blocks_unlisted_write_method():
    """A write method not explicitly whitelisted is denied (e.g. button_cancel)."""
    client = _http_client(_no_egress_handler)
    with pytest.raises(OperationNotAllowedError):
        asyncio.run(client._execute_kw("account.move", "button_cancel", [[1]]))


def test_policy_blocks_disallowed_journal():
    """Moves may only target PUR/SAL/MISC — never bank/cash journals."""
    bank_entry = JournalEntry(
        external_id="x-1",
        entry_date=date(2026, 7, 28),
        journal_code="BANK",
        ref="x",
        move_type="entry",
        lines=(
            JournalLine("1100", debit=Decimal("1")),
            JournalLine("4100", credit=Decimal("1")),
        ),
    )
    with pytest.raises(OperationNotAllowedError):
        asyncio.run(StubOdooClient().post_journal_entry(bank_entry))
    with pytest.raises(OperationNotAllowedError):
        asyncio.run(_http_client(_no_egress_handler).post_journal_entry(bank_entry))


def test_enforce_operation_policy_direct():
    enforce_operation_policy("res.partner", "search_read")  # read: ok
    enforce_operation_policy("account.move", "create")  # whitelisted write: ok
    with pytest.raises(OperationNotAllowedError):
        enforce_operation_policy("res.partner", "unlink")
    with pytest.raises(ModelNotAllowedError):
        enforce_operation_policy("res.users", "read")


# ── retry: bounded exponential backoff on transient failures only ───────────


def test_http_retries_transient_timeout_then_succeeds():
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] <= 2:
            raise httpx.ConnectTimeout("simulated timeout")
        return httpx.Response(200, json={"result": 7})

    client = HttpOdooClient(
        url="https://odoo.example.com",
        db="resto",
        username="svc",
        api_key="k",
        retry_backoff_seconds=0.0,
        transport=httpx.MockTransport(handler),
    )

    async def _run() -> object:
        try:
            return await client._rpc("common", "authenticate", [])
        finally:
            await client.aclose()

    assert asyncio.run(_run()) == 7
    assert attempts["n"] == 3  # two failures + one success


def test_http_retry_exhaustion_raises_odoo_api_error():
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        raise httpx.ConnectTimeout("down")

    client = HttpOdooClient(
        url="https://odoo.example.com",
        db="resto",
        username="svc",
        api_key="k",
        max_retries=2,
        retry_backoff_seconds=0.0,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(OdooApiError, match="unreachable after 3 attempts"):
        asyncio.run(client._rpc("common", "authenticate", []))
    assert attempts["n"] == 3


def test_http_does_not_retry_application_errors():
    """4xx and JSON-RPC errors are permanent — exactly one attempt."""
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(403, text="forbidden")

    client = _http_client(handler)
    with pytest.raises(OdooApiError):
        asyncio.run(client._rpc("common", "authenticate", []))
    assert attempts["n"] == 1


# ── secret handling ─────────────────────────────────────────────────────────


def test_api_key_never_appears_in_repr_or_errors_or_logs(caplog):
    secret = "SECRET-API-KEY-XYZZY"
    client = HttpOdooClient(
        url="https://odoo.example.com",
        db="resto",
        username="svc",
        api_key=secret,
        max_retries=1,
        retry_backoff_seconds=0.0,
        transport=httpx.MockTransport(lambda r: httpx.Response(500, text="boom")),
    )
    assert secret not in repr(client)
    assert secret not in str(client)
    with caplog.at_level("DEBUG"), pytest.raises(OdooApiError) as excinfo:
        asyncio.run(client._rpc("common", "authenticate", []))
    assert secret not in str(excinfo.value)
    assert secret not in caplog.text


# ── duplicate guard at the client layer ─────────────────────────────────────


def test_stub_dedups_same_external_id():
    stub = StubOdooClient()
    first = asyncio.run(stub.create_vendor_bill(_bill(), partner_id=77))
    second = asyncio.run(stub.create_vendor_bill(_bill(), partner_id=77))
    assert first == second  # same external_id -> same move, no duplicate
    dedups = [c for c in stub.calls if c.get("dedup")]
    assert len(dedups) == 1


def test_http_create_embeds_src_marker_and_dedups():
    """The move ref carries [src:<id>]; a pre-existing match short-circuits create."""
    created: list[dict[str, object]] = []
    existing_moves: dict[int, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        params = body["params"]
        if params["service"] == "common":
            return httpx.Response(200, json={"result": 7})
        args = params["args"]
        model, method, model_args = args[3], args[4], args[5]
        if model == "account.move" and method == "search_read":
            needle = model_args[0][0][2]
            hits = [{"id": mid} for mid, ref in existing_moves.items() if needle in ref]
            return httpx.Response(200, json={"result": hits[:1]})
        if model == "account.journal":
            return httpx.Response(200, json={"result": [{"id": 3}]})
        if model == "account.account":
            return httpx.Response(200, json={"result": [{"id": 11}]})
        if model == "account.move" and method == "create":
            created.append(model_args[0])
            mid = 900 + len(created)
            existing_moves[mid] = str(model_args[0]["ref"])
            return httpx.Response(200, json={"result": mid})
        return httpx.Response(200, json={"result": []})

    async def _run() -> tuple[str, str]:
        client = _http_client(handler)
        try:
            a = await client.create_vendor_bill(_bill(), partner_id=13)
            b = await client.create_vendor_bill(_bill(), partner_id=13)  # same external_id
            return a, b
        finally:
            await client.aclose()

    a, b = asyncio.run(_run())
    assert a == b
    assert len(created) == 1  # second call deduped, no second create
    assert "[src:po-9]" in str(created[0]["ref"])


# ── partner binding: controlled, mandatory, non-injectable ──────────────────


def test_vendor_bill_rejects_missing_or_invalid_partner_before_egress():
    """in_invoice without a positive-int partner id is refused pre-egress."""
    stub = StubOdooClient()
    http = _http_client(_no_egress_handler)
    for bad in (None, 0, -3, True):
        with pytest.raises(InvalidPartnerError):
            asyncio.run(stub.create_vendor_bill(_bill(), partner_id=bad))  # type: ignore[arg-type]
        with pytest.raises(InvalidPartnerError):
            asyncio.run(http.create_vendor_bill(_bill(), partner_id=bad))  # type: ignore[arg-type]
    # the http handler asserts on any request, so reaching here proves 0 egress


def test_po_payload_cannot_inject_partner_id():
    """PurchaseBill / JournalEntry deliberately carry no partner-id field, so
    a PO payload cannot smuggle one in — it must come from upsert_supplier."""
    with pytest.raises(TypeError):
        PurchaseBill(  # type: ignore[call-arg]
            source_id="po-9",
            supplier_ref="SUP-C",
            invoice_number="AB-1",
            occurred_on=date(2026, 7, 28),
            subtotal=Decimal("100"),
            partner_id=999,
        )
    with pytest.raises(TypeError):
        JournalEntry(  # type: ignore[call-arg]
            external_id="x",
            entry_date=date(2026, 7, 28),
            journal_code="PUR",
            ref="x",
            move_type="in_invoice",
            lines=(JournalLine("1310", debit=Decimal("1")),),
            partner_id=999,
        )


# ── currency fence: TWD only, refused before egress ─────────────────────────


def _entry_with_currency(code: str) -> JournalEntry:
    return JournalEntry(
        external_id="cur-1",
        entry_date=date(2026, 7, 28),
        journal_code="PUR",
        ref="cur",
        move_type="in_invoice",
        lines=(
            JournalLine("1310", debit=Decimal("10")),
            JournalLine("2100", credit=Decimal("10")),
        ),
        currency_code=code,
    )


def test_currency_guard_allows_twd():
    stub = StubOdooClient()
    move = asyncio.run(stub.create_vendor_bill(_entry_with_currency("TWD"), partner_id=77))
    assert move == "1"


def test_currency_guard_blocks_non_twd_before_egress():
    secret = "SECRET-API-KEY-XYZZY"
    http = HttpOdooClient(
        url="https://odoo.example.com",
        db="resto",
        username="svc",
        api_key=secret,
        transport=httpx.MockTransport(_no_egress_handler),
    )
    stub = StubOdooClient()
    for code in ("USD", "CNY", ""):
        with pytest.raises(UnsupportedCurrencyError) as exc_stub:
            asyncio.run(stub.create_vendor_bill(_entry_with_currency(code), partner_id=77))
        with pytest.raises(UnsupportedCurrencyError) as exc_http:
            asyncio.run(http.create_vendor_bill(_entry_with_currency(code), partner_id=77))
        assert secret not in str(exc_http.value)
        assert secret not in str(exc_stub.value)
    # _no_egress_handler raises AssertionError on any request: 0 requests sent


# ── create retry: no blind re-send, search-based recovery ───────────────────


def _recovery_handler(*, moves: dict[int, str], behavior: dict[str, object]):
    """JSON-RPC mock whose account.move.create can time out or return garbage,
    optionally AFTER committing the move server-side (`server_creates=True`)."""
    counts = {"create": 0, "search": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        params = body["params"]
        if params["service"] == "common":
            return httpx.Response(200, json={"result": 7})
        args = params["args"]
        model, method, model_args = args[3], args[4], args[5]
        if model == "account.journal":
            return httpx.Response(200, json={"result": [{"id": 3}]})
        if model == "account.account":
            return httpx.Response(200, json={"result": [{"id": 11}]})
        if model == "account.move" and method == "search_read":
            counts["search"] += 1
            needle = model_args[0][0][2]
            hits = [{"id": mid} for mid, ref in moves.items() if needle in ref]
            return httpx.Response(200, json={"result": hits[:1]})
        if model == "account.move" and method == "create":
            counts["create"] += 1
            fail_n = int(behavior.get("fail_first_n", 0))
            if counts["create"] <= fail_n:
                if behavior.get("server_creates"):
                    mid = 900 + counts["create"]
                    moves[mid] = str(model_args[0]["ref"])
                mode = behavior.get("mode", "timeout")
                if mode == "malformed":
                    return httpx.Response(200, text="<html>gateway mangled this</html>")
                raise httpx.ReadTimeout("response lost")
            mid = 900 + counts["create"]
            moves[mid] = str(model_args[0]["ref"])
            return httpx.Response(200, json={"result": mid})
        return httpx.Response(200, json={"result": []})

    return handler, counts


def test_http_create_response_lost_recovers_existing_move_without_recreate():
    """Server commits the move, response is lost -> recovery search finds it;
    exactly ONE create ever reaches the server."""
    moves: dict[int, str] = {}
    handler, counts = _recovery_handler(
        moves=moves, behavior={"fail_first_n": 1, "server_creates": True}
    )

    async def _run() -> str:
        client = _http_client(handler)
        try:
            return await client.create_vendor_bill(_bill(), partner_id=13)
        finally:
            await client.aclose()

    move_id = asyncio.run(_run())
    assert counts["create"] == 1  # never re-sent
    assert len(moves) == 1
    assert move_id == str(next(iter(moves)))


def test_http_create_timeout_without_server_create_recreates_once():
    """Timeout where the server never created -> search finds nothing ->
    exactly one more create succeeds. Total 2 creates, 1 stored move."""
    moves: dict[int, str] = {}
    handler, counts = _recovery_handler(
        moves=moves, behavior={"fail_first_n": 1, "server_creates": False}
    )

    async def _run() -> str:
        client = _http_client(handler)
        try:
            return await client.create_vendor_bill(_bill(), partner_id=13)
        finally:
            await client.aclose()

    move_id = asyncio.run(_run())
    assert counts["create"] == 2
    assert len(moves) == 1
    assert move_id == str(next(iter(moves)))


def test_http_create_persistent_timeout_is_bounded_and_raises():
    """Create always times out and search never finds a move -> bounded
    attempts (max_retries+1 creates, one recovery search each) then raise."""
    moves: dict[int, str] = {}
    handler, counts = _recovery_handler(
        moves=moves, behavior={"fail_first_n": 99, "server_creates": False}
    )

    async def _run() -> str:
        client = _http_client(handler)
        try:
            return await client.create_vendor_bill(_bill(), partner_id=13)
        finally:
            await client.aclose()

    with pytest.raises(OdooTransientError):
        asyncio.run(_run())
    assert counts["create"] == 4  # default max_retries=3 -> 4 bounded attempts
    assert counts["search"] == 5  # 1 pre-create dedup + 4 recovery searches
    assert len(moves) == 0


def test_http_create_malformed_response_triggers_recovery_not_blind_recreate():
    """A 2xx body that fails to parse is outcome-unknown: the client must
    search the marker (finding the server-committed move) instead of blindly
    re-sending create."""
    moves: dict[int, str] = {}
    handler, counts = _recovery_handler(
        moves=moves,
        behavior={"fail_first_n": 1, "server_creates": True, "mode": "malformed"},
    )

    async def _run() -> str:
        client = _http_client(handler)
        try:
            return await client.create_vendor_bill(_bill(), partner_id=13)
        finally:
            await client.aclose()

    move_id = asyncio.run(_run())
    assert counts["create"] == 1  # the malformed-response create is never re-sent
    assert len(moves) == 1
    assert move_id == str(next(iter(moves)))


def test_http_partner_create_response_lost_recovers_by_ref():
    """Same no-blind-retry contract for res.partner: a lost create response is
    recovered via the unique ref search; no duplicate partner."""
    partners: dict[int, dict[str, object]] = {}
    counts = {"create": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        params = body["params"]
        if params["service"] == "common":
            return httpx.Response(200, json={"result": 7})
        args = params["args"]
        model, method, model_args = args[3], args[4], args[5]
        if model == "res.partner" and method == "search_read":
            ref = model_args[0][0][2]
            hits = [{"id": pid} for pid, v in partners.items() if v.get("ref") == ref]
            return httpx.Response(200, json={"result": hits[:1]})
        if model == "res.partner" and method == "create":
            counts["create"] += 1
            pid = 700 + counts["create"]
            partners[pid] = dict(model_args[0])
            if counts["create"] == 1:
                raise httpx.ReadTimeout("response lost after partner create")
            return httpx.Response(200, json={"result": pid})
        return httpx.Response(200, json={"result": []})

    async def _run() -> str:
        client = _http_client(handler)
        try:
            return await client.upsert_supplier(SupplierRecord(ref="SUP-C", name="C 食材"))
        finally:
            await client.aclose()

    partner_id = asyncio.run(_run())
    assert counts["create"] == 1  # recovered via ref search, never re-created
    assert len(partners) == 1
    assert partner_id == str(next(iter(partners)))
