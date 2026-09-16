"""Offline tests for the staging deploy automation (scripts/deploy_staging.py).

These run without Docker or a live Odoo: they exercise the pure desired-state
rendering, the idempotency decisions, and — most importantly — the security
contract that the acceptance `verify` phase asserts (permission allow-list,
posting/journal/currency fences, dedup). Those checks must be GREEN anywhere,
so a regression in the Odoo permission boundary fails this suite immediately.
"""

from __future__ import annotations

import json
from pathlib import Path

import scripts.deploy_staging as ds


def _cfg(tmp: Path, pg_mode: str = "docker", action: str = "plan") -> ds.DeployConfig:
    return ds.DeployConfig(
        action=action,
        approve=False,
        home=tmp,
        odoo_url=ds.DEFAULT_ODOO_URL,
        odoo_db=ds.DEFAULT_ODOO_DB,
        admin_user="admin",
        pg_mode=pg_mode,
        verbose=False,
    )


# --- desired-state rendering -------------------------------------------------


def test_compose_pins_images_and_binds_localhost(tmp_path: Path) -> None:
    yaml = ds.render_compose_yaml(_cfg(tmp_path))
    assert ds.ODOO_LOCAL_TAG in yaml
    assert ds.PG_LOCAL_TAG in yaml
    # Odoo port never exposed to 0.0.0.0 (acceptance A4)
    assert "127.0.0.1:18069:8069" in yaml
    assert "0.0.0.0" not in yaml
    assert "pg_password" in yaml  # docker-secret, not inline password


def test_compose_managed_pg_drops_db_service(tmp_path: Path) -> None:
    yaml = ds.render_compose_yaml(_cfg(tmp_path, pg_mode="managed"))
    assert "odoo-staging-db" not in yaml
    assert "secrets:" not in yaml
    assert ds.ODOO_LOCAL_TAG in yaml


def test_odoo_conf_has_no_demo_and_no_password(tmp_path: Path) -> None:
    conf = ds.render_odoo_conf(_cfg(tmp_path))
    assert "without_demo = all" in conf
    assert "db_name = resto_staging" in conf
    assert "password" not in conf.lower()  # PG password stays in docker secret


def test_render_is_deterministic(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    assert ds.render_compose_yaml(cfg) == ds.render_compose_yaml(cfg)
    assert ds.render_odoo_conf(cfg) == ds.render_odoo_conf(cfg)


# --- idempotency decisions ---------------------------------------------------


def test_file_decision_create_unchanged_update(tmp_path: Path) -> None:
    p = tmp_path / "f.txt"
    assert ds.file_decision(p, "a") == ds.CREATE
    p.write_text("a", encoding="utf-8")
    assert ds.file_decision(p, "a") == ds.UNCHANGED
    assert ds.file_decision(p, "b") == ds.UPDATE


def test_secret_decision_never_rotates(tmp_path: Path) -> None:
    p = tmp_path / "s.txt"
    assert ds.secret_decision(p) == ds.CREATE
    p.write_text("existing", encoding="utf-8")
    assert ds.secret_decision(p) == ds.UNCHANGED  # present -> keep, never rotate


def test_new_secret_is_random_and_nonempty() -> None:
    a, b = ds.new_secret(), ds.new_secret()
    assert a and b and a != b


def test_phase_config_files_is_idempotent(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, action="apply")
    first = ds.phase_config_files(cfg)
    assert {r.status for r in first} == {ds.CREATE}
    # re-run converges to no-op
    second = ds.phase_config_files(cfg)
    assert {r.status for r in second} == {ds.UNCHANGED}


def test_phase_secrets_create_then_keep(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, action="apply")
    first = {r.name: r.status for r in ds.phase_secrets(cfg)}
    assert first["pg_password.txt"] == ds.CREATE
    second = {r.name: r.status for r in ds.phase_secrets(cfg)}
    assert all(v == ds.UNCHANGED for v in second.values())
    # secret files are chmod 600
    mode = (cfg.config_dir / "pg_password.txt").stat().st_mode & 0o777
    assert mode == 0o600


def test_plan_mode_writes_nothing(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, action="plan")
    ds.phase_config_files(cfg)
    ds.phase_secrets(cfg)
    assert not cfg.config_dir.exists() or not any(cfg.config_dir.iterdir())


# --- security contract (the checks `verify` runs offline, for real) ----------


def test_individual_security_checks_pass() -> None:
    assert ds._check_g4_posting_fence()[0] == ds.OK
    assert ds._check_g5_journal_fence()[0] == ds.OK
    assert ds._check_f1_currency_fence()[0] == ds.OK
    assert ds._check_idempotent_dedup()[0] == ds.OK


def test_acceptance_offline_has_zero_failures() -> None:
    report = ds.run_acceptance(_cfg(Path(".")))
    counts = report["counts"]
    # The whole point: offline still proves the security contract; nothing FAILs.
    assert counts[ds.FAIL] == 0, report
    # Live infra checks are honestly skipped, not faked green.
    assert counts[ds.SKIP] > 0
    assert report["verdict"] == "INCOMPLETE_OFFLINE"
    # Every G (permission) check actually ran and passed.
    g = [c for c in report["checks"] if c["section"] == "Permission"]
    assert len(g) == 5
    assert all(c["status"] == ds.OK for c in g)


def test_acceptance_total_is_46() -> None:
    report = ds.run_acceptance(_cfg(Path(".")))
    assert report["total"] == 46


# --- evidence + approval gate ------------------------------------------------


def test_write_evidence_has_sha256(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    report = ds.run_acceptance(_cfg(tmp_path))
    path = ds.write_evidence(report, _cfg(tmp_path))
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert len(payload["sha256"]) == 64
    assert payload["report"]["verdict"] == report["verdict"]


def test_verify_offline_does_not_stamp_ready(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    # --approve offline must NOT produce a READY stamp (verdict != READY)
    rc = ds.main(["verify", "--approve"])
    assert rc == 0
    assert not (tmp_path / ".runtime/odoo-certification/STAGING-READY.approved").exists()


# --- CLI ---------------------------------------------------------------------


def test_default_action_is_plan() -> None:
    assert ds.resolve_config([]).action == "plan"


def test_plan_run_returns_zero(tmp_path: Path, capsys) -> None:
    rc = ds.main(["plan", "--home", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "GO / NO-GO" in out
