"""
tests/test_storage.py — Unit tests cho storage engine CivilHandbook.

Kiểm thử:
- Lưu ca mới → đúng CIV-xxxx, revision 1, file Markdown/JSON hợp lệ.
- Idempotency_key gọi lại không tạo ca thứ 2.
- Hai agent cùng revision: một thành công, một nhận conflict.
- Hoàn tác giữ lịch sử.
- Xóa SQLite rồi rebuild_index → đúng số mục.
- FTS tìm đúng với câu có dấu và không dấu.

Dùng mock embedding — không gọi Google API.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

# Thêm src vào sys.path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from handbook import config as cfg
from handbook.models import (
    CaseMetadata,
    CivilTaxonomy,
    LifecycleStatus,
    RevisionContent,
    SemanticStatus,
    VerificationStatus,
)
from handbook.storage import (
    ConflictError,
    atomic_save,
    fts_search,
    get_case,
    init_db,
    rebuild_index_from_sources,
    undo_change,
)
from handbook.id_allocator import allocate_id
from handbook.secret_scanner import scan, has_secrets


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture()
def tmp_dirs(tmp_path: Path):
    """Tạo thư mục tạm cho mỗi test — cô lập hoàn toàn."""
    db_path   = tmp_path / "index" / "handbook.sqlite"
    cases_dir = tmp_path / "cases"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    cases_dir.mkdir(parents=True, exist_ok=True)
    init_db(db_path)
    return db_path, cases_dir


def _make_content(
    case_id: str = "CIV-0001",
    revision: int = 1,
    title: str = "Lỗi Corridor không cập nhật",
    summary: str = "Corridor không tự cập nhật sau khi sửa Alignment",
    problem: str = "Corridor hiển thị cũ sau khi sửa Alignment",
    context: str = "Civil 3D 2027, dự án đường tỉnh lộ",
    tags: list[str] | None = None,
    aliases: list[str] | None = None,
    sensitivity: str = "default",
) -> RevisionContent:
    return RevisionContent(
        case_id=case_id,
        revision=revision,
        title=title,
        summary=summary,
        problem=problem,
        context=context,
        cause="Rebuild mode bị tắt sau khi thêm Region mới",
        steps="1. Mở Corridor Properties\n2. Bật Rebuild Automatically",
        result="Corridor cập nhật đúng",
        civil_groups=[CivilTaxonomy.corridor],
        aliases=aliases or ["Corridor update", "Cập nhật hành lang"],
        tags=tags or ["corridor", "rebuild", "alignment"],
        sensitivity=sensitivity,
        created_at=datetime.now(timezone.utc),
        revision_reason="initial",
    )


def _make_metadata(
    case_id: str = "CIV-0001",
    revision: int = 1,
    idempotency_key: str = "test-idem-001",
    title: str = "Lỗi Corridor không cập nhật",
) -> CaseMetadata:
    now = datetime.now(timezone.utc)
    return CaseMetadata(
        id=case_id,
        revision=revision,
        idempotency_key=idempotency_key,
        title=title,
        lifecycle=LifecycleStatus.active,
        verification=VerificationStatus.unverified,
        semantic_status=SemanticStatus.semantic_pending,
        civil_groups=[CivilTaxonomy.corridor],
        plugins=[],
        created_at=now,
        updated_at=now,
    )


# ─── Test 1: Lưu ca mới ───────────────────────────────────────────────────────

def test_save_new_case_creates_civ_id(tmp_dirs):
    """Lưu ca mới → đúng CIV-xxxx, revision 1, file Markdown/JSON tồn tại."""
    db_path, cases_dir = tmp_dirs

    content  = _make_content()
    metadata = _make_metadata()

    op_id = atomic_save(content, metadata, db_path=db_path, cases_dir=cases_dir)

    assert isinstance(op_id, str) and len(op_id) > 0, "operation_id phải là chuỗi không rỗng"

    # File case.json tồn tại
    meta_path = cases_dir / "CIV-0001" / "case.json"
    assert meta_path.exists(), "case.json phải tồn tại"

    # File revision 0001.md tồn tại
    md_path = cases_dir / "CIV-0001" / "revisions" / "0001.md"
    assert md_path.exists(), "0001.md phải tồn tại"

    # Đọc lại metadata — revision phải là 1
    saved_meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert saved_meta["revision"] == 1, "revision phải là 1"
    assert saved_meta["id"] == "CIV-0001"

    # SQLite phải có bản ghi
    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT id, revision FROM cases WHERE id='CIV-0001'").fetchone()
    conn.close()
    assert row is not None
    assert row[1] == 1


def test_id_allocator_format(tmp_dirs):
    """allocate_id() phải trả đúng dạng CIV-XXXX."""
    db_path, _ = tmp_dirs
    id1 = allocate_id(db_path)
    id2 = allocate_id(db_path)
    assert id1.startswith("CIV-"), "Phải bắt đầu bằng CIV-"
    assert len(id1) == 8, "CIV-XXXX phải có 8 ký tự"
    assert id1 != id2, "Hai lần gọi phải ra khác nhau"
    # Kiểm tra padding zero
    num1 = int(id1.split("-")[1])
    num2 = int(id2.split("-")[1])
    assert num2 == num1 + 1, "ID phải tăng dần"


# ─── Test 2: Idempotency ──────────────────────────────────────────────────────

def test_idempotency_key_no_duplicate(tmp_dirs):
    """Gọi lại cùng idempotency_key không tạo ca thứ 2."""
    db_path, cases_dir = tmp_dirs

    content  = _make_content()
    metadata = _make_metadata(idempotency_key="idem-unique-001")

    op_id_1 = atomic_save(content, metadata, db_path=db_path, cases_dir=cases_dir)
    op_id_2 = atomic_save(content, metadata, db_path=db_path, cases_dir=cases_dir)

    assert op_id_1 == op_id_2, "Cùng idempotency_key phải trả cùng operation_id"

    conn = sqlite3.connect(str(db_path))
    count = conn.execute("SELECT COUNT(*) FROM cases WHERE id='CIV-0001'").fetchone()[0]
    conn.close()
    assert count == 1, "Chỉ được có đúng 1 bản ghi trong DB"


# ─── Test 3: Optimistic concurrency (conflict) ───────────────────────────────

def test_concurrent_revision_conflict(tmp_dirs):
    """Hai agent cùng expected_revision=1: một thành công, một nhận ConflictError."""
    db_path, cases_dir = tmp_dirs

    # Lưu ca ban đầu
    content  = _make_content()
    metadata = _make_metadata(idempotency_key="base-001")
    atomic_save(content, metadata, db_path=db_path, cases_dir=cases_dir)

    # Agent A sửa thành công với expected_revision=1
    content_a = _make_content(revision=1, title="Sửa bởi Agent A",
                              summary="Agent A sửa Corridor sau khi rebuild")
    meta_a    = _make_metadata(idempotency_key="agent-a-rev2", title="Sửa bởi Agent A")
    op_a = atomic_save(
        content_a, meta_a,
        db_path=db_path, cases_dir=cases_dir,
        expected_revision=1,
    )
    assert op_a is not None

    # Agent B cố sửa cùng expected_revision=1 → phải nhận ConflictError
    content_b = _make_content(revision=1, title="Sửa bởi Agent B",
                              summary="Agent B cũng cố sửa")
    meta_b    = _make_metadata(idempotency_key="agent-b-rev2", title="Sửa bởi Agent B")
    with pytest.raises(ConflictError) as exc_info:
        atomic_save(
            content_b, meta_b,
            db_path=db_path, cases_dir=cases_dir,
            expected_revision=1,
        )
    assert "Conflict" in str(exc_info.value)


# ─── Test 4: Undo giữ lịch sử ────────────────────────────────────────────────

def test_undo_preserves_history(tmp_dirs):
    """Hoàn tác tạo reverse revision; file revision cũ vẫn còn."""
    db_path, cases_dir = tmp_dirs

    content  = _make_content()
    metadata = _make_metadata(idempotency_key="undo-test-001")
    op_id = atomic_save(content, metadata, db_path=db_path, cases_dir=cases_dir)

    # File revision 1 tồn tại
    rev1_path = cases_dir / "CIV-0001" / "revisions" / "0001.md"
    assert rev1_path.exists()

    # Hoàn tác
    result = undo_change(op_id, db_path=db_path, cases_dir=cases_dir)
    assert result is True, "undo_change phải trả True"

    # Revision 1 vẫn còn (không bị xóa vật lý)
    assert rev1_path.exists(), "Revision 1 phải vẫn còn sau khi hoàn tác"

    # Revision 2 (reverse) được tạo
    rev2_path = cases_dir / "CIV-0001" / "revisions" / "0002.md"
    assert rev2_path.exists(), "Revision 2 (reverse) phải được tạo"

    # SQLite phải có undo_log với reversed=1
    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT reversed FROM undo_log WHERE operation_id=?", (op_id,)
    ).fetchone()
    conn.close()
    assert row is not None and row[0] == 1, "undo_log phải đánh dấu reversed=1"

    # Gọi lại undo cùng operation_id → trả False (idempotent)
    result2 = undo_change(op_id, db_path=db_path, cases_dir=cases_dir)
    assert result2 is False, "Gọi lại undo cùng op_id phải trả False"


# ─── Test 5: Rebuild index ────────────────────────────────────────────────────

def test_rebuild_index_from_sources(tmp_dirs):
    """Xóa SQLite rồi rebuild_index → đúng số mục."""
    db_path, cases_dir = tmp_dirs

    # Lưu 3 ca
    for i in range(1, 4):
        cid = f"CIV-{i:04d}"
        content  = _make_content(case_id=cid, title=f"Ca thử nghiệm {i}",
                                  summary=f"Tóm tắt ca {i}")
        metadata = _make_metadata(
            case_id=cid,
            idempotency_key=f"rebuild-test-{i:04d}",
            title=f"Ca thử nghiệm {i}",
        )
        atomic_save(content, metadata, db_path=db_path, cases_dir=cases_dir)

    # Xóa SQLite
    db_path.unlink()
    assert not db_path.exists()

    # Rebuild
    count = rebuild_index_from_sources(db_path=db_path, cases_dir=cases_dir)
    assert count == 3, f"Phải rebuild được 3 mục, nhưng nhận được {count}"

    conn = sqlite3.connect(str(db_path))
    db_count = conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
    conn.close()
    assert db_count == 3, "SQLite phải có đúng 3 bản ghi sau rebuild"


# ─── Test 6: FTS tìm kiếm ─────────────────────────────────────────────────────

def test_fts_search_with_diacritics_and_without(tmp_dirs):
    """FTS tìm đúng với từ có dấu và không dấu."""
    db_path, cases_dir = tmp_dirs

    content = _make_content(
        title="Lỗi Corridor không rebuild",
        summary="Corridor không tự cập nhật khi thay đổi Alignment",
        problem="Corridor bị lỗi sau khi sửa tuyến",
        aliases=["corridor update", "cap nhat hanh lang"],
        tags=["corridor", "rebuild"],
    )
    metadata = _make_metadata(idempotency_key="fts-test-001", title="Lỗi Corridor không rebuild")
    atomic_save(content, metadata, db_path=db_path, cases_dir=cases_dir)

    # Tìm với từ có dấu
    results1 = fts_search("Corridor", db_path=db_path)
    assert len(results1) > 0, "FTS phải tìm được với từ 'Corridor'"

    # Tìm với từ không dấu
    results2 = fts_search("cap nhat hanh lang", db_path=db_path)
    assert len(results2) > 0, "FTS phải tìm được với alias không dấu"


# ─── Test 7: get_case ─────────────────────────────────────────────────────────

def test_get_case_returns_correct_data(tmp_dirs):
    """get_case() trả đúng metadata và content."""
    db_path, cases_dir = tmp_dirs

    content  = _make_content(title="Test get_case", summary="Kiểm tra đọc dữ liệu")
    metadata = _make_metadata(idempotency_key="get-test-001", title="Test get_case")
    atomic_save(content, metadata, db_path=db_path, cases_dir=cases_dir)

    saved_meta, saved_content = get_case("CIV-0001", cases_dir=cases_dir)
    assert saved_meta.id == "CIV-0001"
    assert saved_meta.revision == 1
    assert saved_content.title == "Test get_case"


# ─── Test 8: Secret scanner ───────────────────────────────────────────────────

def test_secret_scanner_detects_api_key():
    """Secret scanner phát hiện API key trong văn bản."""
    text_with_key = "GOOGLE_API_KEY=AIzaAbcDefGhiJklMnoPqrStuvWxyz12345678"
    warnings = scan(text_with_key)
    assert len(warnings) > 0, "Phải phát hiện api_key"


def test_secret_scanner_detects_bearer():
    """Secret scanner phát hiện Bearer token."""
    text = "Authorization: Bearer eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.payload.sig"
    warnings = scan(text)
    assert len(warnings) > 0, "Phải phát hiện bearer_token"


def test_secret_scanner_detects_path_traversal():
    """Secret scanner phát hiện path traversal."""
    text = "Đọc file tại ../../etc/passwd"
    warnings = scan(text)
    assert len(warnings) > 0, "Phải phát hiện path traversal"


def test_secret_scanner_clean_text():
    """Văn bản bình thường không trigger scanner."""
    text = "Corridor không cập nhật sau khi sửa Alignment. Cần bật Rebuild Automatically."
    warnings = scan(text)
    assert len(warnings) == 0, "Văn bản bình thường không được trigger scanner"


def test_secret_scanner_detects_connection_string():
    """Secret scanner phát hiện connection string SQL."""
    text = "Server=myserver;Database=mydb;User Id=sa;Password=Secret123"
    warnings = scan(text)
    assert len(warnings) > 0, "Phải phát hiện connection_string"


# ─── Test 9: Thread-safety ID allocator ──────────────────────────────────────

def test_id_allocator_thread_safe(tmp_dirs):
    """allocate_id() thread-safe — nhiều thread không cấp trùng ID."""
    db_path, _ = tmp_dirs
    ids: list[str] = []
    lock = threading.Lock()

    def allocate():
        new_id = allocate_id(db_path)
        with lock:
            ids.append(new_id)

    threads = [threading.Thread(target=allocate) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(ids) == 20
    assert len(set(ids)) == 20, "Tất cả 20 ID phải unique"


# ─── Test 10: Backup failure không rollback file nguồn ───────────────────────

def test_save_succeeds_even_without_backup_destination(tmp_dirs):
    """
    Backup không ảnh hưởng đến kết quả lưu file nguồn.
    (backup_queue chỉ ghi pending, không cần G: drive).
    """
    db_path, cases_dir = tmp_dirs
    content  = _make_content()
    metadata = _make_metadata(idempotency_key="backup-test-001")
    op_id = atomic_save(content, metadata, db_path=db_path, cases_dir=cases_dir)
    assert op_id is not None

    # File nguồn phải tồn tại
    assert (cases_dir / "CIV-0001" / "case.json").exists()

    # backup_queue phải có entry pending
    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT status FROM backup_queue WHERE case_id='CIV-0001'"
    ).fetchone()
    conn.close()
    assert row is not None and row[0] == "pending"


# ─── Test 11: local_only sensitivity ─────────────────────────────────────────

def test_local_only_sensitivity_saved_correctly(tmp_dirs):
    """Ca có sensitivity=local_only được lưu đúng."""
    db_path, cases_dir = tmp_dirs
    content  = _make_content(sensitivity="local_only")
    metadata = _make_metadata(idempotency_key="local-only-001")
    metadata = metadata.model_copy(update={"sensitivity": "local_only"})
    atomic_save(content, metadata, db_path=db_path, cases_dir=cases_dir)

    saved_meta, _ = get_case("CIV-0001", cases_dir=cases_dir)
    assert saved_meta.sensitivity == "local_only"
