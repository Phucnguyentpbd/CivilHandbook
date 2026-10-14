"""
id_allocator.py — Thread-safe ID allocator dùng SQLite sequence.
Trả mã dạng CIV-XXXX (4 chữ số, padding zero).
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from . import config

_lock = threading.Lock()


def _ensure_sequence_table(conn: sqlite3.Connection) -> None:
    """Tạo bảng id_sequence nếu chưa có."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS id_sequence (
            name  TEXT PRIMARY KEY,
            value INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        "INSERT OR IGNORE INTO id_sequence (name, value) VALUES ('case_id', 0)"
    )
    conn.commit()


def allocate_id(db_path: Path | None = None) -> str:
    """
    Cấp phát mã mục tiếp theo theo dạng CIV-XXXX.
    Thread-safe thông qua threading.Lock + SQLite transaction.

    Args:
        db_path: Đường dẫn tới file SQLite (mặc định config.DB_PATH).

    Returns:
        Chuỗi dạng "CIV-0001", "CIV-0002", ...
    """
    if db_path is None:
        db_path = config.DB_PATH

    db_path.parent.mkdir(parents=True, exist_ok=True)

    with _lock:
        conn = sqlite3.connect(str(db_path), timeout=config.SQLITE_BUSY_TIMEOUT_MS / 1000)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(f"PRAGMA busy_timeout={config.SQLITE_BUSY_TIMEOUT_MS}")
            _ensure_sequence_table(conn)

            # atomic increment
            conn.execute(
                "UPDATE id_sequence SET value = value + 1 WHERE name = 'case_id'"
            )
            row = conn.execute(
                "SELECT value FROM id_sequence WHERE name = 'case_id'"
            ).fetchone()
            conn.commit()
            new_val: int = row[0]
        finally:
            conn.close()

    return f"CIV-{new_val:04d}"


def peek_next_id(db_path: Path | None = None) -> str:
    """
    Xem mã tiếp theo mà không tăng counter.
    Chỉ dùng để hiển thị, KHÔNG đảm bảo mã này sẽ được cấp phát cho caller.
    """
    if db_path is None:
        db_path = config.DB_PATH

    if not db_path.exists():
        return "CIV-0001"

    conn = sqlite3.connect(str(db_path), timeout=5)
    try:
        row = conn.execute(
            "SELECT value FROM id_sequence WHERE name = 'case_id'"
        ).fetchone()
    finally:
        conn.close()

    return f"CIV-{(row[0] + 1 if row else 1):04d}"
