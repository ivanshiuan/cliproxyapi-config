"""Odoo external-API client contract + Phase-1 stub + Phase-2 JSON-RPC backend.

This mirrors the LINE integration exactly (see ``integrations/line/messenger.py``):
an abstract contract, an in-memory ``StubOdooClient`` used in dev and every
test, a real ``HttpOdooClient`` over Odoo's JSON-RPC endpoint, and a
``get_odoo()`` DI singleton that picks the backend from the environment.

Permission model (the "how Claude/restaurant_api talk to Odoo safely" story)
is enforced in code, not left to prose:

1. *Identity*: the ``HttpOdooClient`` authenticates as one dedicated Odoo
   service user with an API key (never a password, never admin). Give that
   user a permission group scoped to Accounting + Purchase only. Auth uses
   the key exactly like a password on ``common.authenticate``.

2. *Capability allow-list*: every write goes through ``_execute_kw``, which
   refuses any model outside ``ALLOWED_MODELS``. There is no generic
   "run arbitrary model.method" surface, so an upstream caller (including
   Claude via an MCP wrapper) physically cannot reach Settings, Users, or any
   model we did not sign off on.

3. *One-time posting policy, not per-call confirmation*: writes are tiered.
   Low-risk writes (create a **draft** vendor bill, upsert a supplier) always
   run. Actually *posting* a move to the ledger is high-risk and only happens
   when the caller passes ``post=True`` AND the client was built with
   ``allow_auto_post=True``. The nightly sync leaves everything as drafts for
   human review and never passes ``post=True`` -- so flipping
   ``ODOO_ALLOW_AUTO_POST`` alone changes nothing (fail-safe); full automation
   additionally requires a deliberate code change at the call site passing
   ``post=True``. Decided once, never prompted per event.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal

import httpx

from .postings import JournalEntry

logger = logging.getLogger("restaurant_api.integrations.odoo")

# Correlates client-level retry/recovery log lines with the sync run that
# triggered them. The sync service sets this around each batch; outside a
# batch it is simply empty.
odoo_run_id: contextvars.ContextVar[str] = contextvars.ContextVar("odoo_run_id", default="")

# The only currency this bridge accepts today. Anything else is refused before
# any network egress — silently booking an unconverted foreign-currency amount
# in the company currency would be a mis-statement, not a convenience.
ALLOWED_CURRENCIES: frozenset[str] = frozenset({"TWD"})


# Models this integration is ever allowed to touch. The allow-list IS the
# permission boundary -- anything not here raises before a request is sent.
ALLOWED_MODELS: frozenset[str] = frozenset(
    {
        "res.partner",  # suppliers (AP partners)
        "account.move",  # vendor bills + journal entries
        "account.move.line",
        "account.account",  # chart of accounts (code -> id resolution)
        "account.journal",  # journals (code -> id resolution)
        "account.payment",  # READ-ONLY: payment status write-back
    }
)

# Read methods are allowed on every ALLOWED model. Everything else must be an
# explicitly whitelisted (model, method) pair -- so ``account.payment`` is
# read-only by construction, and ``unlink`` (delete) does not exist anywhere
# on this surface. This is the method-level half of the permission boundary;
# the model allow-list above is the other half.
READ_METHODS: frozenset[str] = frozenset(
    {"read", "search", "search_read", "search_count", "fields_get", "name_get"}
)

ALLOWED_WRITE_OPERATIONS: frozenset[tuple[str, str]] = frozenset(
    {
        ("res.partner", "create"),
        ("res.partner", "write"),
        ("account.move", "create"),
        # action_post is *additionally* gated behind allow_auto_post in
        # _create_move; listing it here only makes it reachable at all.
        ("account.move", "action_post"),
    }
)

# Hard denials, checked before anything else. These can never be enabled by
# configuration -- payment registration, deletion/cancellation of documents,
# and privilege escalation are outside this integration's mandate (docs/20).
FORBIDDEN_METHODS: frozenset[str] = frozenset(
    {
        "unlink",
        "button_cancel",
        "button_draft",
        "action_register_payment",
        "action_archive",
        "toggle_active",
        "sudo",
    }
)

# Journals this integration may write moves into. Bank/cash journals are
# deliberately absent: money movement is not this bridge's job.
ALLOWED_JOURNAL_CODES: frozenset[str] = frozenset({"PUR", "SAL", "MISC"})

# Move types this bridge knows how to build a wire payload for. ``in_invoice``
# is a vendor bill (Odoo-native invoice lines); ``entry`` is a miscellaneous
# journal entry (raw balanced debit/credit lines). Any other type is refused
# before egress — we will not send a shape Odoo would silently reinterpret.
SUPPORTED_MOVE_TYPES: frozenset[str] = frozenset({"in_invoice", "entry"})


class OperationNotAllowedError(PermissionError):
    """Raised when a (model, method) pair is outside the operation policy."""

    def __init__(self, model: str, method: str) -> None:
        self.model = model
        self.method = method
        super().__init__(
            f"operation {model}.{method} is not permitted by the Odoo "
            "integration policy (reads on allowed models, plus explicitly "
            "whitelisted writes only; unlink/payment/cancel are always denied)"
        )


def enforce_operation_policy(model: str, method: str) -> None:
    """The single chokepoint every Odoo call must pass.

    Order matters: forbidden methods are denied even on allowed models, then
    the model allow-list applies, then reads pass, then only whitelisted
    (model, method) writes pass.
    """
    if method in FORBIDDEN_METHODS:
        raise OperationNotAllowedError(model, method)
    if model not in ALLOWED_MODELS:
        raise ModelNotAllowedError(model)
    if method in READ_METHODS:
        return
    if (model, method) not in ALLOWED_WRITE_OPERATIONS:
        raise OperationNotAllowedError(model, method)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class OdooApiError(RuntimeError):
    """Raised when Odoo returns a JSON-RPC error or a non-2xx HTTP status."""

    def __init__(self, message: str, *, data: object | None = None) -> None:
        self.data = data
        super().__init__(message)


class OdooTransientError(OdooApiError):
    """Transient transport/server failure (timeout, connection loss, 5xx,
    malformed response) — the request MAY have succeeded server-side.

    ``_create_move`` treats this as "outcome unknown" and recovers by searching
    for the source marker instead of blindly re-sending the create.
    """


class InvalidPartnerError(ValueError):
    """Raised when an ``in_invoice`` move lacks a controlled positive partner id."""

    def __init__(self, partner_id: object) -> None:
        super().__init__(
            "a vendor bill (in_invoice) requires a positive integer partner_id "
            f"obtained from upsert_supplier; got {partner_id!r}"
        )


class UnsupportedCurrencyError(ValueError):
    """Raised before any egress when an entry's currency is not allow-listed."""

    def __init__(self, currency_code: str) -> None:
        self.currency_code = currency_code
        super().__init__(
            f"currency {currency_code!r} is not supported by the Odoo bridge "
            f"(allowed: {sorted(ALLOWED_CURRENCIES)}); refusing before egress"
        )


class ModelNotAllowedError(PermissionError):
    """Raised when a call targets a model outside ``ALLOWED_MODELS``."""

    def __init__(self, model: str) -> None:
        self.model = model
        super().__init__(f"model {model!r} is not in the Odoo integration allow-list")


class PostingNotPermittedError(PermissionError):
    """Raised when post=True is requested but auto-post is not enabled."""

    def __init__(self) -> None:
        super().__init__(
            "posting to the Odoo ledger requires allow_auto_post=True "
            "(set ODOO_ALLOW_AUTO_POST); create as draft instead"
        )


def _validate_move_inputs(entry: JournalEntry, partner_id: int | None) -> None:
    """Every pre-egress guard a move must pass, shared by stub and HTTP backend.

    Runs before any network call: balance, currency allow-list, journal fence,
    and — for vendor bills — the controlled partner binding. ``partner_id`` may
    only originate from ``upsert_supplier``'s return value; the JournalEntry
    itself deliberately has no partner-id field, so a PO payload cannot inject
    one.
    """
    entry.assert_balanced()
    if entry.currency_code not in ALLOWED_CURRENCIES:
        raise UnsupportedCurrencyError(entry.currency_code)
    if entry.move_type not in SUPPORTED_MOVE_TYPES:
        # An unknown move type would be built into a payload Odoo could
        # reinterpret (e.g. an invoice engine silently rebalancing raw lines).
        # Refuse before any network egress.
        raise OperationNotAllowedError("account.move", f"move_type:{entry.move_type}")
    if entry.journal_code not in ALLOWED_JOURNAL_CODES:
        raise OperationNotAllowedError("account.move", f"journal:{entry.journal_code}")
    if entry.move_type == "in_invoice" and (
        isinstance(partner_id, bool) or not isinstance(partner_id, int) or partner_id <= 0
    ):
        raise InvalidPartnerError(partner_id)


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SupplierRecord:
    """Minimal supplier master row pushed to Odoo ``res.partner``."""

    ref: str  # supplier code -- the upsert key
    name: str
    tax_id: str | None = None  # 統一編號
    payment_terms: str | None = None


# ---------------------------------------------------------------------------
# Abstract contract
# ---------------------------------------------------------------------------


class OdooClient(ABC):
    """The narrow Odoo surface every restaurant_api caller depends on."""

    @abstractmethod
    async def upsert_supplier(self, supplier: SupplierRecord) -> str:
        """Create or update a supplier; returns the Odoo record id (as str)."""

    @abstractmethod
    async def create_vendor_bill(
        self, entry: JournalEntry, *, partner_id: int, post: bool = False
    ) -> str:
        """Create an AP vendor bill from a balanced entry. Draft unless post=True.

        ``partner_id`` must be the id returned by ``upsert_supplier`` — the
        bill is always bound to a controlled supplier partner, never to a
        caller-chosen value.
        """

    @abstractmethod
    async def post_journal_entry(self, entry: JournalEntry, *, post: bool = False) -> str:
        """Create a miscellaneous journal entry. Draft unless post=True."""

    @abstractmethod
    async def get_ap_aging(self, supplier_ref: str | None = None) -> list[dict[str, object]]:
        """Read outstanding vendor bills (accounts payable), optionally by supplier."""

    @abstractmethod
    async def find_move(self, external_id: str) -> str | None:
        """Look up an existing move by its restaurant_api source id, or None.

        This is the duplicate guard: ``_create_move`` calls it before creating,
        so a crash between "created in Odoo" and "stamped in our DB" cannot
        produce a second draft on the next run.
        """


# ---------------------------------------------------------------------------
# Phase-1 stub -- no network, fully deterministic for tests
# ---------------------------------------------------------------------------


@dataclass
class StubOdooClient(OdooClient):
    """In-memory Odoo backend used in Phase 1 and in all tests.

    Records every call so tests can assert on it, and returns deterministic
    fake ids. Honours the same ``allow_auto_post`` gate as the HTTP backend so
    permission behaviour is testable without a live Odoo.
    """

    allow_auto_post: bool = False
    calls: list[dict[str, object]] = field(default_factory=list)
    _seq: int = 0
    # supplier ref -> fake id, so a second upsert returns the same id
    _suppliers: dict[str, str] = field(default_factory=dict)
    # external_id -> fake move id, mirroring the HTTP backend's dedup guard
    _moves_by_ext: dict[str, str] = field(default_factory=dict)
    # posted "aging" rows the test can pre-seed
    ap_rows: list[dict[str, object]] = field(default_factory=list)

    def _next_id(self) -> str:
        self._seq += 1
        return str(self._seq)

    async def find_move(self, external_id: str) -> str | None:
        return self._moves_by_ext.get(external_id)

    async def upsert_supplier(self, supplier: SupplierRecord) -> str:
        rec_id = self._suppliers.get(supplier.ref) or self._next_id()
        self._suppliers[supplier.ref] = rec_id
        self.calls.append({"op": "upsert_supplier", "supplier": supplier, "id": rec_id})
        logger.info("odoo upsert_supplier (stub) ref=%s -> %s", supplier.ref, rec_id)
        return rec_id

    def _record_move(
        self, op: str, entry: JournalEntry, post: bool, partner_id: int | None = None
    ) -> str:
        _validate_move_inputs(entry, partner_id)
        if post and not self.allow_auto_post:
            raise PostingNotPermittedError()
        existing = self._moves_by_ext.get(entry.external_id)
        if existing is not None:
            self.calls.append(
                {"op": op, "external_id": entry.external_id, "dedup": True, "id": existing}
            )
            return existing
        rec_id = self._next_id()
        self._moves_by_ext[entry.external_id] = rec_id
        self.calls.append(
            {
                "op": op,
                "external_id": entry.external_id,
                "posted": post and self.allow_auto_post,
                "entry": entry,
                "partner_id": partner_id,
                "id": rec_id,
            }
        )
        state = "posted" if (post and self.allow_auto_post) else "draft"
        logger.info("odoo %s (stub) ext=%s state=%s -> %s", op, entry.external_id, state, rec_id)
        return rec_id

    async def create_vendor_bill(
        self, entry: JournalEntry, *, partner_id: int, post: bool = False
    ) -> str:
        return self._record_move("create_vendor_bill", entry, post, partner_id)

    async def post_journal_entry(self, entry: JournalEntry, *, post: bool = False) -> str:
        return self._record_move("post_journal_entry", entry, post)

    async def get_ap_aging(self, supplier_ref: str | None = None) -> list[dict[str, object]]:
        self.calls.append({"op": "get_ap_aging", "supplier_ref": supplier_ref})
        if supplier_ref is None:
            return list(self.ap_rows)
        return [r for r in self.ap_rows if r.get("partner_ref") == supplier_ref]


# ---------------------------------------------------------------------------
# Phase-2 HTTP backend -- real Odoo JSON-RPC
# ---------------------------------------------------------------------------


def _to_wire(amount: Decimal) -> float:
    """Serialize a Decimal for Odoo's JSON-RPC monetary fields.

    This is the ONLY place a money value becomes a float, and only at the wire
    boundary: all arithmetic and the balance check already happened in Decimal.
    Odoo re-rounds monetary fields to the currency precision server-side.
    """
    return float(amount)


@dataclass
class HttpOdooClient(OdooClient):
    """Real Odoo backend over the JSON-RPC endpoint (``/jsonrpc``).

    Authenticates once as the service user (uid cached for the process) and
    routes every model call through ``_execute_kw`` so the allow-list holds.
    A single lazily-created ``httpx.AsyncClient`` is reused; ``transport`` lets
    tests inject ``httpx.MockTransport``.
    """

    url: str
    db: str
    username: str
    # repr=False: the key must never surface in tracebacks, logs, or debug
    # output that stringifies this object. It is sent only inside the JSON-RPC
    # request body, which is never logged.
    api_key: str = field(repr=False)
    allow_auto_post: bool = False
    timeout_seconds: float = 15.0
    # Bounded retry for *transient* failures (transport errors, HTTP 5xx).
    # JSON-RPC application errors and 4xx are never retried.
    max_retries: int = 3
    retry_backoff_seconds: float = 0.2
    transport: httpx.BaseTransport | None = None
    _client: httpx.AsyncClient | None = field(default=None, repr=False, compare=False)
    _uid: int | None = field(default=None, repr=False, compare=False)
    _account_ids: dict[str, int] = field(default_factory=dict, repr=False, compare=False)
    _journal_ids: dict[str, int] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_env(cls) -> HttpOdooClient:
        from ...config import get_settings

        s = get_settings()
        if not (s.odoo_url and s.odoo_db and s.odoo_username and s.odoo_api_key):
            raise RuntimeError("ODOO_URL / ODOO_DB / ODOO_USERNAME / ODOO_API_KEY must all be set")
        return cls(
            url=s.odoo_url.rstrip("/"),
            db=s.odoo_db,
            username=s.odoo_username,
            api_key=s.odoo_api_key,
            allow_auto_post=s.odoo_allow_auto_post,
        )

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.url,
                timeout=self.timeout_seconds,
                transport=self.transport,  # type: ignore[arg-type]
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _rpc(
        self,
        service: str,
        method: str,
        args: list[object],
        *,
        max_attempts: int | None = None,
    ) -> object:
        """One JSON-RPC call with bounded retry on *transient* failures only.

        ``max_attempts=1`` disables in-call retry — used for non-idempotent
        writes (``create``), whose retry is handled by search-based recovery in
        ``_create_move`` instead of blind re-send. Application errors (4xx,
        JSON-RPC error body) never retry; exhausted transient failures raise
        ``OdooTransientError`` so callers can distinguish "outcome unknown"
        from a definitive rejection.
        """
        payload = {
            "jsonrpc": "2.0",
            "method": "call",
            "params": {"service": service, "method": method, "args": args},
        }
        attempts_allowed = self.max_retries + 1 if max_attempts is None else max_attempts
        last_error = "unknown"
        for attempt in range(attempts_allowed):
            if attempt:
                await asyncio.sleep(self.retry_backoff_seconds * (2 ** (attempt - 1)))
            try:
                resp = await self._get_client().post("/jsonrpc", json=payload)
            except httpx.TransportError as exc:
                # network-level: timeout, connect refused, proxy hiccup — transient
                last_error = type(exc).__name__
                logger.warning(
                    "odoo rpc transport error, attempt %d/%d: %s",
                    attempt + 1,
                    attempts_allowed,
                    last_error,
                    extra={"run_id": odoo_run_id.get()},
                )
                continue
            if resp.status_code >= 500:
                # server-side transient
                last_error = f"HTTP {resp.status_code}"
                logger.warning(
                    "odoo rpc %s, attempt %d/%d",
                    last_error,
                    attempt + 1,
                    attempts_allowed,
                    extra={"run_id": odoo_run_id.get()},
                )
                continue
            if resp.status_code >= 400:
                raise OdooApiError(f"Odoo HTTP {resp.status_code}: {resp.text[:300]}")
            try:
                body = resp.json()
            except ValueError:
                # 2xx with an unparseable body: the request may have been
                # applied server-side — treat as transient/outcome-unknown,
                # never as a definitive result.
                last_error = "malformed JSON response"
                logger.warning(
                    "odoo rpc malformed response, attempt %d/%d",
                    attempt + 1,
                    attempts_allowed,
                    extra={"run_id": odoo_run_id.get()},
                )
                continue
            if "error" in body:
                err = body["error"]
                msg = err.get("data", {}).get("message") if isinstance(err, dict) else str(err)
                raise OdooApiError(f"Odoo JSON-RPC error: {msg}", data=err)
            return body.get("result")
        raise OdooTransientError(
            f"Odoo unreachable after {attempts_allowed} attempts (last: {last_error})"
        )

    async def _authenticate(self) -> int:
        if self._uid is None:
            uid = await self._rpc(
                "common", "authenticate", [self.db, self.username, self.api_key, {}]
            )
            if not isinstance(uid, int) or uid is False:
                raise OdooApiError("Odoo authentication failed (bad db/user/api_key)")
            self._uid = uid
        return self._uid

    async def _execute_kw(
        self,
        model: str,
        method: str,
        args: list[object],
        kwargs: dict[str, object] | None = None,
        *,
        max_attempts: int | None = None,
    ) -> object:
        enforce_operation_policy(model, method)
        uid = await self._authenticate()
        return await self._rpc(
            "object",
            "execute_kw",
            [self.db, uid, self.api_key, model, method, args, kwargs or {}],
            max_attempts=max_attempts,
        )

    async def _resolve_account_id(self, code: str) -> int:
        if code not in self._account_ids:
            rows = await self._execute_kw(
                "account.account",
                "search_read",
                [[["code", "=", code]]],
                {"fields": ["id"], "limit": 1},
            )
            if not isinstance(rows, list) or not rows:
                raise OdooApiError(f"no account with code {code!r} in Odoo chart")
            self._account_ids[code] = int(rows[0]["id"])
        return self._account_ids[code]

    async def _resolve_journal_id(self, code: str) -> int:
        if code not in self._journal_ids:
            rows = await self._execute_kw(
                "account.journal",
                "search_read",
                [[["code", "=", code]]],
                {"fields": ["id"], "limit": 1},
            )
            if not isinstance(rows, list) or not rows:
                raise OdooApiError(f"no journal with code {code!r} in Odoo")
            self._journal_ids[code] = int(rows[0]["id"])
        return self._journal_ids[code]

    async def find_move(self, external_id: str) -> str | None:
        rows = await self._execute_kw(
            "account.move",
            "search_read",
            [[["ref", "like", f"[src:{external_id}]"]]],
            {"fields": ["id"], "limit": 1},
        )
        if isinstance(rows, list) and rows:
            return str(rows[0]["id"])
        return None

    async def _build_journal_entry_vals(
        self, entry: JournalEntry, journal_id: int, marker: str
    ) -> dict[str, object]:
        """Miscellaneous journal entry (``move_type='entry'``): raw line_ids.

        Balanced debit/credit journal items are the correct Odoo shape for a
        general entry, and Odoo stores them verbatim. This shape is deliberately
        NOT used for vendor bills — see ``_build_invoice_vals``.
        """
        line_cmds: list[object] = []
        for ln in entry.lines:
            account_id = await self._resolve_account_id(ln.account_code)
            line_cmds.append(
                (
                    0,
                    0,
                    {
                        "account_id": account_id,
                        "name": ln.description,
                        "debit": _to_wire(ln.debit),
                        "credit": _to_wire(ln.credit),
                    },
                )
            )
        return {
            "move_type": entry.move_type,
            "date": entry.entry_date.isoformat(),
            # the [src:...] suffix is the idempotency marker find_move queries
            "ref": f"{entry.ref} {marker}",
            "journal_id": journal_id,
            "line_ids": line_cmds,
        }

    async def _build_invoice_vals(
        self, entry: JournalEntry, partner_id: int | None, journal_id: int, marker: str
    ) -> dict[str, object]:
        """Vendor bill (``move_type='in_invoice'``): Odoo-native invoice lines.

        Odoo 17's invoice engine recomputes the journal items of an invoice-type
        move from its invoice lines, so raw debit/credit ``line_ids`` are
        rejected as unbalanced (confirmed against live Odoo 17, sandbox run
        sbx20260730090315: debits collapsed to 0). We therefore send only the
        **debit** business lines as ``invoice_line_ids`` and let Odoo generate
        the accounts-payable counterpart from ``partner_id`` + the line total.

        EXPLICIT_INPUT_VAT_LINE_COMPATIBILITY_MODE: input VAT is booked as its
        own invoice line on its own account (1360), NOT as an Odoo tax object —
        no ``tax_ids`` are set. The manual accounts-payable credit line (2100)
        from the balanced ``JournalEntry`` is intentionally omitted; that line
        is Odoo's to generate. The entry has already passed Decimal balance
        validation, so ``sum(invoice line price_unit) == entry.total_debit()``
        and Odoo's generated payable equals that same total.
        """
        invoice_lines: list[object] = []
        for ln in entry.lines:
            if ln.debit <= 0:
                # credit lines (the accounts-payable counterpart) are generated
                # by Odoo from the partner + total — never sent by us.
                continue
            account_id = await self._resolve_account_id(ln.account_code)
            invoice_lines.append(
                (
                    0,
                    0,
                    {
                        "name": ln.description,
                        "account_id": account_id,
                        "quantity": 1.0,
                        "price_unit": _to_wire(ln.debit),
                    },
                )
            )
        iso = entry.entry_date.isoformat()
        return {
            "move_type": entry.move_type,
            # partner binding comes only from upsert_supplier's result, and the
            # invoice/accounting dates are the controlled business date.
            "partner_id": partner_id,
            "invoice_date": iso,
            "date": iso,
            "journal_id": journal_id,
            # the [src:...] suffix is the idempotency marker find_move queries
            "ref": f"{entry.ref} {marker}",
            "invoice_line_ids": invoice_lines,
        }

    async def _create_move(
        self, entry: JournalEntry, op: str, post: bool, partner_id: int | None = None
    ) -> str:
        # All guards fire before any network egress: balance, currency fence,
        # move-type fence, journal fence, controlled partner binding for bills.
        _validate_move_inputs(entry, partner_id)
        if post and not self.allow_auto_post:
            raise PostingNotPermittedError()
        marker = f"[src:{entry.external_id}]"
        # Duplicate guard: a crash after Odoo-create but before our DB commit
        # must not create a second draft on re-run. The external_id is embedded
        # in the move's ref as a searchable marker.
        existing = await self.find_move(entry.external_id)
        if existing is not None:
            logger.info("odoo %s dedup ext=%s -> existing %s", op, entry.external_id, existing)
            return existing
        journal_id = await self._resolve_journal_id(entry.journal_code)
        # Vendor bills use Odoo-native invoice lines; general entries use raw
        # balanced journal items. Unsupported types were already refused above.
        if entry.move_type == "in_invoice":
            vals = await self._build_invoice_vals(entry, partner_id, journal_id, marker)
        else:  # "entry"
            vals = await self._build_journal_entry_vals(entry, journal_id, marker)

        # create is NOT idempotent, so it never blind-retries. On a transient
        # failure the outcome is unknown (Odoo may have committed the move and
        # lost the response), so recovery is: search the source marker; reuse
        # the move if found; only re-send create when the search proves the
        # server never created it. Total attempts stay bounded.
        move_id: object | None = None
        for create_attempt in range(1, self.max_retries + 2):
            try:
                created = await self._execute_kw(
                    "account.move", "create", [vals], max_attempts=1
                )
                move_id = created[0] if isinstance(created, list) else created
                break
            except OdooTransientError:
                logger.warning(
                    "odoo.create_outcome_unknown",
                    extra={
                        "run_id": odoo_run_id.get(),
                        "marker": marker,
                        "attempt": create_attempt,
                        "action": "search_marker_before_any_recreate",
                    },
                )
                recovered = await self.find_move(entry.external_id)
                if recovered is not None:
                    logger.info(
                        "odoo.create_recovered",
                        extra={
                            "run_id": odoo_run_id.get(),
                            "marker": marker,
                            "attempt": create_attempt,
                            "action": "reuse_server_created_move",
                            "odoo_move_id": recovered,
                        },
                    )
                    return recovered
                if create_attempt > self.max_retries:
                    raise
                await asyncio.sleep(self.retry_backoff_seconds * (2 ** (create_attempt - 1)))
        if move_id is None:
            raise OdooTransientError(
                f"Odoo create unresolved after {self.max_retries + 1} bounded attempts"
            )
        if post and self.allow_auto_post:
            await self._execute_kw("account.move", "action_post", [[move_id]])
        state = "posted" if (post and self.allow_auto_post) else "draft"
        logger.info("odoo %s ext=%s state=%s -> %s", op, entry.external_id, state, move_id)
        return str(move_id)

    async def create_vendor_bill(
        self, entry: JournalEntry, *, partner_id: int, post: bool = False
    ) -> str:
        return await self._create_move(entry, "create_vendor_bill", post, partner_id)

    async def post_journal_entry(self, entry: JournalEntry, *, post: bool = False) -> str:
        return await self._create_move(entry, "post_journal_entry", post)

    async def _find_partner(self, ref: str) -> int | None:
        found = await self._execute_kw(
            "res.partner",
            "search_read",
            [[["ref", "=", ref]]],
            {"fields": ["id"], "limit": 1},
        )
        if isinstance(found, list) and found:
            return int(found[0]["id"])
        return None

    async def upsert_supplier(self, supplier: SupplierRecord) -> str:
        vals: dict[str, object] = {
            "name": supplier.name,
            "ref": supplier.ref,
            "supplier_rank": 1,
        }
        if supplier.tax_id:
            vals["vat"] = supplier.tax_id
        rec_id = await self._find_partner(supplier.ref)
        if rec_id is not None:
            await self._execute_kw("res.partner", "write", [[rec_id], vals])
            return str(rec_id)
        # Same no-blind-retry policy as moves: a transient failure on create
        # means "outcome unknown" — re-search the unique ref before ever
        # re-sending create, so a lost response cannot duplicate a partner.
        for create_attempt in range(1, self.max_retries + 2):
            try:
                created = await self._execute_kw("res.partner", "create", [vals], max_attempts=1)
                return str(created[0] if isinstance(created, list) else created)
            except OdooTransientError:
                logger.warning(
                    "odoo.partner_create_outcome_unknown",
                    extra={
                        "run_id": odoo_run_id.get(),
                        "supplier_ref": supplier.ref,
                        "attempt": create_attempt,
                        "action": "search_ref_before_any_recreate",
                    },
                )
                recovered = await self._find_partner(supplier.ref)
                if recovered is not None:
                    return str(recovered)
                if create_attempt > self.max_retries:
                    raise
                await asyncio.sleep(self.retry_backoff_seconds * (2 ** (create_attempt - 1)))
        raise OdooTransientError("unreachable")  # pragma: no cover

    async def get_ap_aging(self, supplier_ref: str | None = None) -> list[dict[str, object]]:
        domain: list[object] = [
            ["move_type", "=", "in_invoice"],
            ["state", "=", "posted"],
            ["payment_state", "in", ["not_paid", "partial"]],
        ]
        if supplier_ref is not None:
            domain.append(["partner_id.ref", "=", supplier_ref])
        rows = await self._execute_kw(
            "account.move",
            "search_read",
            [domain],
            {"fields": ["name", "invoice_date_due", "amount_residual", "payment_state"]},
        )
        return list(rows) if isinstance(rows, list) else []


# ---------------------------------------------------------------------------
# DI helper
# ---------------------------------------------------------------------------


_singleton: OdooClient | None = None


def get_odoo() -> OdooClient:
    """Return the process-wide Odoo client.

    Real ``HttpOdooClient`` when ``ODOO_API_KEY`` (and friends) are configured,
    otherwise the in-memory ``StubOdooClient`` used in dev and tests.
    """
    global _singleton
    if _singleton is None:
        from ...config import get_settings

        s = get_settings()
        if s.odoo_url and s.odoo_db and s.odoo_username and s.odoo_api_key:
            _singleton = HttpOdooClient.from_env()
        else:
            _singleton = StubOdooClient(allow_auto_post=s.odoo_allow_auto_post)
    return _singleton


def reset_odoo() -> None:
    """For tests -- clear the singleton."""
    global _singleton
    _singleton = None


__all__ = [
    "ALLOWED_CURRENCIES",
    "ALLOWED_JOURNAL_CODES",
    "ALLOWED_MODELS",
    "ALLOWED_WRITE_OPERATIONS",
    "FORBIDDEN_METHODS",
    "READ_METHODS",
    "HttpOdooClient",
    "InvalidPartnerError",
    "ModelNotAllowedError",
    "OdooApiError",
    "OdooClient",
    "OdooTransientError",
    "OperationNotAllowedError",
    "PostingNotPermittedError",
    "StubOdooClient",
    "SupplierRecord",
    "UnsupportedCurrencyError",
    "enforce_operation_policy",
    "get_odoo",
    "odoo_run_id",
    "reset_odoo",
]
