"""Standalone behavioral test for the WS Origin guard in
`opensquilla-ws-origin-validation.patch`.

Runs without installing the opensquilla package: it extracts the patched
`_ws_origin_allowed` function from the patched source and exercises it against
stub ws/config objects. Point OPENSQUILLA_SRC at your patched checkout.

    OPENSQUILLA_SRC=/path/to/opensquilla python3 patches/test_ws_origin_guard.py
"""

from __future__ import annotations

import ast
import os
import sys
from types import SimpleNamespace

SRC_ROOT = os.environ.get("OPENSQUILLA_SRC", ".")
WS_PATH = os.path.join(SRC_ROOT, "src/opensquilla/gateway/websocket.py")


def _load_guard():
    src = open(WS_PATH).read()
    tree = ast.parse(src)
    fn = next(
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "_ws_origin_allowed"
    )
    future = ast.ImportFrom(
        module="__future__", names=[ast.alias(name="annotations", asname=None)], level=0
    )
    mod = ast.fix_missing_locations(ast.Module(body=[future, fn], type_ignores=[]))
    ns: dict = {}
    exec(compile(mod, "<extract>", "exec"), ns)  # noqa: S102 - trusted local source
    return ns["_ws_origin_allowed"]


class _Headers(dict):
    def get(self, k, d=None):
        return super().get(k, d)


def _ws(origin):
    h = _Headers()
    if origin is not None:
        h["origin"] = origin
    return SimpleNamespace(headers=h)


def main() -> int:
    guard = _load_guard()
    cfg = SimpleNamespace(
        port=18791,
        control_ui=SimpleNamespace(allowed_origins=["https://ops.internal"]),
    )
    cases = [
        ("no Origin (CLI/native)", _ws(None), True),
        ("same-origin 127.0.0.1", _ws("http://127.0.0.1:18791"), True),
        ("same-origin localhost", _ws("http://localhost:18791"), True),
        ("configured extra origin", _ws("https://ops.internal"), True),
        ("FOREIGN evil.example (CSWSH)", _ws("https://evil.example"), False),
        ("wrong port on loopback", _ws("http://127.0.0.1:9999"), False),
        ("literal 'null' origin", _ws("null"), False),
    ]
    ok = True
    for name, ws, want in cases:
        got = guard(ws, cfg)
        if got != want:
            ok = False
        print(f"[{'PASS' if got == want else 'FAIL'}] {name:34} -> {got} (want {want})")
    print("\nALL PASS" if ok else "\nSOME FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
