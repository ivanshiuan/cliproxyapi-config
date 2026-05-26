"""Workspace — sandboxed filesystem for agent-generated artifacts.

Each task gets `workspace/<task_id>/`. All writes from the Coder agent go through
`WorkspaceManager`, which path-traversal-guards every operation.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


class WorkspaceError(ValueError):
    """Raised when a path violates workspace containment."""


class WorkspaceManager:
    """Filesystem manager scoped to a single task directory.

    All paths passed to read/write are interpreted as relative to `root`.
    Absolute paths and `..` traversal raise WorkspaceError.
    """

    def __init__(self, root: Path):
        self.root = root.expanduser().resolve()

    @classmethod
    def create(cls, workspace_root: Path, task_id: str, user_request: str) -> WorkspaceManager:
        """Create a fresh task directory under `workspace_root` and seed task.json."""
        root = workspace_root.expanduser().resolve() / task_id
        root.mkdir(parents=True, exist_ok=False)
        manifest = {
            "task_id": task_id,
            "user_request": user_request,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        (root / "task.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
        return cls(root)

    # ──────────────────────────────────────────────────────────────────────
    # Path safety
    # ──────────────────────────────────────────────────────────────────────

    def _safe_path(self, rel_path: str) -> Path:
        """Resolve `rel_path` against root; reject anything that escapes."""
        if not rel_path:
            raise WorkspaceError("Empty path")
        p = Path(rel_path)
        if p.is_absolute():
            raise WorkspaceError(f"Absolute path not allowed: {rel_path!r}")
        resolved = (self.root / p).resolve()
        # Python 3.12 has Path.is_relative_to; we use it directly.
        if not resolved.is_relative_to(self.root):
            raise WorkspaceError(f"Path escapes workspace: {rel_path!r}")
        return resolved

    # ──────────────────────────────────────────────────────────────────────
    # File operations
    # ──────────────────────────────────────────────────────────────────────

    def write(self, rel_path: str, content: str) -> int:
        """Write text content; create parent dirs as needed. Returns byte count."""
        target = self._safe_path(rel_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8")
        target.write_bytes(data)
        return len(data)

    def read(self, rel_path: str) -> str:
        target = self._safe_path(rel_path)
        if not target.exists():
            raise FileNotFoundError(f"Not in workspace: {rel_path!r}")
        return target.read_text(encoding="utf-8")

    def exists(self, rel_path: str) -> bool:
        try:
            return self._safe_path(rel_path).exists()
        except WorkspaceError:
            return False

    def list_files(self) -> list[str]:
        """List all files (relative paths), excluding task.json and dotfiles."""
        out: list[str] = []
        for p in sorted(self.root.rglob("*")):
            if not p.is_file():
                continue
            if p.name.startswith("."):
                continue
            rel = p.relative_to(self.root).as_posix()
            if rel == "task.json":
                continue
            out.append(rel)
        return out

    def tree(self) -> str:
        """Pretty file tree for display / for prompting Coder during heal."""
        files = self.list_files()
        if not files:
            return "(empty)"
        return "\n".join(f"  {f}" for f in files)
