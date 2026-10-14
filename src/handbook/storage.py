"""
storage.py — Core storage engine của CivilHandbook.

Chức năng:
- init_db(): Tạo SQLite schema (WAL mode, FTS5, embeddings, undo log, backup queue).
- atomic_save(): Ghi file tạm → sync → rename; cập nhật SQLite trong transaction.
- get_case(): Đọc metadata + nội dung một ca.
- undo_change(): Hoàn tác bằng cách tạo reverse revision; không xóa lịch sử.
- rebuild_index_from_sources(): Dựng lại SQLite từ Markdown/JSON gốc.

Nguyên tắc:
- Nguồn chân lý là file Markdown/JSON trong cases/; SQLite chỉ là chỉ mục dẫn xuất.
- Sửa nội dung luôn tạo revision mới; không bao giờ ghi đè file revision cũ.
- idempotency_key ngăn agent gọi lại tạo mục trùng.
- expected_revision (optimistic concurrency) đảm bảo không ghi đè revision mà client chưa thấy.
"""

from __future__ import annotations

import json
import os
import sqlite3
import struct
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from . import config
from .models import (
    CaseMetadata,
    LifecycleStatus,
    RevisionContent,
    SemanticStatus,
    VerificationStatus,
)

# ─── Chuẩn hóa Unicode cho FTS ───────────────────────────────────────────────

def _normalize_text(text: str) -> str:
    """Lowercase, bỏ dấu tiếng Việt, NFC → ASCII folding cho FTS."""
    text = text.lower().replace("đ", "d")
    nfd = unicodedata.normalize("NFD", text)
    ascii_form = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
    return ascii_form


# ─── Schema SQLite ────────────────────────────────────────────────────────────

_SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- Bảng chính lưu metadata JSON của từng ca
CREATE TABLE IF NOT EXISTS cases (
    id              TEXT PRIMARY KEY,
    revision        INTEGER NOT NULL,
    metadata        TEXT NOT NULL,   -- JSON của CaseMetadata
    idempotency_key TEXT NOT NULL UNIQUE
);

-- FTS5 virtual table cho tìm kiếm toàn văn
-- Cột 'normalized' chứa văn bản đã chuẩn hóa (không dấu, chữ thường)
CREATE VIRTUAL TABLE IF NOT EXISTS cases_fts USING fts5(
    case_id UNINDEXED,
    title,
    summary,
    problem,
    cause,
    steps,
    aliases,
    tags,
    normalized,
    tokenize='unicode61 remove_diacritics 1'
);

-- Bảng lưu vector embedding (float32 * 768 = BLOB 3072 bytes)
CREATE TABLE IF NOT EXISTS embeddings (
    case_id    TEXT NOT NULL,
    vector     BLOB NOT NULL,
    generation INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    PRIMARY KEY (case_id, generation)
);

-- Hàng đợi backup một chiều
CREATE TABLE IF NOT EXISTS backup_queue (
    id         TEXT PRIMARY KEY,
    case_id    TEXT NOT NULL,
    revision   INTEGER NOT NULL,
    status     TEXT NOT NULL DEFAULT 'pending',  -- pending | done | error
    created_at TEXT NOT NULL
);

-- Undo log: mỗi thao tác lưu/sửa đều ghi lại
CREATE TABLE IF NOT EXISTS undo_log (
    operation_id    TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL,
    case_id         TEXT NOT NULL,
    old_revision    INTEGER,          -- NULL cho thao tác tạo mới
    new_revision    INTEGER NOT NULL,
    created_at      TEXT NOT NULL,
    reversed        INTEGER NOT NULL DEFAULT 0  -- 0=chưa hoàn tác, 1=đã hoàn tác
);

-- Sequence table cho ID allocator (dùng chung với id_allocator.py)
CREATE TABLE IF NOT EXISTS id_sequence (
    name  TEXT PRIMARY KEY,
    value INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO id_sequence (name, value) VALUES ('case_id', 0);
"""


def init_db(db_path: Path | None = None) -> None:
    """Khởi tạo SQLite schema. Idempotent — gọi nhiều lần vẫn an toàn."""
    path = db_path or config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=config.SQLITE_BUSY_TIMEOUT_MS / 1000)
    try:
        conn.executescript(_SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _case_dir(case_id: str, cases_dir: Path | None = None) -> Path:
    return (cases_dir or config.CASES_DIR) / case_id


def _revisions_dir(case_id: str, cases_dir: Path | None = None) -> Path:
    return _case_dir(case_id, cases_dir) / "revisions"


def _revision_path(case_id: str, revision: int, cases_dir: Path | None = None) -> Path:
    return _revisions_dir(case_id, cases_dir) / f"{revision:04d}.md"


def _metadata_path(case_id: str, cases_dir: Path | None = None) -> Path:
    return _case_dir(case_id, cases_dir) / "case.json"


def _serialize_metadata(meta: CaseMetadata) -> str:
    return meta.model_dump_json(indent=2)


def _deserialize_metadata(raw: str) -> CaseMetadata:
    return CaseMetadata.model_validate_json(raw)


def _content_to_markdown(content: RevisionContent) -> str:
    """Chuyển RevisionContent thành Markdown với YAML-like frontmatter."""
    data = content.model_dump(mode="json")
    lines: list[str] = ["---"]
    for key, val in data.items():
        if key in ("steps", "result", "context", "problem", "cause", "scope_notes",
                   "undo_procedure", "revision_reason"):
            continue  # đưa vào body
        if isinstance(val, (dict, list)):
            lines.append(f"{key}: {json.dumps(val, ensure_ascii=False)}")
        else:
            lines.append(f"{key}: {json.dumps(val, ensure_ascii=False)}")
    lines.append("---")
    lines.append("")
    lines.append(f"# {content.title}")
    lines.append("")
    lines.append(f"**Tóm tắt:** {content.summary}")
    lines.append("")
    lines.append("## Vấn đề / Triệu chứng")
    lines.append(content.problem)
    lines.append("")
    lines.append("## Bối cảnh")
    lines.append(content.context)
    lines.append("")
    lines.append("## Nguyên nhân")
    hypothesis_note = " *(giả thuyết)*" if content.cause_is_hypothesis else ""
    lines.append(content.cause + hypothesis_note)
    lines.append("")
    lines.append("## Các bước xử lý")
    lines.append(content.steps)
    lines.append("")
    lines.append("## Kết quả")
    lines.append(content.result)
    lines.append("")
    if content.failed_attempts:
        lines.append("## Những cách không hiệu quả")
        for fa in content.failed_attempts:
            lines.append(f"- {fa}")
        lines.append("")
    if content.warnings:
        lines.append("## Cảnh báo")
        for w in content.warnings:
            lines.append(f"- {w}")
        lines.append("")
    if content.scope_notes:
        lines.append("## Phạm vi ghi / Điều kiện áp dụng")
        lines.append(content.scope_notes)
        lines.append("")
    if content.undo_procedure:
        lines.append("## Cách hoàn tác")
        lines.append(content.undo_procedure)
        lines.append("")
    lines.append(f"*Lý do revision:* {content.revision_reason}")
    return "\n".join(lines)


def _parse_markdown_to_content(md_text: str) -> RevisionContent:
    """Parse Markdown (frontmatter JSON) trả về RevisionContent."""
    if not md_text.startswith("---"):
        raise ValueError("Markdown không có frontmatter")
    end = md_text.index("---", 3)
    frontmatter_text = md_text[3:end].strip()
    data: dict[str, Any] = {}
    for line in frontmatter_text.splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        key = k.strip().strip('"').strip("'")
        val_str = v.strip()
        try:
            data[key] = json.loads(val_str)
        except json.JSONDecodeError:
            data[key] = val_str
    # Trích body cho các trường dài
    body = md_text[end + 3:]
    _extract_section(body, "Vấn đề / Triệu chứng", "problem", data)
    _extract_section(body, "Bối cảnh", "context", data)
    _extract_section(body, "Nguyên nhân", "cause", data)
    _extract_section(body, "Các bước xử lý", "steps", data)
    _extract_section(body, "Kết quả", "result", data)
    _extract_section(body, "Phạm vi ghi / Điều kiện áp dụng", "scope_notes", data)
    _extract_section(body, "Cách hoàn tác", "undo_procedure", data)
    return RevisionContent.model_validate(data)


def _extract_section(body: str, heading: str, field: str, data: dict[str, Any]) -> None:
    """Trích nội dung section từ body Markdown vào dict data."""
    import re
    pattern = rf"## {re.escape(heading)}\n(.*?)(?=\n## |\Z)"
    m = re.search(pattern, body, re.DOTALL)
    if m:
        data[field] = m.group(1).strip()


# ─── Atomic write ─────────────────────────────────────────────────────────────

def _atomic_write(path: Path, content: str) -> None:
    """Ghi file theo kiểu tạm → fsync → rename để đảm bảo tính nguyên vẹn."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        tmp_path.replace(path)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise


# ─── FTS helpers ──────────────────────────────────────────────────────────────

def _insert_fts(conn: sqlite3.Connection, content: RevisionContent) -> None:
    """Chèn hoặc cập nhật bản ghi trong FTS5."""
    # Xóa bản ghi cũ nếu tồn tại
    conn.execute("DELETE FROM cases_fts WHERE case_id = ?", (content.case_id,))

    aliases_str = " ".join(content.aliases)
    tags_str    = " ".join(content.tags)

    raw_text = " ".join([
        content.title, content.summary, content.problem,
        content.cause, content.steps, aliases_str, tags_str,
    ])
    normalized = _normalize_text(raw_text)

    conn.execute(
        """
        INSERT INTO cases_fts (case_id, title, summary, problem, cause, steps,
                               aliases, tags, normalized)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            content.case_id,
            content.title,
            content.summary,
            content.problem,
            content.cause,
            content.steps,
            aliases_str,
            tags_str,
            normalized,
        ),
    )


# ─── atomic_save ──────────────────────────────────────────────────────────────

class ConflictError(Exception):
    """Raised khi expected_revision không khớp revision hiện tại."""
    pass


class IdempotentResult:
    """Returned khi idempotency_key đã tồn tại — không tạo ca mới."""
    def __init__(self, existing_case_id: str, existing_operation_id: str):
        self.case_id     = existing_case_id
        self.operation_id = existing_operation_id


def atomic_save(
    content: RevisionContent,
    metadata: CaseMetadata,
    db_path: Path | None = None,
    cases_dir: Path | None = None,
    expected_revision: Optional[int] = None,
) -> str:
    """
    Lưu một ca mới hoặc tạo revision mới.

    Quy trình:
    1. Kiểm tra idempotency_key — nếu đã có, trả operation_id cũ.
    2. Kiểm tra expected_revision (optimistic concurrency) nếu là revision thứ 2+.
    3. Ghi Markdown: file tạm → fsync → rename.
    4. Ghi case.json: file tạm → fsync → rename.
    5. Cập nhật SQLite: cases, FTS5, undo_log, backup_queue trong một transaction.

    Returns:
        operation_id (str) — mã hoàn tác.

    Raises:
        ConflictError: Nếu expected_revision không khớp.
        IdempotentResult: Không raise; hàm trả operation_id của lần gọi đầu.
    """
    path_db    = db_path    or config.DB_PATH
    path_cases = cases_dir  or config.CASES_DIR

    path_db.parent.mkdir(parents=True, exist_ok=True)
    path_cases.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(
        str(path_db),
        timeout=config.SQLITE_BUSY_TIMEOUT_MS / 1000,
        isolation_level=None,
    )
    try:
        conn.execute(f"PRAGMA busy_timeout={config.SQLITE_BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("BEGIN IMMEDIATE")

        # 1. Kiểm tra idempotency_key
        existing = conn.execute(
            "SELECT case_id, operation_id FROM undo_log WHERE idempotency_key = ? LIMIT 1",
            (metadata.idempotency_key,),
        ).fetchone()
        if existing:
            conn.execute("ROLLBACK")
            return existing[1]  # trả operation_id cũ

        # 2. Kiểm tra optimistic concurrency cho revision thứ 2+
        current_row = conn.execute(
            "SELECT revision FROM cases WHERE id = ?", (metadata.id,)
        ).fetchone()

        if current_row is not None:
            current_rev: int = current_row[0]
            if expected_revision is not None and current_rev != expected_revision:
                conn.execute("ROLLBACK")
                raise ConflictError(
                    f"Conflict: expected revision {expected_revision}, "
                    f"but current is {current_rev}. Hãy đọc lại revision {current_rev} trước."
                )
            old_rev = current_rev
            new_rev = current_rev + 1
        else:
            old_rev = None
            new_rev = 1

        # Cập nhật metadata với revision mới
        metadata = metadata.model_copy(update={"revision": new_rev})
        content  = content.model_copy(update={"revision": new_rev})

        # 3. Ghi Markdown (bên ngoài transaction để fsync không block SQLite)
        md_path = _revision_path(metadata.id, new_rev, path_cases)
        md_text = _content_to_markdown(content)

        # 4. Ghi case.json
        meta_path = _metadata_path(metadata.id, path_cases)
        meta_text = _serialize_metadata(metadata)

        # Thực hiện file I/O (ngoài lock tốt hơn, nhưng vẫn trong transaction logic)
        try:
            _atomic_write(md_path, md_text)
            _atomic_write(meta_path, meta_text)
        except Exception:
            conn.execute("ROLLBACK")
            raise

        # 5. Cập nhật SQLite
        operation_id = str(uuid.uuid4())
        now_iso = datetime.now(timezone.utc).isoformat()

        conn.execute(
            """
            INSERT INTO cases (id, revision, metadata, idempotency_key)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                revision = excluded.revision,
                metadata = excluded.metadata,
                idempotency_key = excluded.idempotency_key
            """,
            (metadata.id, new_rev, meta_text, metadata.idempotency_key),
        )

        _insert_fts(conn, content)

        conn.execute(
            """
            INSERT INTO undo_log (operation_id, idempotency_key, case_id,
                                  old_revision, new_revision, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (operation_id, metadata.idempotency_key, metadata.id,
             old_rev, new_rev, now_iso),
        )

        backup_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO backup_queue (id, case_id, revision, status, created_at)
            VALUES (?, ?, ?, 'pending', ?)
            """,
            (backup_id, metadata.id, new_rev, now_iso),
        )

        conn.execute("COMMIT")
        return operation_id

    except ConflictError:
        raise
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()


# ─── get_case ─────────────────────────────────────────────────────────────────

def get_case(
    case_id: str,
    revision: Optional[int] = None,
    db_path: Path | None = None,
    cases_dir: Path | None = None,
) -> tuple[CaseMetadata, RevisionContent]:
    """
    Đọc metadata và nội dung một ca.

    Args:
        case_id: Mã mục, ví dụ "CIV-0001".
        revision: Nếu None, đọc revision hiện hành; nếu có, đọc revision cụ thể.

    Returns:
        (CaseMetadata, RevisionContent)

    Raises:
        FileNotFoundError: Nếu ca hoặc revision không tồn tại.
    """
    path_cases = cases_dir or config.CASES_DIR
    meta_path  = _metadata_path(case_id, path_cases)

    if not meta_path.exists():
        raise FileNotFoundError(f"Không tìm thấy ca {case_id}")

    meta_text = meta_path.read_text(encoding="utf-8")
    metadata  = _deserialize_metadata(meta_text)

    rev = revision if revision is not None else metadata.revision
    md_path = _revision_path(case_id, rev, path_cases)

    if not md_path.exists():
        raise FileNotFoundError(f"Không tìm thấy revision {rev} của {case_id}")

    md_text = md_path.read_text(encoding="utf-8")
    content = _parse_markdown_to_content(md_text)

    return metadata, content


# ─── undo_change ──────────────────────────────────────────────────────────────

def undo_change(
    operation_id: str,
    db_path: Path | None = None,
    cases_dir: Path | None = None,
) -> bool:
    """
    Hoàn tác một thao tác lưu/sửa bằng cách tạo reverse revision.
    Không xóa vật lý lịch sử; revision cũ vẫn còn.
    Gọi lại cùng operation_id không tạo thêm thay đổi (idempotent).

    Returns:
        True nếu hoàn tác thành công; False nếu operation_id không tồn tại
        hoặc đã được hoàn tác rồi.
    """
    path_db    = db_path    or config.DB_PATH
    path_cases = cases_dir  or config.CASES_DIR

    conn = sqlite3.connect(
        str(path_db),
        timeout=config.SQLITE_BUSY_TIMEOUT_MS / 1000,
        isolation_level=None,
    )
    try:
        conn.execute(f"PRAGMA busy_timeout={config.SQLITE_BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("BEGIN IMMEDIATE")

        row = conn.execute(
            """
            SELECT case_id, old_revision, new_revision, reversed
            FROM undo_log WHERE operation_id = ?
            """,
            (operation_id,),
        ).fetchone()

        if not row:
            conn.execute("ROLLBACK")
            return False

        case_id, old_rev, new_rev, reversed_flag = row

        if reversed_flag:
            conn.execute("ROLLBACK")
            return False  # đã hoàn tác rồi, không làm gì thêm

        # Trường hợp hoàn tác ca mới (old_rev = None): đánh dấu lifecycle = deleted
        current_meta_path = _metadata_path(case_id, path_cases)
        if not current_meta_path.exists():
            conn.execute("ROLLBACK")
            return False

        meta_text = current_meta_path.read_text(encoding="utf-8")
        metadata  = _deserialize_metadata(meta_text)

        if old_rev is None:
            # Xóa logic: tạo revision đánh dấu deleted, lifecycle=deleted
            undo_content_text = (
                f"---\n"
                f'case_id: "{case_id}"\n'
                f'revision: {metadata.revision + 1}\n'
                f'title: "[UNDO] {metadata.title}"\n'
                f'summary: "Ca này đã bị hoàn tác (undo) — không hiện trong tìm kiếm thông thường."\n'
                f'problem: "[Hoàn tác]"\n'
                f'context: "[Hoàn tác]"\n'
                f'created_at: "{datetime.now(timezone.utc).isoformat()}"\n'
                f'revision_reason: "undo operation {operation_id}"\n'
                f"---\n\n# [UNDO] {metadata.title}\n\nCa này đã được hoàn tác.\n"
            )
            new_lifecycle = LifecycleStatus.deleted
        else:
            # Khôi phục về old_rev: đọc nội dung old_rev và tạo revision mới từ đó
            old_md_path = _revision_path(case_id, old_rev, path_cases)
            if old_md_path.exists():
                undo_content_text = old_md_path.read_text(encoding="utf-8")
            else:
                undo_content_text = (
                    f"---\n"
                    f'case_id: "{case_id}"\n'
                    f'revision: {metadata.revision + 1}\n'
                    f'title: "[UNDO] {metadata.title}"\n'
                    f'summary: "Hoàn tác về revision {old_rev}"\n'
                    f'problem: "[Hoàn tác]"\n'
                    f'context: "[Hoàn tác]"\n'
                    f'created_at: "{datetime.now(timezone.utc).isoformat()}"\n'
                    f'revision_reason: "undo to revision {old_rev}"\n'
                    f"---\n\n# [UNDO] {metadata.title}\n\n"
                    f"Hoàn tác về revision {old_rev}.\n"
                )
            new_lifecycle = metadata.lifecycle

        undo_rev = metadata.revision + 1
        undo_md_path = _revision_path(case_id, undo_rev, path_cases)
        _atomic_write(undo_md_path, undo_content_text)

        updated_meta = metadata.model_copy(update={
            "revision": undo_rev,
            "lifecycle": new_lifecycle,
            "updated_at": datetime.now(timezone.utc),
        })
        _atomic_write(current_meta_path, _serialize_metadata(updated_meta))

        now_iso = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE cases SET revision = ?, metadata = ? WHERE id = ?",
            (undo_rev, _serialize_metadata(updated_meta), case_id),
        )
        conn.execute(
            "UPDATE undo_log SET reversed = 1 WHERE operation_id = ?",
            (operation_id,),
        )
        # Ghi undo log cho thao tác hoàn tác này
        conn.execute(
            """
            INSERT INTO undo_log (operation_id, idempotency_key, case_id,
                                  old_revision, new_revision, created_at, reversed)
            VALUES (?, ?, ?, ?, ?, ?, 0)
            """,
            (
                f"undo-{operation_id}",
                f"undo-{operation_id}",
                case_id,
                metadata.revision,
                undo_rev,
                now_iso,
            ),
        )
        conn.execute("COMMIT")
        return True

    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()


# ─── rebuild_index_from_sources ───────────────────────────────────────────────

def rebuild_index_from_sources(
    db_path: Path | None = None,
    cases_dir: Path | None = None,
) -> int:
    """
    Quét toàn bộ cases/ để dựng lại SQLite từ Markdown/JSON gốc.
    Dùng khi SQLite bị hỏng hoặc bị xóa.

    Returns:
        Số mục được đưa vào index.
    """
    path_db    = db_path    or config.DB_PATH
    path_cases = cases_dir  or config.CASES_DIR

    # Xóa và tạo lại schema
    if path_db.exists():
        path_db.unlink()
    init_db(path_db)

    count = 0
    conn = sqlite3.connect(
        str(path_db),
        timeout=config.SQLITE_BUSY_TIMEOUT_MS / 1000,
        isolation_level=None,
    )
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(f"PRAGMA busy_timeout={config.SQLITE_BUSY_TIMEOUT_MS}")
        conn.execute("BEGIN")

        if not path_cases.exists():
            conn.execute("COMMIT")
            return 0

        for case_dir in sorted(path_cases.iterdir()):
            meta_path = case_dir / "case.json"
            if not meta_path.exists():
                continue
            try:
                meta_text = meta_path.read_text(encoding="utf-8")
                metadata  = _deserialize_metadata(meta_text)
            except Exception:
                continue

            # Tìm revision lớn nhất
            revisions_dir = case_dir / "revisions"
            if not revisions_dir.exists():
                continue

            md_files = sorted(revisions_dir.glob("*.md"))
            if not md_files:
                continue

            # Đọc revision hiện hành
            current_rev_path = revisions_dir / f"{metadata.revision:04d}.md"
            if not current_rev_path.exists():
                current_rev_path = md_files[-1]

            try:
                md_text = current_rev_path.read_text(encoding="utf-8")
                content = _parse_markdown_to_content(md_text)
            except Exception:
                continue

            conn.execute(
                """
                INSERT OR REPLACE INTO cases (id, revision, metadata, idempotency_key)
                VALUES (?, ?, ?, ?)
                """,
                (metadata.id, metadata.revision, meta_text, metadata.idempotency_key),
            )

            # Cập nhật id_sequence nếu cần
            civ_num = int(metadata.id.split("-")[1])
            conn.execute(
                """
                UPDATE id_sequence SET value = MAX(value, ?)
                WHERE name = 'case_id'
                """,
                (civ_num,),
            )

            _insert_fts(conn, content)
            count += 1

        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()

    return count


# ─── FTS Search (dùng cho tests) ──────────────────────────────────────────────

def fts_search(
    query: str,
    db_path: Path | None = None,
    max_results: int = 5,
) -> list[dict[str, Any]]:
    """
    Tìm kiếm FTS5 đơn giản. Trả danh sách dict với case_id và rank.
    Tìm kiếm cả văn bản gốc và văn bản đã chuẩn hóa.
    """
    path_db = db_path or config.DB_PATH
    if not path_db.exists():
        return []

    normalized_query = _normalize_text(query)
    conn = sqlite3.connect(str(path_db), timeout=5)
    try:
        # Tìm theo từ khóa gốc
        rows = conn.execute(
            """
            SELECT case_id, rank
            FROM cases_fts
            WHERE cases_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (query, max_results),
        ).fetchall()

        # Nếu không tìm được, thử với normalized query
        if not rows and normalized_query != query:
            rows = conn.execute(
                """
                SELECT case_id, rank
                FROM cases_fts
                WHERE normalized MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (normalized_query, max_results),
            ).fetchall()

        return [{"case_id": r[0], "rank": r[1]} for r in rows]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()
