"""Tests for the pytest sandbox runner."""

from __future__ import annotations

from pathlib import Path

from devswarm.sandbox import run_pytest


def _seed_passing(ws: Path) -> None:
    (ws / "test_pass.py").write_text(
        "def test_ok():\n    assert 1 + 1 == 2\n",
        encoding="utf-8",
    )


def _seed_failing(ws: Path) -> None:
    (ws / "test_fail.py").write_text(
        "def test_bad():\n    assert 1 + 1 == 3\n",
        encoding="utf-8",
    )


def _seed_import_error(ws: Path) -> None:
    (ws / "test_imp.py").write_text(
        "import this_module_does_not_exist_xyz_42\n\n"
        "def test_x():\n    assert True\n",
        encoding="utf-8",
    )


def test_passing_test_returns_passed_true(tmp_path: Path):
    _seed_passing(tmp_path)
    result = run_pytest(tmp_path, timeout_sec=30)
    assert result["passed"] is True
    assert result["exit_code"] == 0
    assert result["failed_tests"] == []


def test_failing_test_returns_passed_false(tmp_path: Path):
    _seed_failing(tmp_path)
    result = run_pytest(tmp_path, timeout_sec=30)
    assert result["passed"] is False
    assert result["exit_code"] != 0
    # The pytest stdout marks failures with `FAILED test_fail.py::test_bad`.
    assert any("test_fail.py" in name for name in result["failed_tests"])


def test_missing_workspace_does_not_crash(tmp_path: Path):
    bogus = tmp_path / "does_not_exist"
    result = run_pytest(bogus, timeout_sec=5)
    assert result["passed"] is False
    assert result["exit_code"] == -1


def test_import_error_is_reported(tmp_path: Path):
    _seed_import_error(tmp_path)
    result = run_pytest(tmp_path, timeout_sec=30)
    assert result["passed"] is False
    # Collection errors land in stdout (pytest behavior).
    assert "this_module_does_not_exist_xyz_42" in (
        result["stdout_tail"] + result["stderr_tail"]
    )


def test_stdout_tail_truncated(tmp_path: Path):
    # Generate >4KB of output to force truncation.
    (tmp_path / "test_big.py").write_text(
        "def test_big():\n"
        + "\n".join(f"    print('line {i} ' + 'x' * 80)" for i in range(200))
        + "\n    assert True\n",
        encoding="utf-8",
    )
    result = run_pytest(tmp_path, timeout_sec=30)
    # Either passes or fails depending on capture; what we care about is sane truncation.
    assert isinstance(result["stdout_tail"], str)
    assert len(result["stdout_tail"]) < 6000
