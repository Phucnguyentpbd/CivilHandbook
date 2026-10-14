"""
concurrency.py — Khóa ghi liên tiến trình dùng SQLite WAL.
Context manager `write_lock()` đảm bảo tại một thời điểm chỉ có
một tiến trình đang ghi vào kho.
"""

from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path
from typing import Generator

from . import config


@contextlib.contextmanager
def write_lock(db_path: Path | None = None) -> Generator[sqlite3.Connection, None, None]:
    """
    Context manager khóa ghi toàn kho qua SQLite IMMEDIATE transaction.

    Khi nhiều tiến trình cùng yêu cầu lock, SQLite WAL + busy_timeout
    đảm bảo chỉ một bên ghi; bên còn lại chờ tối đa SQLITE_BUSY_TIMEOUT_MS
    rồi nhận OperationalError nếu hết thời gian.

    Yields:
        sqlite3.Connection — kết nối đang trong transaction IMMEDIATE.

    Raises:
        sqlite3.OperationalError: Nếu không thể lấy lock trong thời gian chờ.
    """
    path = db_path or config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(
        str(path),
        timeout=config.SQLITE_BUSY_TIMEOUT_MS / 1000,
        isolation_level=None,   # autocommit off — ta quản lý thủ công
    )
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(f"PRAGMA busy_timeout={config.SQLITE_BUSY_TIMEOUT_MS}")
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()
