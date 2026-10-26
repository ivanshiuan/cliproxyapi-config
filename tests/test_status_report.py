"""scripts/status_report.py must count real call sites of the calc engines, not mentions of their names.

A text grep also counted imports, re-exports, docstrings and the script's own lookup table, so an engine
nobody calls was reported as "wired". That is exactly the lie docs/STATUS.md exists to prevent.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "status_report.py"
    spec = importlib.util.spec_from_file_location("status_report_under_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_engine_wiring_counts_calls_not_mentions(tmp_path, monkeypatch):
    sr = _load_script()
    services = tmp_path / "restaurant_api" / "services"
    (services / "calc").mkdir(parents=True)
    (services / "uses.py").write_text(
        '"""Mentions compute_daily_pnl and validate_uniform_invoice in prose."""\n'
        "from .calc import compute_daily_pnl, validate_uniform_invoice\n"  # imported, never called
        'TABLE = {"consume_bom": "BOM"}\n'  # a name used as a string key
        "\n"
        "def run(x):\n"
        "    return compute_daily_pnl(x) + calc.consume_bom(x)\n",  # the only two real calls
        encoding="utf-8",
    )
    (services / "calc" / "engine.py").write_text("def consume_bom(): ...\nconsume_bom()\n", encoding="utf-8")
    monkeypatch.setattr(sr, "ROOT", tmp_path)

    counts = {fn: n for fn, _label, n in sr.engine_wiring(["restaurant_api/services/uses.py", "restaurant_api/services/calc/engine.py"])}

    assert counts["compute_daily_pnl"] == 1
    assert counts["consume_bom"] == 1  # the call inside services/calc/ itself does not count
    assert counts["validate_uniform_invoice"] == 0  # imported and named in a docstring: still nobody calls it
