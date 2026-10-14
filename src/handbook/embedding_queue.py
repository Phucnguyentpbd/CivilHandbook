"""
embedding_queue.py — Hàng đợi retry embedding không blocking.

Khi embedding lỗi (network down, quota exceeded, v.v.):
  - File nguồn đã được lưu thành công (atomic_save đã xong)
  - SQLite được set semantic_status = 'semantic_pending'
  - Hàng đợi này retry sau khi provider phục hồi

Không blocking save — lưu file gốc trước, retry sau.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

from handbook.config import DB_PATH, SQLITE_BUSY_TIMEOUT_MS

if TYPE_CHECKING:
    from handbook.embedding import EmbeddingProvider

logger = logging.getLogger(__name__)


def get_pending_cases(db_path: Path = DB_PATH, limit: int = 50) -> list[str]:
    """
    Lấy danh sách case_id có semantic_status = 'semantic_pending'.
    """
    con = sqlite3.connect(str(db_path), timeout=SQLITE_BUSY_TIMEOUT_MS / 1000)
    con.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
    try:
        rows = con.execute(
            """
            SELECT case_id FROM cases
            WHERE semantic_status = 'semantic_pending'
            ORDER BY updated_at ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        con.close()


def process_queue(
    provider: "EmbeddingProvider",
    db_path: Path = DB_PATH,
    batch_size: int = 10,
) -> dict[str, int]:
    """
    Retry embedding cho các mục đang pending.

    Args:
        provider: EmbeddingProvider đã sẵn sàng (health OK).
        db_path: Đường dẫn tới SQLite.
        batch_size: Số mục xử lý mỗi lần gọi.

    Returns:
        {"processed": int, "failed": int, "skipped": int}
    """
    con = sqlite3.connect(str(db_path), timeout=SQLITE_BUSY_TIMEOUT_MS / 1000)
    con.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
    con.row_factory = sqlite3.Row

    stats = {"processed": 0, "failed": 0, "skipped": 0}

    try:
        rows = con.execute(
            """
            SELECT case_id, fts_text
            FROM cases
            WHERE semantic_status = 'semantic_pending'
            ORDER BY updated_at ASC
            LIMIT ?
            """,
            (batch_size,),
        ).fetchall()

        for row in rows:
            case_id = row["case_id"]
            fts_text = row["fts_text"] or ""

            if not fts_text.strip():
                # Không có text để embed — skip
                con.execute(
                    "UPDATE cases SET semantic_status = 'semantic_skipped' WHERE case_id = ?",
                    (case_id,),
                )
                con.commit()
                stats["skipped"] += 1
                continue

            try:
                vectors = provider.embed_documents([fts_text])
                vec = vectors[0]

                import struct
                blob = struct.pack(f"{len(vec)}f", *vec)

                con.execute(
                    """
                    UPDATE cases
                    SET embedding = ?,
                        semantic_status = 'semantic_ok',
                        embedding_model = ?
                    WHERE case_id = ?
                    """,
                    (blob, provider.model_id, case_id),
                )
                con.commit()
                stats["processed"] += 1
                logger.info("Queue: embedded %s OK", case_id)

            except Exception as e:
                err_str = str(e)
                logger.warning("Queue: lỗi embed %s — %s", case_id, err_str)

                # Nếu 401/403 — key hỏng, dừng toàn bộ
                if "401" in err_str or "403" in err_str or "PERMISSION_DENIED" in err_str:
                    logger.error("Queue: lỗi auth — dừng xử lý hàng đợi.")
                    stats["failed"] += 1
                    break

                stats["failed"] += 1
                # Giữ nguyên semantic_pending để thử lại lần sau

    finally:
        con.close()

    return stats


def set_pending(case_id: str, db_path: Path = DB_PATH) -> None:
    """
    Đánh dấu một mục là semantic_pending.
    Dùng khi embedding lỗi ngay sau khi save.
    """
    con = sqlite3.connect(str(db_path), timeout=SQLITE_BUSY_TIMEOUT_MS / 1000)
    try:
        con.execute(
            "UPDATE cases SET semantic_status = 'semantic_pending' WHERE case_id = ?",
            (case_id,),
        )
        con.commit()
    finally:
        con.close()
