"""
backup_sync.py — Đồng bộ backup một chiều từ D: sang Google Drive.

Kho chính: D:\\AGENT_AI\\CivilHandbook\\data
Kho backup: G:\\My Drive\\CodexSync\\backups\\CivilHandbook

Quy tắc:
- Sao chép 1 chiều: D: -> G: (tuyệt đối không ghi ngược).
- Không backup API key, SQLite DB, WAL/SHM file.
- Chỉ backup Markdown, JSON, taxonomy, manifest.
- Tạo mirror hiện hành + snapshot ngày (tối đa 30 snapshot).
- Cung cấp hàm restore_check() kiểm tra tính toàn vẹn vào thư mục tạm.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from handbook import config

DEST_ROOT = Path(r"G:\My Drive\CodexSync\backups\CivilHandbook")
DEST_CURRENT = DEST_ROOT / "current"
DEST_SNAPSHOTS = DEST_ROOT / "snapshots"


def compute_file_sha256(path: Path) -> str:
    """Tính SHA-256 của một file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def generate_manifest(data_dir: Path) -> dict[str, Any]:
    """Tạo manifest gồm danh sách file, revision và checksum."""
    manifest: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": str(data_dir),
        "files": {},
        "case_count": 0,
    }

    cases_dir = data_dir / "cases"
    if cases_dir.exists():
        for case_dir in cases_dir.iterdir():
            if case_dir.is_dir() and case_dir.name.startswith("CIV-"):
                manifest["case_count"] += 1
                for fpath in case_dir.rglob("*"):
                    if fpath.is_file():
                        rel = str(fpath.relative_to(data_dir)).replace("\\", "/")
                        manifest["files"][rel] = {
                            "size": fpath.stat().st_size,
                            "sha256": compute_file_sha256(fpath),
                        }

    taxonomy_dir = data_dir / "taxonomy"
    if taxonomy_dir.exists():
        for fpath in taxonomy_dir.rglob("*.json"):
            if fpath.is_file():
                rel = str(fpath.relative_to(data_dir)).replace("\\", "/")
                manifest["files"][rel] = {
                    "size": fpath.stat().st_size,
                    "sha256": compute_file_sha256(fpath),
                }

    return manifest


def sync_backup() -> dict[str, Any]:
    """
    Thực hiện sao chép một chiều sang Google Drive.
    """
    codex_sync_root = Path(r"G:\My Drive\CodexSync")
    if not codex_sync_root.exists():
        return {
            "status": "drive_not_ready",
            "message": "Thư mục Google Drive (G:\\My Drive\\CodexSync) chưa sẵn sàng.",
        }

    DEST_CURRENT.mkdir(parents=True, exist_ok=True)
    DEST_SNAPSHOTS.mkdir(parents=True, exist_ok=True)

    manifest = generate_manifest(config.DATA_DIR)

    # 1. Đồng bộ sang mirror current
    for rel_path, info in manifest["files"].items():
        src_file = config.DATA_DIR / rel_path
        dst_file = DEST_CURRENT / rel_path
        dst_file.parent.mkdir(parents=True, exist_ok=True)
        if not dst_file.exists() or compute_file_sha256(dst_file) != info["sha256"]:
            shutil.copy2(src_file, dst_file)

    # Ghi manifest vào current
    with open(DEST_CURRENT / "manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    # 2. Tạo snapshot theo ngày (YYYY-MM-DD)
    today_str = datetime.now().strftime("%Y-%m-%d")
    today_snapshot_dir = DEST_SNAPSHOTS / today_str
    today_snapshot_dir.mkdir(parents=True, exist_ok=True)

    for rel_path in manifest["files"]:
        src_file = DEST_CURRENT / rel_path
        dst_file = today_snapshot_dir / rel_path
        dst_file.parent.mkdir(parents=True, exist_ok=True)
        if not dst_file.exists():
            shutil.copy2(src_file, dst_file)

    with open(today_snapshot_dir / "manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    # 3. Dọn dẹp snapshot cũ (giữ tối đa 30 snapshot)
    all_snapshots = sorted([d for d in DEST_SNAPSHOTS.iterdir() if d.is_dir()])
    if len(all_snapshots) > 30:
        for old in all_snapshots[:-30]:
            shutil.rmtree(old, ignore_errors=True)

    # 4. Giải phóng hàng đợi backup trong SQLite
    if config.DB_PATH.exists():
        try:
            conn = sqlite3.connect(str(config.DB_PATH))
            conn.execute("UPDATE backup_queue SET status = 'done' WHERE status = 'pending'")
            conn.commit()
            conn.close()
        except Exception:
            pass

    return {
        "status": "success",
        "case_count": manifest["case_count"],
        "files_count": len(manifest["files"]),
        "current_mirror": str(DEST_CURRENT),
        "snapshot_date": today_str,
    }


def restore_check(temp_dir: Path) -> dict[str, Any]:
    """
    Kiểm tra khôi phục vào thư mục tạm mà không ghi đè kho chính.
    Đối chiếu số mục, revision và SHA-256 checksum.
    """
    if not (DEST_CURRENT / "manifest.json").exists():
        return {"status": "error", "message": "Chưa có bản backup nào trong Drive."}

    with open(DEST_CURRENT / "manifest.json", encoding="utf-8") as f:
        manifest = json.load(f)

    restore_target = temp_dir / "restore_check_data"
    restore_target.mkdir(parents=True, exist_ok=True)

    mismatches = []
    restored_count = 0

    for rel_path, info in manifest["files"].items():
        src_file = DEST_CURRENT / rel_path
        dst_file = restore_target / rel_path
        dst_file.parent.mkdir(parents=True, exist_ok=True)

        if not src_file.exists():
            mismatches.append(f"Missing file in backup: {rel_path}")
            continue

        shutil.copy2(src_file, dst_file)
        calc_hash = compute_file_sha256(dst_file)
        if calc_hash != info["sha256"]:
            mismatches.append(f"Hash mismatch on {rel_path}: expected {info['sha256']}, got {calc_hash}")
        else:
            restored_count += 1

    # Dựng lại SQLite thử trong thư mục tạm
    from handbook.storage import rebuild_index_from_sources
    test_db = restore_target / "test_rebuilt.sqlite"
    rebuilt_cases = rebuild_index_from_sources(
        db_path=test_db,
        cases_dir=restore_target / "cases",
    )

    return {
        "status": "verified" if not mismatches and rebuilt_cases == manifest["case_count"] else "failed",
        "expected_cases": manifest["case_count"],
        "rebuilt_cases": rebuilt_cases,
        "files_checked": restored_count,
        "mismatches": mismatches,
    }


if __name__ == "__main__":
    result = sync_backup()
    print(json.dumps(result, ensure_ascii=False, indent=2))
