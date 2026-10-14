"""
search.py — Tìm kiếm lai FTS5/BM25 + Semantic Cosine + RRF fusion.

Tương thích với schema của storage.py:
- Bảng cases: id, revision, metadata (JSON), idempotency_key
- Bảng cases_fts: case_id, title, summary, problem, cause, steps, aliases, tags, normalized
- Bảng embeddings: case_id, vector, generation

Luồng tìm kiếm:
  1. FTS5 trên SQLite (BM25 ranking) — bắt mã lỗi, lệnh, layer, DLL, TCVN
  2. Semantic cosine similarity trên bảng embeddings
  3. RRF (Reciprocal Rank Fusion, k=60) trộn hai danh sách
  4. Boost: phiên bản Civil, tên lệnh chính xác, nhóm, verified_live, dự án
  5. Lọc: verification_filter, civil_version, civil_groups, plugins
  6. Nếu score thấp (dưới SCORE_THRESHOLD) trả empty
  7. Nếu hai mục mâu thuẫn, trả cả hai kèm flag conflicts_with
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import struct
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from handbook.config import DB_PATH, MAX_SEARCH_RESULTS, SQLITE_BUSY_TIMEOUT_MS
from handbook.text_utils import normalize_vn

logger = logging.getLogger(__name__)

# ─── Ngưỡng score tối thiểu ───────────────────────────────────────────────────
SCORE_THRESHOLD = 0.005  # dưới ngưỡng này → trả empty
RRF_K = 60               # hằng số RRF chuẩn


# ─── SearchResult ─────────────────────────────────────────────────────────────

@dataclass
class SearchResult:
    case_id: str
    revision: int
    title: str
    summary: str
    verification: str           # verified_live | verified_offline | partial | unverified
    score: float
    matched_by: str             # "fts" | "semantic" | "both" | "conflict"
    civil_groups: list[str] = field(default_factory=list)
    plugins: list[str] = field(default_factory=list)
    conflicts_with: list[str] = field(default_factory=list)
    lifecycle: str = "active"
    civil_version: Optional[str] = None
    project: Optional[str] = None
    sensitivity: str = "normal"


# ─── Cosine similarity ────────────────────────────────────────────────────────

def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity giữa hai vector float."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _blob_to_vec(blob: bytes, dim: int) -> list[float]:
    """Giải mã BLOB float32 từ SQLite thành list[float]."""
    if not blob or len(blob) < dim * 4:
        return []
    return list(struct.unpack(f"{dim}f", blob[:dim * 4]))


# ─── Metadata parser ──────────────────────────────────────────────────────────

def _parse_metadata(meta_json: str) -> dict[str, Any]:
    """Parse JSON metadata từ cột cases.metadata."""
    try:
        return json.loads(meta_json)
    except Exception:
        return {}


# ─── RRF fusion ───────────────────────────────────────────────────────────────

def _rrf_score(rank: int, k: int = RRF_K) -> float:
    """RRF score cho một mục có rank (1-based)."""
    return 1.0 / (k + rank)


def _rrf_merge(
    fts_ids: list[str],
    sem_ids: list[str],
    k: int = RRF_K,
) -> dict[str, float]:
    """Trộn hai danh sách kết quả bằng RRF. Trả {case_id: rrf_score}."""
    scores: dict[str, float] = {}
    for rank, case_id in enumerate(fts_ids, start=1):
        scores[case_id] = scores.get(case_id, 0.0) + _rrf_score(rank, k)
    for rank, case_id in enumerate(sem_ids, start=1):
        scores[case_id] = scores.get(case_id, 0.0) + _rrf_score(rank, k)
    return scores


# ─── Boost logic ──────────────────────────────────────────────────────────────

_VERIFICATION_ORDER = {
    "verified_live": 4,
    "verified_offline": 3,
    "partial": 2,
    "unverified": 1,
}


def _boost(
    score: float,
    meta: dict[str, Any],
    query_norm: str,
    civil_version: Optional[str],
    project: Optional[str],
) -> float:
    """Tăng score theo độ ưu tiên spec."""
    boost = score

    # verified_live được ưu tiên cao nhất
    ver = meta.get("verification", "unverified")
    ver_rank = _VERIFICATION_ORDER.get(ver, 1)
    boost += ver_rank * 0.05

    # Khớp chính xác trong title
    title_norm = normalize_vn(meta.get("title", ""))
    if query_norm and query_norm in title_norm:
        boost += 0.10

    # Đúng phiên bản Civil
    if civil_version and meta.get("civil_version") == civil_version:
        boost += 0.08

    # Đúng dự án
    if project and meta.get("project") == project:
        boost += 0.06

    return boost


# ─── FTS5 search ──────────────────────────────────────────────────────────────

def _fts_search(
    con: sqlite3.Connection,
    query: str,
    limit: int,
    civil_groups: list[str],
    plugins: list[str],
    civil_version: Optional[str],
    verification_filter: Optional[list[str]],
) -> list[str]:
    """
    Tìm bằng FTS5 BM25 thông minh đa tầng:
    1. Cụm từ chính xác (Exact phrase)
    2. Tất cả các từ đều khớp (AND Prefix Tokens trên cả bảng gốc và normalized)
    3. Bất kỳ từ nào khớp (OR Tokens xếp hạng theo BM25)
    4. Multi-word LIKE fallback
    """
    query_clean = query.strip()
    if not query_clean:
        return []

    query_norm = normalize_vn(query_clean)

    # Tách tokens an toàn loại bỏ ký tự cú pháp FTS5
    tokens_raw = [w.strip() for w in re.split(r'[\s,.;:!?()\[\]{}"\'\-+/*\\]+', query_clean) if len(w.strip()) >= 1]
    tokens_norm = [w.strip() for w in re.split(r'[\s,.;:!?()\[\]{}"\'\-+/*\\]+', query_norm) if len(w.strip()) >= 1]

    fts_candidates: list[str] = []

    # ─── TẦNG 1: Cụm từ chính xác (Exact phrase) ─────────────────────────────
    phrase_raw = f'"{query_clean.replace("\"", "\"\"")}"'
    try:
        rows = con.execute(
            "SELECT case_id FROM cases_fts WHERE cases_fts MATCH ? ORDER BY rank LIMIT ?",
            (phrase_raw, limit * 5),
        ).fetchall()
        fts_candidates.extend(r[0] for r in rows)
    except Exception as e:
        logger.debug("FTS phrase raw error: %s", e)

    phrase_norm = f'"{query_norm.replace("\"", "\"\"")}"'
    try:
        rows = con.execute(
            "SELECT case_id FROM cases_fts WHERE normalized MATCH ? ORDER BY rank LIMIT ?",
            (phrase_norm, limit * 5),
        ).fetchall()
        fts_candidates.extend(r[0] for r in rows)
    except Exception as e:
        logger.debug("FTS phrase norm error: %s", e)

    # ─── TẦNG 2: Tất cả các từ đều phải có mặt (AND Prefix Match) ───────────
    if len(tokens_raw) > 1:
        and_raw = " AND ".join(f'"{t}"*' for t in tokens_raw)
        try:
            rows = con.execute(
                "SELECT case_id FROM cases_fts WHERE cases_fts MATCH ? ORDER BY rank LIMIT ?",
                (and_raw, limit * 5),
            ).fetchall()
            fts_candidates.extend(r[0] for r in rows)
        except Exception as e:
            logger.debug("FTS AND raw error: %s", e)

    if len(tokens_norm) > 1:
        and_norm = " AND ".join(f'"{t}"*' for t in tokens_norm)
        try:
            rows = con.execute(
                "SELECT case_id FROM cases_fts WHERE normalized MATCH ? ORDER BY rank LIMIT ?",
                (and_norm, limit * 5),
            ).fetchall()
            fts_candidates.extend(r[0] for r in rows)
        except Exception as e:
            logger.debug("FTS AND norm error: %s", e)
    elif len(tokens_norm) == 1:
        single_norm = f'"{tokens_norm[0]}"*'
        try:
            rows = con.execute(
                "SELECT case_id FROM cases_fts WHERE normalized MATCH ? ORDER BY rank LIMIT ?",
                (single_norm, limit * 5),
            ).fetchall()
            fts_candidates.extend(r[0] for r in rows)
        except Exception as e:
            logger.debug("FTS single norm error: %s", e)

    # ─── TẦNG 3: Bất kỳ từ nào khớp (OR Match xếp hạng BM25) ────────────────
    if len(tokens_norm) > 1 and not fts_candidates:
        or_norm = " OR ".join(f'"{t}"*' for t in tokens_norm)
        try:
            rows = con.execute(
                "SELECT case_id FROM cases_fts WHERE normalized MATCH ? ORDER BY rank LIMIT ?",
                (or_norm, limit * 5),
            ).fetchall()
            fts_candidates.extend(r[0] for r in rows)
        except Exception as e:
            logger.debug("FTS OR norm error: %s", e)

    # ─── TẦNG 4: Multi-word LIKE fallback ─────────────────────────────────────
    if not fts_candidates and tokens_norm:
        clauses = " AND ".join("normalized LIKE ?" for _ in tokens_norm)
        params = [f"%{t}%" for t in tokens_norm]
        try:
            rows = con.execute(
                f"SELECT case_id FROM cases_fts WHERE {clauses} LIMIT ?",
                params + [limit * 5],
            ).fetchall()
            fts_candidates.extend(r[0] for r in rows)
        except Exception as e:
            logger.debug("FTS LIKE fallback error: %s", e)

    # Deduplicate giữ thứ tự rank
    seen: set[str] = set()
    unique_candidates: list[str] = []
    for cid in fts_candidates:
        if cid not in seen:
            seen.add(cid)
            unique_candidates.append(cid)

    # Filter theo metadata (lifecycle, civil_groups, plugins, verification, civil_version)
    result: list[str] = []
    for case_id in unique_candidates:
        row = con.execute(
            "SELECT revision, metadata FROM cases WHERE id = ?", (case_id,)
        ).fetchone()
        if not row:
            continue

        meta = _parse_metadata(row[1])
        lifecycle = meta.get("lifecycle", "active")

        # Chỉ active
        if lifecycle != "active":
            continue

        # verification_filter
        if verification_filter:
            ver = meta.get("verification", "unverified")
            if ver not in verification_filter:
                continue

        # civil_version filter
        if civil_version:
            mv = meta.get("civil_version")
            if mv and mv != civil_version:
                continue

        # civil_groups filter (OR logic)
        if civil_groups:
            cg = [g.get("value", g) if isinstance(g, dict) else g
                  for g in meta.get("civil_groups", [])]
            if not any(g in cg for g in civil_groups):
                continue

        # plugins filter (OR logic)
        if plugins:
            pl = [p.get("name", p) if isinstance(p, dict) else p
                  for p in meta.get("plugins", [])]
            if not any(p in pl for p in plugins):
                continue

        result.append(case_id)
        if len(result) >= limit:
            break

    return result


# ─── Semantic search ──────────────────────────────────────────────────────────

def _semantic_search(
    con: sqlite3.Connection,
    query_vec: list[float],
    dim: int,
    limit: int,
    civil_groups: list[str],
    plugins: list[str],
    civil_version: Optional[str],
    verification_filter: Optional[list[str]],
) -> list[str]:
    """
    Tìm semantic bằng cosine similarity trên bảng embeddings.
    Bỏ qua mục local_only (không có embedding).
    """
    # Lấy tất cả embedding (generation cao nhất)
    rows = con.execute(
        """
        SELECT e.case_id, e.vector, c.metadata
        FROM embeddings e
        JOIN cases c ON c.id = e.case_id
        WHERE e.generation = (
            SELECT MAX(e2.generation) FROM embeddings e2 WHERE e2.case_id = e.case_id
        )
        """,
    ).fetchall()

    scored: list[tuple[str, float]] = []
    for row in rows:
        case_id = row[0]
        blob = row[1]
        meta = _parse_metadata(row[2])

        # Filter lifecycle
        if meta.get("lifecycle", "active") != "active":
            continue

        # Filter sensitivity — local_only không có embedding nhưng phòng ngừa
        if meta.get("sensitivity") == "local_only":
            continue

        # verification_filter
        if verification_filter:
            if meta.get("verification", "unverified") not in verification_filter:
                continue

        # civil_version
        if civil_version:
            mv = meta.get("civil_version")
            if mv and mv != civil_version:
                continue

        # civil_groups
        if civil_groups:
            cg = [g.get("value", g) if isinstance(g, dict) else g
                  for g in meta.get("civil_groups", [])]
            if not any(g in cg for g in civil_groups):
                continue

        # plugins
        if plugins:
            pl = [p.get("name", p) if isinstance(p, dict) else p
                  for p in meta.get("plugins", [])]
            if not any(p in pl for p in plugins):
                continue

        vec = _blob_to_vec(blob, dim)
        if not vec:
            continue

        sim = _cosine(query_vec, vec)
        scored.append((case_id, sim))

    scored.sort(key=lambda x: x[1], reverse=True)
    return [case_id for case_id, _ in scored[:limit]]


# ─── Main search function ─────────────────────────────────────────────────────

def search_cases(
    query: str,
    civil_groups: list[str] = [],
    plugins: list[str] = [],
    civil_version: Optional[str] = None,
    project: Optional[str] = None,
    verification_filter: Optional[list[str]] = None,
    limit: int = MAX_SEARCH_RESULTS,
    db_path: Path = DB_PATH,
    embedding_provider=None,
    embedding_dim: int = 768,
) -> list[SearchResult]:
    """
    Tìm kiếm lai FTS5 + Semantic + RRF.

    Args:
        query: Câu hỏi hoặc từ khóa.
        civil_groups: Lọc theo nhóm Civil (OR logic).
        plugins: Lọc theo plugin (OR logic).
        civil_version: Ưu tiên mục đúng phiên bản này.
        project: Ưu tiên mục của dự án này.
        verification_filter: Chỉ trả mục có trạng thái trong list này.
        limit: Tối đa bao nhiêu kết quả (mặc định 5, max 10).
        db_path: Đường dẫn SQLite.
        embedding_provider: Provider để embed query; None → chỉ dùng FTS.
        embedding_dim: Kích thước vector.

    Returns:
        Danh sách SearchResult đã sắp xếp. Trả [] nếu score dưới SCORE_THRESHOLD.
    """
    if not query or not query.strip():
        return []

    if not db_path.exists():
        return []

    query_norm = normalize_vn(query)
    limit = max(1, min(limit, 10))

    con = sqlite3.connect(str(db_path), timeout=SQLITE_BUSY_TIMEOUT_MS / 1000)
    con.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
    con.row_factory = sqlite3.Row

    try:
        # 1. FTS5
        fts_ids = _fts_search(
            con, query, limit * 3, civil_groups, plugins, civil_version, verification_filter
        )

        # 2. Semantic
        sem_ids: list[str] = []
        if embedding_provider is not None:
            try:
                query_vec = embedding_provider.embed_query(query)
                sem_ids = _semantic_search(
                    con, query_vec, embedding_dim, limit * 3,
                    civil_groups, plugins, civil_version, verification_filter,
                )
            except Exception as e:
                logger.warning("Semantic search lỗi — chỉ dùng FTS: %s", e)

        # 3. RRF fusion
        rrf_scores = _rrf_merge(fts_ids, sem_ids)
        if not rrf_scores:
            return []

        # 4. Lấy metadata và summary
        all_ids = list(rrf_scores.keys())
        placeholders = ",".join("?" * len(all_ids))
        rows = con.execute(
            f"SELECT id, revision, metadata FROM cases WHERE id IN ({placeholders})",
            all_ids,
        ).fetchall()
        row_map = {r["id"]: r for r in rows}

        fts_summary_rows = con.execute(
            f"SELECT case_id, summary FROM cases_fts WHERE case_id IN ({placeholders})",
            all_ids,
        ).fetchall()
        summary_map = {r["case_id"]: (r["summary"] or "") for r in fts_summary_rows}

        # 5. Build SearchResult với boost
        results: list[SearchResult] = []
        for case_id, base_score in rrf_scores.items():
            row = row_map.get(case_id)
            if not row:
                continue

            meta = _parse_metadata(row["metadata"])
            lifecycle = meta.get("lifecycle", "active")
            if lifecycle != "active":
                continue  # double-check

            in_fts = case_id in fts_ids
            in_sem = case_id in sem_ids
            if in_fts and in_sem:
                matched_by = "both"
            elif in_fts:
                matched_by = "fts"
            else:
                matched_by = "semantic"

            final_score = _boost(base_score, meta, query_norm, civil_version, project)

            # Parse civil_groups (có thể là list of enum dict hoặc string)
            raw_cg = meta.get("civil_groups", [])
            cg = [g.get("value", g) if isinstance(g, dict) else str(g) for g in raw_cg]

            # Parse plugins (có thể là list of dict với "name" key, hoặc string)
            raw_pl = meta.get("plugins", [])
            pl = [p.get("name", str(p)) if isinstance(p, dict) else str(p) for p in raw_pl]

            cw = meta.get("conflicts_with", [])

            results.append(SearchResult(
                case_id=case_id,
                revision=row["revision"] or 1,
                title=meta.get("title", ""),
                summary=summary_map.get(case_id, "") or meta.get("summary", ""),
                verification=meta.get("verification", "unverified"),
                score=round(final_score, 6),
                matched_by=matched_by,
                civil_groups=cg,
                plugins=pl,
                conflicts_with=cw,
                lifecycle=lifecycle,
                civil_version=meta.get("civil_version"),
                project=meta.get("project"),
                sensitivity=meta.get("sensitivity", "normal"),
            ))

        # 6. Sort by score desc
        results.sort(key=lambda r: r.score, reverse=True)

        # 7. Threshold check
        if not results or results[0].score < SCORE_THRESHOLD:
            return []

        final = results[:limit]
        seen_ids = {r.case_id for r in final}

        # 8. Xử lý conflicts_with (không vượt quá limit)
        for result in list(final):
            for conflict_id in result.conflicts_with:
                if conflict_id not in seen_ids and len(final) < limit:
                    crow_data = con.execute(
                        "SELECT id, revision, metadata FROM cases WHERE id = ?",
                        (conflict_id,),
                    ).fetchone()
                    if crow_data:
                        cmeta = _parse_metadata(crow_data["metadata"])
                        if cmeta.get("lifecycle", "active") == "active":
                            raw_cg2 = cmeta.get("civil_groups", [])
                            cg2 = [g.get("value", g) if isinstance(g, dict) else str(g)
                                   for g in raw_cg2]
                            raw_pl2 = cmeta.get("plugins", [])
                            pl2 = [p.get("name", str(p)) if isinstance(p, dict) else str(p)
                                   for p in raw_pl2]
                            final.append(SearchResult(
                                case_id=conflict_id,
                                revision=crow_data["revision"] or 1,
                                title=cmeta.get("title", ""),
                                summary=cmeta.get("summary", ""),
                                verification=cmeta.get("verification", "unverified"),
                                score=0.0,
                                matched_by="conflict",
                                civil_groups=cg2,
                                plugins=pl2,
                                conflicts_with=cmeta.get("conflicts_with", []),
                                lifecycle=cmeta.get("lifecycle", "active"),
                                civil_version=cmeta.get("civil_version"),
                                project=cmeta.get("project"),
                                sensitivity=cmeta.get("sensitivity", "normal"),
                            ))
                            seen_ids.add(conflict_id)

        return final

    finally:
        con.close()
