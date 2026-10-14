"""
server.py — MCP Server cho Sổ tay kinh nghiệm Civil (CivilHandbook).

Giao tiếp bằng FastMCP stdio theo chuẩn MCP Python SDK.
Cung cấp 6 công cụ và 2 tài nguyên chỉ đọc theo đúng thiết kế Sổ tay Civil.
TUYỆT ĐỐI KHÔNG có quyền sửa DWG hoặc gọi civil3d_execute.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

from . import config
from .embedding import GoogleEmbeddingProvider, MockEmbeddingProvider
from .id_allocator import allocate_id
from .models import (
    CaseMetadata,
    CivilTaxonomy,
    LifecycleStatus,
    RevisionContent,
    SemanticStatus,
    VerificationStatus,
)
from .search import search_cases
from .secret_scanner import scan as scan_for_secrets
from .storage import (
    ConflictError,
    atomic_save,
    get_case,
    init_db,
    undo_change,
)
from .git_sync import git_commit_and_push, git_pull, git_status
from .taxonomy import load_civil_groups, load_known_plugins as load_plugins

logger = logging.getLogger("civil_handbook.server")

# Tạo FastMCP Server
mcp = FastMCP("civil-handbook", instructions="Sổ tay kinh nghiệm Civil 3D cá nhân dùng chung")


# ─── Resources ────────────────────────────────────────────────────────────────

@mcp.resource("civil-handbook://taxonomy/civil-groups")
def get_taxonomy_civil_groups() -> str:
    """Trả về 18 nhóm phân loại đối tượng Civil 3D chuẩn của Sổ tay."""
    groups = load_civil_groups()
    return json.dumps(groups, ensure_ascii=False, indent=2)


@mcp.resource("civil-handbook://taxonomy/plugins")
def get_taxonomy_plugins() -> str:
    """Trả về danh mục các plugin AutoCAD/Civil 3D đã nhận diện."""
    plugins = load_plugins()
    return json.dumps(plugins, ensure_ascii=False, indent=2)


# ─── Tools ────────────────────────────────────────────────────────────────────

@mcp.tool(
    name="civil_handbook_search_cases",
    description=(
        "Tìm kiếm lai (FTS5 + Semantic + RRF) trong Sổ tay kinh nghiệm Civil. "
        "Chỉ đọc, tối đa 10 kết quả (mặc định 5). "
        "Trả về mã CIV-xxxx, tiêu đề, tóm tắt, mức kiểm chứng và quan hệ."
    ),
)
def civil_handbook_search_cases(
    query: str,
    civil_groups: Optional[list[str]] = None,
    plugins: Optional[list[str]] = None,
    civil_version: Optional[str] = None,
    project: Optional[str] = None,
    verification: Optional[list[str]] = None,
    limit: int = 5,
) -> str:
    """
    Tìm kiếm ca kinh nghiệm trong sổ tay.
    """
    init_db()
    limit = max(1, min(limit, 10))

    # Khởi tạo embedding provider (thử Google, nếu lỗi auth thì None để FTS fallback)
    embed_provider = None
    try:
        gp = GoogleEmbeddingProvider()
        health = gp.health()
        if health.get("ok"):
            embed_provider = gp
    except Exception:
        embed_provider = None

    results = search_cases(
        query=query,
        civil_groups=civil_groups or [],
        plugins=plugins or [],
        civil_version=civil_version,
        project=project,
        verification_filter=verification,
        limit=limit,
        embedding_provider=embed_provider,
    )

    if not results:
        return json.dumps({
            "status": "not_found",
            "message": "Chưa có mục phù hợp trong Sổ tay Civil.",
            "total": 0,
            "results": [],
        }, ensure_ascii=False, indent=2)

    data = [
        {
            "case_id": r.case_id,
            "revision": r.revision,
            "title": r.title,
            "summary": r.summary,
            "verification": r.verification,
            "matched_by": r.matched_by,
            "score": r.score,
            "civil_groups": r.civil_groups,
            "plugins": r.plugins,
            "conflicts_with": r.conflicts_with,
            "civil_version": r.civil_version,
            "project": r.project,
        }
        for r in results
    ]

    return json.dumps({
        "status": "success",
        "total": len(data),
        "results": data,
    }, ensure_ascii=False, indent=2)


@mcp.tool(
    name="civil_handbook_get_case",
    description=(
        "Đọc chi tiết một mục kinh nghiệm theo mã CIV-xxxx. "
        "Có thể chỉ định số revision cụ thể hoặc lấy revision hiện hành."
    ),
)
def civil_handbook_get_case(
    case_id: str,
    revision: Optional[int] = None,
) -> str:
    """
    Đọc chi tiết một ca kinh nghiệm.
    """
    init_db()
    try:
        metadata, content = get_case(case_id=case_id, revision=revision)
        return json.dumps({
            "status": "success",
            "case_id": metadata.id,
            "current_revision": metadata.revision,
            "requested_revision": revision or metadata.revision,
            "metadata": metadata.model_dump(mode="json"),
            "content": content.model_dump(mode="json"),
        }, ensure_ascii=False, indent=2)
    except FileNotFoundError:
        return json.dumps({
            "status": "error",
            "message": f"Không tìm thấy mục {case_id} (hoặc revision {revision}).",
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({
            "status": "error",
            "message": f"Lỗi đọc ca: {e}",
        }, ensure_ascii=False, indent=2)


@mcp.tool(
    name="civil_handbook_save_case",
    description=(
        "Lưu một ca kinh nghiệm mới vào Sổ tay Civil khi người dùng nói 'bảo lưu'. "
        "Yêu cầu idempotency_key để chống trùng lặp. "
        "Không ghi vào bản vẽ Civil 3D/AutoCAD."
    ),
)
def civil_handbook_save_case(
    title: str,
    summary: str,
    problem: str,
    context: str,
    idempotency_key: str,
    cause: str = "",
    cause_is_hypothesis: bool = False,
    steps: str = "",
    result: str = "",
    failed_attempts: Optional[list[str]] = None,
    warnings: Optional[list[str]] = None,
    scope_notes: str = "",
    undo_procedure: str = "",
    civil_groups: Optional[list[str]] = None,
    aliases: Optional[list[str]] = None,
    tags: Optional[list[str]] = None,
    plugins: Optional[list[dict[str, Any]]] = None,
    sources: Optional[list[str]] = None,
    related_to: Optional[list[str]] = None,
    conflicts_with: Optional[list[str]] = None,
    verification: str = "unverified",
    civil_version: Optional[str] = None,
    project: Optional[str] = None,
    sensitivity: str = "default",
) -> str:
    """
    Lưu một ca kinh nghiệm mới vào Sổ tay.
    """
    init_db()

    # Quét bảo mật secret scanner
    combined_text = f"{title} {summary} {problem} {context} {cause} {steps} {result}"
    secrets_found = scan_for_secrets(combined_text)
    if secrets_found:
        return json.dumps({
            "status": "rejected_security",
            "message": "Phát hiện thông tin nhạy cảm/bí mật trong nội dung, không thể lưu:",
            "warnings": secrets_found,
        }, ensure_ascii=False, indent=2)

    # Kiểm tra idempotency_key trước khi cấp phát ID mới
    import sqlite3
    conn_chk = sqlite3.connect(str(config.DB_PATH))
    existing = conn_chk.execute(
        "SELECT case_id, operation_id FROM undo_log WHERE idempotency_key = ? LIMIT 1",
        (idempotency_key,),
    ).fetchone()
    conn_chk.close()
    if existing:
        old_cid, old_opid = existing
        old_meta, _ = get_case(old_cid)
        return json.dumps({
            "status": "success",
            "case_id": old_cid,
            "revision": old_meta.revision,
            "title": old_meta.title,
            "verification": old_meta.verification.value,
            "civil_groups": [g.value if hasattr(g, 'value') else str(g) for g in old_meta.civil_groups],
            "plugins": old_meta.plugins,
            "semantic_status": old_meta.semantic_status.value,
            "operation_id": old_opid,
            "idempotent": True,
            "undo_command": f"Dùng mã undo {old_opid} hoặc nói 'hoàn tác mục vừa lưu'",
        }, ensure_ascii=False, indent=2)

    # Thử git pull trước khi cấp phát ID mới (nếu có remote git)
    try:
        git_pull()
    except Exception:
        pass

    # Cấp phát mã mới
    case_id = allocate_id()
    now = datetime.now(timezone.utc)

    # Parse civil groups sang enum
    cg_enums = []
    for g in (civil_groups or []):
        try:
            cg_enums.append(CivilTaxonomy(g))
        except ValueError:
            pass

    ver_enum = VerificationStatus(verification) if verification in VerificationStatus._value2member_map_ else VerificationStatus.unverified

    content = RevisionContent(
        case_id=case_id,
        revision=1,
        title=title,
        summary=summary,
        problem=problem,
        context=context,
        cause=cause,
        cause_is_hypothesis=cause_is_hypothesis,
        steps=steps,
        result=result,
        failed_attempts=failed_attempts or [],
        warnings=warnings or [],
        scope_notes=scope_notes,
        undo_procedure=undo_procedure,
        civil_groups=cg_enums,
        aliases=aliases or [],
        tags=tags or [],
        plugins=plugins or [],
        sources=sources or [],
        related_to=related_to or [],
        conflicts_with=conflicts_with or [],
        sensitivity=sensitivity,
        created_at=now,
        revision_reason="initial",
    )

    metadata = CaseMetadata(
        id=case_id,
        revision=1,
        idempotency_key=idempotency_key,
        title=title,
        lifecycle=LifecycleStatus.active,
        verification=ver_enum,
        semantic_status=SemanticStatus.local_only if sensitivity == "local_only" else SemanticStatus.semantic_pending,
        civil_groups=cg_enums,
        plugins=[p.get("name", str(p)) if isinstance(p, dict) else str(p) for p in (plugins or [])],
        created_at=now,
        updated_at=now,
        related_to=related_to or [],
        conflicts_with=conflicts_with or [],
        sensitivity=sensitivity,
        project=project,
        civil_version=civil_version,
    )

    try:
        op_id = atomic_save(content=content, metadata=metadata)

        # Tự động commit và push Git
        git_res = {"status": "skipped"}
        try:
            git_res = git_commit_and_push(case_id=case_id, title=title)
        except Exception as ge:
            git_res = {"status": "git_error", "error": str(ge)}

        return json.dumps({
            "status": "success",
            "case_id": case_id,
            "revision": 1,
            "title": title,
            "verification": ver_enum.value,
            "civil_groups": [g.value for g in cg_enums],
            "plugins": metadata.plugins,
            "semantic_status": metadata.semantic_status.value,
            "operation_id": op_id,
            "git_sync": git_res,
            "undo_command": f"Dùng mã undo {op_id} hoặc nói 'hoàn tác mục vừa lưu'",
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({
            "status": "error",
            "message": f"Lỗi khi lưu ca: {e}",
        }, ensure_ascii=False, indent=2)


@mcp.tool(
    name="civil_handbook_revise_case",
    description=(
        "Tạo revision mới cho một mục đã có trong Sổ tay Civil. "
        "Yêu cầu case_id, expected_revision và revision_reason. "
        "Không ghi đè revision cũ."
    ),
)
def civil_handbook_revise_case(
    case_id: str,
    expected_revision: int,
    idempotency_key: str,
    revision_reason: str,
    title: Optional[str] = None,
    summary: Optional[str] = None,
    problem: Optional[str] = None,
    context: Optional[str] = None,
    cause: Optional[str] = None,
    cause_is_hypothesis: Optional[bool] = None,
    steps: Optional[str] = None,
    result: Optional[str] = None,
    failed_attempts: Optional[list[str]] = None,
    warnings: Optional[list[str]] = None,
    scope_notes: Optional[str] = None,
    undo_procedure: Optional[str] = None,
    verification: Optional[str] = None,
) -> str:
    """
    Tạo revision mới cho một mục kinh nghiệm.
    """
    init_db()
    try:
        old_meta, old_content = get_case(case_id=case_id)
        if old_meta.revision != expected_revision:
            return json.dumps({
                "status": "conflict",
                "message": (
                    f"Conflict: expected revision {expected_revision}, "
                    f"nhưng hiện tại là {old_meta.revision}. Hãy đọc lại revision mới nhất."
                ),
            }, ensure_ascii=False, indent=2)

        # Merge updates
        now = datetime.now(timezone.utc)
        new_content_data = old_content.model_dump()
        new_content_data["revision"] = expected_revision + 1
        new_content_data["revision_reason"] = revision_reason
        new_content_data["created_at"] = now

        if title is not None:
            new_content_data["title"] = title
        if summary is not None:
            new_content_data["summary"] = summary
        if problem is not None:
            new_content_data["problem"] = problem
        if context is not None:
            new_content_data["context"] = context
        if cause is not None:
            new_content_data["cause"] = cause
        if cause_is_hypothesis is not None:
            new_content_data["cause_is_hypothesis"] = cause_is_hypothesis
        if steps is not None:
            new_content_data["steps"] = steps
        if result is not None:
            new_content_data["result"] = result
        if failed_attempts is not None:
            new_content_data["failed_attempts"] = failed_attempts
        if warnings is not None:
            new_content_data["warnings"] = warnings
        if scope_notes is not None:
            new_content_data["scope_notes"] = scope_notes
        if undo_procedure is not None:
            new_content_data["undo_procedure"] = undo_procedure

        new_content = RevisionContent.model_validate(new_content_data)

        new_meta_data = old_meta.model_dump()
        new_meta_data["revision"] = expected_revision + 1
        new_meta_data["idempotency_key"] = idempotency_key
        new_meta_data["updated_at"] = now
        if title is not None:
            new_meta_data["title"] = title
        if verification is not None and verification in VerificationStatus._value2member_map_:
            new_meta_data["verification"] = VerificationStatus(verification)

        new_metadata = CaseMetadata.model_validate(new_meta_data)

        op_id = atomic_save(
            content=new_content,
            metadata=new_metadata,
            expected_revision=expected_revision,
        )

        return json.dumps({
            "status": "success",
            "case_id": case_id,
            "new_revision": expected_revision + 1,
            "revision_reason": revision_reason,
            "operation_id": op_id,
        }, ensure_ascii=False, indent=2)

    except ConflictError as e:
        return json.dumps({"status": "conflict", "message": str(e)}, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"Lỗi revise: {e}"}, ensure_ascii=False, indent=2)


@mcp.tool(
    name="civil_handbook_undo_change",
    description=(
        "Hoàn tác một thao tác lưu hoặc sửa trong Sổ tay Civil bằng operation_id. "
        "Tạo revision đảo ngược, bảo toàn toàn bộ lịch sử."
    ),
)
def civil_handbook_undo_change(
    operation_id: str,
) -> str:
    """
    Hoàn tác một thay đổi trong sổ tay.
    """
    init_db()
    try:
        ok = undo_change(operation_id=operation_id)
        if ok:
            return json.dumps({
                "status": "success",
                "message": f"Đã hoàn tác thành công thao tác {operation_id}.",
                "operation_id": operation_id,
            }, ensure_ascii=False, indent=2)
        else:
            return json.dumps({
                "status": "not_found_or_already_undone",
                "message": f"Không tìm thấy thao tác {operation_id} hoặc thao tác đã được hoàn tác trước đó.",
            }, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"Lỗi hoàn tác: {e}"}, ensure_ascii=False, indent=2)


@mcp.tool(
    name="civil_handbook_sync",
    description=(
        "Đồng bộ Sổ tay Civil với GitHub (git pull). "
        "Tự động kéo các ca mới nhất từ máy khác về và cập nhật lại chỉ mục SQLite. "
        "Dùng khi người dùng nói 'đồng bộ sổ tay', 'cập nhật sổ tay từ github'."
    ),
)
def civil_handbook_sync() -> str:
    """
    Đồng bộ dữ liệu sổ tay từ GitHub.
    """
    init_db()
    res = git_pull()
    return json.dumps(res, ensure_ascii=False, indent=2)


@mcp.tool(
    name="civil_handbook_health",
    description=(
        "Kiểm tra tình trạng sức khỏe hệ thống Sổ tay Civil: "
        "file nguồn, SQLite WAL, hàng đợi embedding/backup, git status, provider trạng thái. "
        "Tuyệt đối không hiển thị API key."
    ),
)
def civil_handbook_health() -> str:
    """
    Health check tổng thể.
    """
    init_db()
    sqlite_ok = config.DB_PATH.exists()
    cases_dir_ok = config.CASES_DIR.exists()
    backup_queue_dir_ok = config.BACKUP_QUEUE_DIR.exists()

    # Kiểm tra Google provider
    google_health = {"ok": False, "detail": "not_checked"}
    try:
        gp = GoogleEmbeddingProvider()
        google_health = gp.health()
    except Exception as e:
        google_health = {"ok": False, "detail": str(e)}

    # Trạng thái Git
    git_info = git_status()

    return json.dumps({
        "status": "healthy" if sqlite_ok and cases_dir_ok else "degraded",
        "storage": {
            "sqlite_db": str(config.DB_PATH),
            "sqlite_exists": sqlite_ok,
            "cases_dir": str(config.CASES_DIR),
            "cases_dir_exists": cases_dir_ok,
            "backup_queue_dir_exists": backup_queue_dir_ok,
        },
        "git": git_info,
        "embedding_provider": {
            "provider_id": "google",
            "model_id": config.EMBEDDING_MODEL,
            "dimension": config.EMBEDDING_DIM,
            "health": google_health,
        },
        "runtime": {
            "mode": "stdio",
            "cad_execute_allowed": False,
        },
    }, ensure_ascii=False, indent=2)


# ─── Entrypoint ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    mcp.run()
