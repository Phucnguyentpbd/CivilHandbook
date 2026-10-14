"""
tests/test_git_sync.py — Unit test cho module git_sync.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from handbook.git_sync import (
    git_commit_and_push,
    git_pull,
    git_status,
    is_git_repo,
    get_git_remote,
    get_current_branch,
)


def test_is_git_repo_non_repo(tmp_path: Path):
    assert is_git_repo(tmp_path) is False


def test_git_repo_basic_flow(tmp_path: Path):
    # Khởi tạo git repo tạm
    subprocess.run(["git", "init"], cwd=str(tmp_path), check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(tmp_path), check=True)

    assert is_git_repo(tmp_path) is True
    assert get_git_remote(tmp_path) is None

    # Tạo file giả lập trong data/cases
    cases_dir = tmp_path / "data" / "cases" / "CIV-0001"
    cases_dir.mkdir(parents=True)
    (cases_dir / "case.json").write_text('{"id": "CIV-0001"}', encoding="utf-8")

    # Commit local
    res = git_commit_and_push("CIV-0001", "Test case", project_dir=tmp_path)
    assert res["status"] in ("committed_local", "pushed")

    # Kiểm tra status
    st = git_status(tmp_path)
    assert st["is_git_repo"] is True
    assert st["last_commit"] is not None

    # Pull khi không có remote
    pull_res = git_pull(tmp_path)
    assert pull_res["status"] == "no_remote"
