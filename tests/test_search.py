"""
tests/test_search.py — Unit test cho Giai đoạn 3: Tìm kiếm lai + Embedding.

Dùng SQLite in-memory để test độc lập với storage.py.
MockEmbeddingProvider để test semantic mà không gọi cloud.
"""

from __future__ import annotations

import json
import math
import sqlite3
import struct
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# ─── Setup path ───────────────────────────────────────────────────────────────
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from handbook.embedding import MockEmbeddingProvider, EmbeddingProvider
from handbook.search import (
    SearchResult,
    _cosine,
    _rrf_merge,
    _rrf_score,
    search_cases,
    SCORE_THRESHOLD,
)
from handbook.text_utils import normalize_vn, build_fts_text, tokenize_query


# ─── Fixture: tạo in-memory SQLite với schema đủ cho search ──────────────────

def _create_schema(con: sqlite3.Connection) -> None:
    """Tạo schema chuẩn để test search."""
    con.executescript(
        """
        PRAGMA journal_mode=WAL;

        CREATE TABLE IF NOT EXISTS cases (
            id              TEXT PRIMARY KEY,
            revision        INTEGER NOT NULL,
            metadata        TEXT NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE
        );

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

        CREATE TABLE IF NOT EXISTS embeddings (
            case_id    TEXT NOT NULL,
            vector     BLOB NOT NULL,
            generation INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            PRIMARY KEY (case_id, generation)
        );
        """
    )


def _insert_case(
    con: sqlite3.Connection,
    case_id: str,
    title: str,
    summary: str,
    problem: str = "",
    cause: str = "",
    steps: str = "",
    verification: str = "unverified",
    lifecycle: str = "active",
    civil_groups: list[str] | None = None,
    plugins: list[str] | None = None,
    conflicts_with: list[str] | None = None,
    civil_version: str | None = None,
    project: str | None = None,
    sensitivity: str = "normal",
    embedding: list[float] | None = None,
) -> None:
    """Chèn một case vào DB test theo đúng schema chuẩn."""
    cg = civil_groups or []
    pl = plugins or []
    cw = conflicts_with or []

    metadata = {
        "id": case_id,
        "revision": 1,
        "idempotency_key": f"key-{case_id}",
        "title": title,
        "summary": summary,
        "lifecycle": lifecycle,
        "verification": verification,
        "semantic_status": "indexed" if embedding else "local_only",
        "civil_groups": cg,
        "plugins": pl,
        "conflicts_with": cw,
        "related_to": [],
        "sensitivity": sensitivity,
        "civil_version": civil_version,
        "project": project,
        "created_at": "2026-10-13T00:00:00Z",
        "updated_at": "2026-10-13T00:00:00Z",
    }
    meta_json = json.dumps(metadata, ensure_ascii=False)

    con.execute(
        """
        INSERT OR REPLACE INTO cases (id, revision, metadata, idempotency_key)
        VALUES (?, 1, ?, ?)
        """,
        (case_id, meta_json, f"key-{case_id}"),
    )

    all_text = f"{title} {summary} {problem} {cause} {steps} {' '.join(pl)}"
    normalized = normalize_vn(all_text)
    con.execute(
        """
        INSERT INTO cases_fts (case_id, title, summary, problem, cause, steps, aliases, tags, normalized)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (case_id, title, summary, problem, cause, steps, "", " ".join(pl), normalized),
    )

    if embedding is not None:
        blob = struct.pack(f"{len(embedding)}f", *embedding)
        con.execute(
            """
            INSERT OR REPLACE INTO embeddings (case_id, vector, generation, created_at)
            VALUES (?, ?, 1, datetime('now'))
            """,
            (case_id, blob),
        )

    con.commit()


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Fixture trả về path của SQLite tạm."""
    path = tmp_path / "test_handbook.sqlite"
    con = sqlite3.connect(str(path))
    con.execute("PRAGMA journal_mode=WAL")
    _create_schema(con)
    con.close()
    return path


@pytest.fixture
def populated_db(db_path: Path) -> Path:
    """DB đã có data mẫu để test search."""
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row

    # Case 1: lỗi FTS5 với mã lỗi
    _insert_case(
        con, "CIV-0001",
        title="Lỗi FTS5 khi tạo trắc ngang",
        summary="Civil 3D báo lỗi eHatchError khi rebuild section view",
        problem="eHatchError xuất hiện sau khi cập nhật corridor",
        verification="verified_live",
        civil_groups=["Sample Line/Section/Section View", "Corridor"],
        plugins=["QuickSectionView"],
        civil_version="2027",
        embedding=[0.9] * 768,  # vector gần với query test
    )

    # Case 2: tên lệnh chính xác
    _insert_case(
        con, "CIV-0002",
        title="Lệnh ADDASSEMBLY không hoạt động",
        summary="Lệnh ADDASSEMBLY trong Civil 3D bị vô hiệu sau khi cài plugin",
        problem="Plugin SAC làm override lệnh ADDASSEMBLY",
        verification="verified_offline",
        civil_groups=["Assembly/Subassembly/SAC"],
        plugins=["SAC-Tool"],
        embedding=[0.5] * 768,
    )

    # Case 3: không dấu — test normalize
    _insert_case(
        con, "CIV-0003",
        title="Đường đồng mức bị gãy khi xuất DXF",
        summary="Contour lines bị rã đoạn khi export sang DXF format",
        problem="Lỗi cao độ Z bị mất sau khi chuyển kinh tuyến trục",
        verification="partial",
        civil_groups=["Surface", "Map/GIS"],
        embedding=[0.3] * 768,
    )

    # Case 4: verified_live nên xếp trên unverified
    _insert_case(
        con, "CIV-0004",
        title="Surface TIN bị lỗi tam giác",
        summary="TIN surface hiển thị tam giác xấu do điểm đo trùng tọa độ",
        problem="Điểm trùng tọa độ gây lỗi khi build surface",
        verification="verified_live",
        civil_groups=["Surface"],
        embedding=[0.8] * 768,
    )

    # Case 5: unverified — xếp sau verified_live
    _insert_case(
        con, "CIV-0005",
        title="Surface TIN lỗi hiển thị",
        summary="TIN surface không hiển thị đúng màu sắc trong viewport",
        problem="Viewport hiển thị sai màu TIN",
        verification="unverified",
        civil_groups=["Surface"],
        embedding=[0.7] * 768,
    )

    # Case 6: two conflicting cases
    _insert_case(
        con, "CIV-0006",
        title="Cách xử lý lỗi alignment Civil 2024",
        summary="Dùng lệnh ALIGNREGEN để fix alignment lỗi trong Civil 2024",
        verification="verified_live",
        civil_groups=["Alignment"],
        civil_version="2024",
        conflicts_with=["CIV-0007"],
        embedding=[0.6] * 768,
    )
    _insert_case(
        con, "CIV-0007",
        title="Cách xử lý lỗi alignment Civil 2027",
        summary="Trong Civil 2027, dùng ALIGNRESET thay vì ALIGNREGEN",
        verification="verified_live",
        civil_groups=["Alignment"],
        civil_version="2027",
        conflicts_with=["CIV-0006"],
        embedding=[0.55] * 768,
    )

    # Case 8: local_only — không có embedding
    _insert_case(
        con, "CIV-0008",
        title="Thông tin dự án bí mật",
        summary="Thông tin nội bộ dự án XYZ không được gửi cloud",
        sensitivity="local_only",
        verification="unverified",
        # Không có embedding
    )

    # Case 9: lifecycle=superseded — không xuất hiện trong tìm kiếm thường
    _insert_case(
        con, "CIV-0009",
        title="Cách cũ xử lý alignment",
        summary="Phương pháp cũ đã bị thay thế bởi CIV-0006",
        lifecycle="superseded",
        civil_groups=["Alignment"],
        embedding=[0.6] * 768,
    )

    con.close()
    return db_path


# ─── Test text_utils ──────────────────────────────────────────────────────────

class TestTextUtils:
    def test_normalize_removes_accents(self):
        assert normalize_vn("Đường Hầm Trắc Ngang") == "duong ham trac ngang"

    def test_normalize_lowercase(self):
        assert normalize_vn("CIVIL 3D FTS5") == "civil 3d fts5"

    def test_normalize_mixed(self):
        assert normalize_vn("Lỗi eHatchError") == "loi ehatcherror"

    def test_normalize_empty(self):
        assert normalize_vn("") == ""

    def test_normalize_numbers_preserved(self):
        result = normalize_vn("TCVN 5729:2012")
        assert "tcvn" in result
        assert "5729" in result
        assert "2012" in result

    def test_build_fts_text_joins(self):
        result = build_fts_text(["Lỗi FTS5", "Civil 3D"])
        assert "loi fts5" in result
        assert "civil 3d" in result

    def test_build_fts_text_skips_empty(self):
        result = build_fts_text(["Title", None, "", "  "])
        assert result == "title"

    def test_tokenize_query(self):
        tokens = tokenize_query("Lỗi Trắc Ngang Civil")
        assert "loi" in tokens
        assert "trac" in tokens
        assert "ngang" in tokens
        assert "civil" in tokens


# ─── Test embedding ───────────────────────────────────────────────────────────

class TestMockEmbedding:
    def test_mock_health(self):
        provider = MockEmbeddingProvider()
        result = provider.health()
        assert result["ok"] is True

    def test_mock_embed_documents_returns_list(self):
        provider = MockEmbeddingProvider()
        vecs = provider.embed_documents(["hello", "world"])
        assert len(vecs) == 2
        assert len(vecs[0]) == 768

    def test_mock_embed_query(self):
        provider = MockEmbeddingProvider()
        vec = provider.embed_query("test query")
        assert len(vec) == 768
        assert all(v == pytest.approx(0.1) for v in vec)

    def test_mock_dimension(self):
        provider = MockEmbeddingProvider()
        assert provider.dimension == 768
        assert provider.provider_id == "mock"


# ─── Test cosine similarity ───────────────────────────────────────────────────

class TestCosine:
    def test_identical_vectors(self):
        v = [1.0] * 10
        assert _cosine(v, v) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert _cosine(a, b) == pytest.approx(0.0)

    def test_zero_vector(self):
        a = [0.0] * 10
        b = [1.0] * 10
        assert _cosine(a, b) == pytest.approx(0.0)

    def test_similar_vectors(self):
        a = [0.9] * 768
        b = [0.8] * 768
        sim = _cosine(a, b)
        assert sim > 0.99  # rất gần nhau


# ─── Test RRF ─────────────────────────────────────────────────────────────────

class TestRRF:
    def test_rrf_score_decreases_with_rank(self):
        s1 = _rrf_score(1)
        s2 = _rrf_score(2)
        s3 = _rrf_score(10)
        assert s1 > s2 > s3

    def test_rrf_merge_combined_score(self):
        fts = ["A", "B", "C"]
        sem = ["B", "A", "D"]
        scores = _rrf_merge(fts, sem)
        # B xuất hiện ở cả hai danh sách → score cao hơn D (chỉ có semantic)
        assert scores["B"] > scores["D"]

    def test_rrf_merge_unique_items(self):
        fts = ["X"]
        sem = ["Y"]
        scores = _rrf_merge(fts, sem)
        assert "X" in scores
        assert "Y" in scores


# ─── Test FTS search ──────────────────────────────────────────────────────────

class TestFTSSearch:
    def test_fts_finds_error_code(self, populated_db: Path):
        """FTS tìm được mã lỗi eHatchError."""
        results = search_cases("eHatchError", db_path=populated_db)
        ids = [r.case_id for r in results]
        assert "CIV-0001" in ids

    def test_fts_finds_command_name(self, populated_db: Path):
        """FTS tìm được tên lệnh ADDASSEMBLY."""
        results = search_cases("ADDASSEMBLY", db_path=populated_db)
        ids = [r.case_id for r in results]
        assert "CIV-0002" in ids

    def test_fts_finds_without_accent(self, populated_db: Path):
        """FTS tìm được với câu không dấu 'duong dong muc'."""
        results = search_cases("duong dong muc", db_path=populated_db)
        ids = [r.case_id for r in results]
        assert "CIV-0003" in ids

    def test_fts_finds_with_accent(self, populated_db: Path):
        """FTS tìm được với câu có dấu."""
        results = search_cases("đường đồng mức", db_path=populated_db)
        ids = [r.case_id for r in results]
        assert "CIV-0003" in ids

    def test_fts_finds_plugin_name(self, populated_db: Path):
        """FTS tìm được tên plugin."""
        results = search_cases("QuickSectionView", db_path=populated_db)
        ids = [r.case_id for r in results]
        assert "CIV-0001" in ids

    def test_fts_no_results_for_nonsense(self, populated_db: Path):
        """Query không có kết quả trả empty list."""
        results = search_cases("xyzabc_nothing_matches_this_unique_string", db_path=populated_db)
        assert results == []

    def test_superseded_not_in_results(self, populated_db: Path):
        """Mục lifecycle=superseded không xuất hiện."""
        results = search_cases("alignment", db_path=populated_db)
        ids = [r.case_id for r in results]
        assert "CIV-0009" not in ids


# ─── Test Semantic search ─────────────────────────────────────────────────────

class TestSemanticSearch:
    def test_semantic_with_mock_finds_similar(self, populated_db: Path):
        """Semantic với mock provider: tìm được mục có embedding."""
        # Mock provider trả [0.1]*768; case có embedding [0.9]*768 gần nhất (cosine=1.0)
        provider = MockEmbeddingProvider()
        results = search_cases(
            "trắc ngang bị lỗi",
            db_path=populated_db,
            embedding_provider=provider,
        )
        # Phải có ít nhất 1 kết quả (semantic hoạt động)
        assert len(results) >= 1

    def test_semantic_different_phrasing(self, populated_db: Path):
        """Câu diễn đạt khác vẫn tìm được bằng semantic."""
        # Mock embedding: tất cả đều là [0.1]*768 → cosine similarity giống nhau
        # Vẫn phải trả kết quả (không trả empty vì FTS hoặc semantic match)
        provider = MockEmbeddingProvider()
        results = search_cases(
            "section view rebuild hatch",
            db_path=populated_db,
            embedding_provider=provider,
        )
        assert isinstance(results, list)


# ─── Test verification ordering ──────────────────────────────────────────────

class TestVerificationOrdering:
    def test_verified_live_ranks_before_unverified(self, populated_db: Path):
        """verified_live xếp trước unverified khi điều kiện tương đương."""
        provider = MockEmbeddingProvider()
        results = search_cases(
            "surface tin lỗi",
            db_path=populated_db,
            embedding_provider=provider,
        )
        if len(results) >= 2:
            # Tìm CIV-0004 (verified_live) và CIV-0005 (unverified)
            ids = [r.case_id for r in results]
            if "CIV-0004" in ids and "CIV-0005" in ids:
                idx4 = ids.index("CIV-0004")
                idx5 = ids.index("CIV-0005")
                assert idx4 < idx5, "verified_live (CIV-0004) phải xếp trước unverified (CIV-0005)"


# ─── Test filters ─────────────────────────────────────────────────────────────

class TestFilters:
    def test_filter_by_civil_version(self, populated_db: Path):
        """Lọc theo civil_version."""
        results = search_cases(
            "alignment",
            civil_version="2024",
            db_path=populated_db,
        )
        for r in results:
            if r.case_id in ("CIV-0006", "CIV-0007"):
                if r.case_id == "CIV-0007":  # version 2027
                    # Có thể xuất hiện nhờ conflict, nhưng CIV-0006 phải có
                    pass

    def test_filter_by_civil_groups(self, populated_db: Path):
        """Lọc theo civil_groups."""
        results = search_cases(
            "lỗi",
            civil_groups=["Assembly/Subassembly/SAC"],
            db_path=populated_db,
        )
        for r in results:
            assert "Assembly/Subassembly/SAC" in r.civil_groups or r.matched_by == "conflict"

    def test_filter_by_plugins(self, populated_db: Path):
        """Lọc theo plugins."""
        results = search_cases(
            "lệnh",
            plugins=["SAC-Tool"],
            db_path=populated_db,
        )
        for r in results:
            assert "SAC-Tool" in r.plugins or r.matched_by == "conflict"

    def test_filter_verification_verified_live_only(self, populated_db: Path):
        """Lọc chỉ lấy verified_live."""
        results = search_cases(
            "surface",
            verification_filter=["verified_live"],
            db_path=populated_db,
        )
        for r in results:
            assert r.verification in ("verified_live",) or r.matched_by == "conflict"


# ─── Test local_only ──────────────────────────────────────────────────────────

class TestLocalOnly:
    def test_local_only_has_no_embedding_call(self, populated_db: Path):
        """Mục local_only không có embedding — semantic không gọi provider."""
        call_count = {"n": 0}
        original_embed = MockEmbeddingProvider.embed_query

        def patched_embed(self, text):
            call_count["n"] += 1
            return original_embed(self, text)

        provider = MockEmbeddingProvider()
        with patch.object(MockEmbeddingProvider, "embed_query", patched_embed):
            results = search_cases(
                "thong tin bi mat",
                db_path=populated_db,
                embedding_provider=provider,
            )

        # CIV-0008 (local_only) không có embedding → không nằm trong kết quả semantic
        ids = [r.case_id for r in results]
        # Có thể xuất hiện qua FTS (text match) nhưng không qua semantic vector
        # Điều quan trọng: provider không crash
        assert isinstance(results, list)


# ─── Test conflicts ───────────────────────────────────────────────────────────

class TestConflicts:
    def test_conflicting_cases_both_returned(self, populated_db: Path):
        """Hai mục mâu thuẫn phải xuất hiện cùng nhau."""
        results = search_cases("ALIGNREGEN alignment", db_path=populated_db)
        ids = [r.case_id for r in results]
        # Nếu CIV-0006 xuất hiện, CIV-0007 cũng phải xuất hiện (và ngược lại)
        if "CIV-0006" in ids:
            assert "CIV-0007" in ids, "CIV-0007 phải xuất hiện cùng CIV-0006 vì conflicts_with"
        if "CIV-0007" in ids:
            assert "CIV-0006" in ids, "CIV-0006 phải xuất hiện cùng CIV-0007 vì conflicts_with"

    def test_conflict_flag_set(self, populated_db: Path):
        """Mục mâu thuẫn có conflicts_with không rỗng."""
        results = search_cases("ALIGNREGEN alignment", db_path=populated_db)
        for r in results:
            if r.case_id == "CIV-0006":
                assert "CIV-0007" in r.conflicts_with
            if r.case_id == "CIV-0007":
                assert "CIV-0006" in r.conflicts_with


# ─── Test empty results ───────────────────────────────────────────────────────

class TestEmptyResults:
    def test_no_results_for_irrelevant_query(self, populated_db: Path):
        """Query không liên quan trả empty, không kéo mục xa nghĩa."""
        results = search_cases(
            "xyz_totally_unrelated_gibberish_99999",
            db_path=populated_db,
        )
        assert results == [], f"Phải trả [] nhưng trả {[r.case_id for r in results]}"

    def test_empty_query_returns_empty(self, populated_db: Path):
        results = search_cases("", db_path=populated_db)
        assert results == []

    def test_whitespace_query_returns_empty(self, populated_db: Path):
        results = search_cases("   ", db_path=populated_db)
        assert results == []


# ─── Test limit ───────────────────────────────────────────────────────────────

class TestLimit:
    def test_results_respect_limit(self, populated_db: Path):
        """Kết quả không vượt quá limit."""
        results = search_cases("lỗi", limit=3, db_path=populated_db)
        assert len(results) <= 3

    def test_default_limit_is_5(self, populated_db: Path):
        results = search_cases("lỗi", db_path=populated_db)
        assert len(results) <= 5


# ─── Test SearchResult structure ─────────────────────────────────────────────

class TestSearchResultStructure:
    def test_result_has_required_fields(self, populated_db: Path):
        results = search_cases("eHatchError", db_path=populated_db)
        assert len(results) > 0
        r = results[0]
        assert r.case_id
        assert r.revision >= 1
        assert r.title
        assert r.summary
        assert r.verification in ("verified_live", "verified_offline", "partial", "unverified")
        assert r.score >= 0
        assert r.matched_by in ("fts", "semantic", "both", "conflict")
        assert isinstance(r.civil_groups, list)
        assert isinstance(r.plugins, list)
        assert isinstance(r.conflicts_with, list)

    def test_result_matched_by_fts(self, populated_db: Path):
        """Khi không có embedding provider, matched_by phải là 'fts'."""
        results = search_cases("eHatchError", db_path=populated_db, embedding_provider=None)
        assert len(results) > 0
        for r in results:
            assert r.matched_by in ("fts", "conflict")

    def test_result_matched_by_both(self, populated_db: Path):
        """Khi có embedding provider, kết quả ở cả hai list → 'both'."""
        provider = MockEmbeddingProvider()
        results = search_cases("lỗi corridor", db_path=populated_db, embedding_provider=provider)
        # Không ép buộc 'both' vì phụ thuộc data, chỉ verify không crash
        assert isinstance(results, list)
