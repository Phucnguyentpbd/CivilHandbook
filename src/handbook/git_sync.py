"""
git_sync.py — Tự động đồng bộ Sổ tay Civil qua Git / GitHub.

Mục đích:
- Hỗ trợ đa máy (máy chính, laptop, máy cơ quan) qua GitHub Private Repo.
- Nguồn chân lý là Markdown & JSON trên Git.
- SQLite là chỉ mục dẫn xuất cục bộ — máy nào kéo code/data về tự rebuild máy đó.
- Không để lỗi mạng hoặc chưa cấu hình remote làm sập thao tác lưu cục bộ.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any

from . import config
from .storage import rebuild_index_from_sources

logger = logging.getLogger("civil_handbook.git_sync")
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _run_git(args: list[str], cwd: Path | None = None) -> tuple[int, str, str]:
    """Chạy một lệnh git và trả về (returncode, stdout, stderr)."""
    target_cwd = cwd or PROJECT_ROOT
    try:
        proc = subprocess.run(
            ["git"] + args,
            cwd=str(target_cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except Exception as e:
        return -1, "", str(e)


def is_git_repo(project_dir: Path | None = None) -> bool:
    """Kiểm tra thư mục dự án có phải git repository không."""
    target_dir = project_dir or PROJECT_ROOT
    return (target_dir / ".git").exists()


def get_git_remote(project_dir: Path | None = None) -> str | None:
    """Lấy URL của remote origin nếu có."""
    code, out, _ = _run_git(["remote", "get-url", "origin"], cwd=project_dir)
    return out if code == 0 and out else None


def get_current_branch(project_dir: Path | None = None) -> str:
    """Lấy tên nhánh hiện tại."""
    code, out, _ = _run_git(["branch", "--show-current"], cwd=project_dir)
    return out if code == 0 and out else "main"


def git_pull(
    project_dir: Path | None = None,
    rebuild_on_change: bool = True,
) -> dict[str, Any]:
    """
    Kéo các thay đổi mới nhất từ GitHub về.
    Nếu có cập nhật ca mới, tự động dựng lại chỉ mục SQLite.
    """
    target_dir = project_dir or PROJECT_ROOT
    if not is_git_repo(target_dir):
        return {"status": "not_git_repo", "message": "Thư mục chưa khởi tạo Git."}

    remote = get_git_remote(target_dir)
    if not remote:
        return {
            "status": "no_remote",
            "message": "Chưa cấu hình Git remote 'origin'.",
        }

    # Chạy git pull --rebase
    code, out, err = _run_git(["pull", "--rebase", "origin", get_current_branch(target_dir)], cwd=target_dir)
    if code != 0:
        logger.warning("Git pull thất bại: %s", err)
        return {
            "status": "pull_error",
            "error": err,
            "stdout": out,
            "message": "Không thể kéo từ GitHub (kiểm tra kết nối mạng hoặc conflict).",
        }

    already_up_to_date = "Already up to date" in out or "up-to-date" in out.lower()
    rebuilt_count = 0

    if not already_up_to_date and rebuild_on_change:
        try:
            rebuilt_count = rebuild_index_from_sources()
            logger.info("Đã tự động rebuild index SQLite sau khi git pull: %d ca", rebuilt_count)
        except Exception as e:
            logger.error("Lỗi rebuild index sau git pull: %s", e)

    return {
        "status": "success",
        "already_up_to_date": already_up_to_date,
        "detail": out,
        "rebuilt_cases": rebuilt_count,
        "message": "Đã cập nhật mới nhất từ GitHub." if not already_up_to_date else "Dữ liệu đã ở trạng thái mới nhất.",
    }


def git_commit_and_push(
    case_id: str,
    title: str,
    project_dir: Path | None = None,
) -> dict[str, Any]:
    """
    Stage ca mới, commit và push lên GitHub.
    Nếu không có remote hoặc lỗi mạng, chỉ commit cục bộ mà không làm fail thao tác.
    """
    target_dir = project_dir or PROJECT_ROOT
    if not is_git_repo(target_dir):
        return {"status": "not_git_repo", "message": "Chưa khởi tạo Git."}

    # 1. Add data/cases/, data/evidence/ và data/taxonomy/ nếu tồn tại
    add_paths = []
    for rel in ["data/cases", "data/evidence", "data/taxonomy"]:
        if (target_dir / rel).exists():
            add_paths.append(rel)
    if not add_paths:
        return {"status": "nothing_to_add", "message": "Không có thư mục dữ liệu để lưu."}

    code_add, _, err_add = _run_git(["add"] + add_paths, cwd=target_dir)
    if code_add != 0:
        return {"status": "add_error", "error": err_add}

    # 2. Commit
    commit_msg = f"feat(cases): {case_id} - {title}"
    code_commit, out_commit, err_commit = _run_git(
        ["commit", "-m", commit_msg],
        cwd=target_dir,
    )
    # Nếu không có gì để commit (ví dụ nội dung không đổi)
    if code_commit != 0 and "nothing to commit" not in (out_commit + err_commit).lower():
        logger.debug("Git commit output: %s %s", out_commit, err_commit)

    # 3. Push lên remote nếu có
    remote = get_git_remote(target_dir)
    if not remote:
        return {
            "status": "committed_local",
            "message": "Đã lưu Git cục bộ (chưa cấu hình remote GitHub để push).",
        }

    branch = get_current_branch(target_dir)
    code_push, out_push, err_push = _run_git(["push", "origin", branch], cwd=target_dir)
    if code_push != 0:
        logger.warning("Git push gặp sự cố (offline hoặc quyền ghi): %s", err_push)
        return {
            "status": "push_pending",
            "message": "Đã commit cục bộ; push lên GitHub bị hoãn do lỗi mạng/quyền truy cập.",
            "error": err_push,
        }

    return {
        "status": "pushed",
        "remote": remote,
        "branch": branch,
        "message": f"Đã đẩy thành công {case_id} lên GitHub ({branch}).",
    }


def git_status(project_dir: Path | None = None) -> dict[str, Any]:
    """Lấy thông tin tổng quan về trạng thái Git."""
    target_dir = project_dir or PROJECT_ROOT
    if not is_git_repo(target_dir):
        return {"is_git_repo": False, "remote": None, "branch": None}

    remote = get_git_remote(target_dir)
    branch = get_current_branch(target_dir)
    _, status_out, _ = _run_git(["status", "--short"], cwd=target_dir)
    _, last_log, _ = _run_git(["log", "-1", "--oneline"], cwd=target_dir)

    return {
        "is_git_repo": True,
        "remote": remote,
        "branch": branch,
        "has_uncommitted": bool(status_out),
        "last_commit": last_log or None,
    }
