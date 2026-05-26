"""Sandbox — run pytest on agent-generated code in a subprocess.

Limitations (v1):
- Not containerized. Generated code runs with the same OS privileges as the swarm process.
  For self-hosted commander use this is acceptable; do NOT point this tool at production
  credentials. Phase 2+ will wrap this in Docker/firejail/nsjail.
- Linux-only resource limits (CPU/AS) via `resource` module; soft no-op on macOS/Windows.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# Output truncation budgets (chars, not tokens — feed into LLM later).
_STDOUT_TAIL = 4000
_STDERR_TAIL = 2000

# Resource limits for the pytest subprocess (Linux only).
_CPU_SECONDS = 30
_ADDR_SPACE_BYTES = 512 * 1024 * 1024  # 512 MB

_FAILED_LINE = re.compile(r"^FAILED\s+(\S+)", re.MULTILINE)


def _apply_rlimits() -> None:
    """preexec_fn hook: applied in the child process before exec()."""
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (_CPU_SECONDS, _CPU_SECONDS))
        resource.setrlimit(
            resource.RLIMIT_AS, (_ADDR_SPACE_BYTES, _ADDR_SPACE_BYTES)
        )
    except Exception:  # pragma: no cover — Windows or restricted env
        # Best-effort; fall through. The outer timeout still protects us.
        pass


def _tail(s: str, limit: int) -> str:
    if len(s) <= limit:
        return s
    return "…(truncated)…\n" + s[-limit:]


def _parse_failed_tests(stdout: str) -> list[str]:
    """Extract `FAILED <nodeid>` lines from pytest stdout."""
    return [m.group(1) for m in _FAILED_LINE.finditer(stdout)]


def run_pytest(workspace_path: Path, timeout_sec: int = 60) -> dict:
    """Run pytest in the workspace and return a structured result.

    Returns dict with keys: passed, exit_code, stdout_tail, stderr_tail, failed_tests.
    """
    workspace_path = workspace_path.expanduser().resolve()
    if not workspace_path.is_dir():
        return {
            "passed": False,
            "exit_code": -1,
            "stdout_tail": "",
            "stderr_tail": f"Workspace does not exist: {workspace_path}",
            "failed_tests": [],
        }

    # Use the same Python interpreter as the swarm. `-x` stops on first failure,
    # `--tb=short` keeps tracebacks compact, `-q` reduces noise.
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-x",
        "--tb=short",
        "-q",
        "--no-header",
        "-p",
        "no:cacheprovider",
    ]

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(workspace_path),
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            preexec_fn=_apply_rlimits if sys.platform != "win32" else None,
            check=False,
        )
        stdout, stderr, exit_code = proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired as exc:
        stdout = (exc.stdout.decode() if isinstance(exc.stdout, bytes) else exc.stdout) or ""
        stderr = (exc.stderr.decode() if isinstance(exc.stderr, bytes) else exc.stderr) or ""
        stderr = f"[sandbox] pytest exceeded {timeout_sec}s timeout\n" + stderr
        exit_code = -2

    return {
        "passed": exit_code == 0,
        "exit_code": exit_code,
        "stdout_tail": _tail(stdout, _STDOUT_TAIL),
        "stderr_tail": _tail(stderr, _STDERR_TAIL),
        "failed_tests": _parse_failed_tests(stdout),
    }
