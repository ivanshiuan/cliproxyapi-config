"""Tests for WorkspaceManager — path safety and basic file ops."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from devswarm.workspace import WorkspaceError, WorkspaceManager


def test_create_writes_manifest(tmp_path: Path):
    ws = WorkspaceManager.create(tmp_path, "task01", "Do the thing")
    assert ws.root == (tmp_path / "task01").resolve()
    manifest = json.loads((ws.root / "task.json").read_text(encoding="utf-8"))
    assert manifest["task_id"] == "task01"
    assert manifest["user_request"] == "Do the thing"
    assert "created_at" in manifest


def test_write_and_read_roundtrip(tmp_path: Path):
    ws = WorkspaceManager.create(tmp_path, "task02", "X")
    n = ws.write("hello.py", "print('hi')\n")
    assert n == len("print('hi')\n".encode("utf-8"))
    assert ws.read("hello.py") == "print('hi')\n"


def test_write_creates_parent_dirs(tmp_path: Path):
    ws = WorkspaceManager.create(tmp_path, "task03", "X")
    ws.write("pkg/sub/mod.py", "x = 1\n")
    assert (ws.root / "pkg" / "sub" / "mod.py").exists()


def test_reject_absolute_path(tmp_path: Path):
    ws = WorkspaceManager.create(tmp_path, "task04", "X")
    with pytest.raises(WorkspaceError):
        ws.write("/etc/passwd", "pwned")


def test_reject_traversal(tmp_path: Path):
    ws = WorkspaceManager.create(tmp_path, "task05", "X")
    with pytest.raises(WorkspaceError):
        ws.write("../escape.txt", "pwned")
    with pytest.raises(WorkspaceError):
        ws.write("sub/../../escape.txt", "pwned")


def test_reject_empty_path(tmp_path: Path):
    ws = WorkspaceManager.create(tmp_path, "task06", "X")
    with pytest.raises(WorkspaceError):
        ws.write("", "x")


def test_list_files_excludes_manifest_and_dotfiles(tmp_path: Path):
    ws = WorkspaceManager.create(tmp_path, "task07", "X")
    ws.write("a.py", "a")
    ws.write("nested/b.py", "b")
    # Write a dotfile directly via raw filesystem to make sure list_files skips it.
    (ws.root / ".secret").write_text("hidden")
    files = ws.list_files()
    assert files == ["a.py", "nested/b.py"]


def test_tree_returns_empty_marker_when_empty(tmp_path: Path):
    ws = WorkspaceManager.create(tmp_path, "task08", "X")
    assert ws.tree() == "(empty)"


def test_exists_false_for_traversal(tmp_path: Path):
    ws = WorkspaceManager.create(tmp_path, "task09", "X")
    assert ws.exists("../whatever") is False
