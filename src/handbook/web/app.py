"""
app.py — FastAPI Backend cho Civil Notebook (Sổ tay điện tử Civil 3D).
Hỗ trợ tra cứu tức thì, soạn thảo Markdown, paste ảnh chụp màn hình CAD,
và tự động đồng bộ 2 chiều với GitHub.
"""

from __future__ import annotations

import base64
import json
import logging
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import config
from ..git_sync import get_current_branch, get_git_remote, git_commit_and_push, git_pull
from ..id_allocator import allocate_id, peek_next_id
from ..models import (
    CaseMetadata,
    CivilTaxonomy,
    LifecycleStatus,
    RevisionContent,
    SemanticStatus,
    VerificationStatus,
)
from ..search import search_cases
from ..secret_scanner import scan as scan_for_secrets
from ..storage import (
    ConflictError,
    atomic_save,
    get_case,
    init_db,
    rebuild_index_from_sources,
)

logger = logging.getLogger("civil_handbook.web")

# ─── Khởi tạo FastAPI App ─────────────────────────────────────────────────────

app = FastAPI(
    title="Civil Notebook",
    description="Sổ tay điện tử tra cứu & ghi chú kỹ thuật Civil 3D / AutoCAD độc lập",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

WEB_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"

TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
STATIC_DIR.mkdir(parents=True, exist_ok=True)
config.EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

# Mount thư mục tĩnh và thư mục ảnh evidence
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/evidence", StaticFiles(directory=str(config.EVIDENCE_DIR)), name="evidence")


# ─── Pydantic Schemas ─────────────────────────────────────────────────────────

class CreateCasePayload(BaseModel):
    title: str
    summary: str
    problem: str
    context: str = "Autodesk Civil 3D"
    cause: str = ""
    cause_is_hypothesis: bool = False
    steps: str = ""
    result: str = ""
    warnings: list[str] = Field(default_factory=list)
    civil_groups: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    plugins: list[str] = Field(default_factory=list)
    verification: str = "verified_live"
    civil_version: Optional[str] = "Civil 3D 2020-2027"
    project: Optional[str] = None
    sensitivity: str = "default"


class UpdateCasePayload(BaseModel):
    expected_revision: int
    revision_reason: str = "Cập nhật qua Civil Notebook Web"
    title: str
    summary: str
    problem: str
    context: str = "Autodesk Civil 3D"
    cause: str = ""
    cause_is_hypothesis: bool = False
    steps: str = ""
    result: str = ""
    warnings: list[str] = Field(default_factory=list)
    civil_groups: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    plugins: list[str] = Field(default_factory=list)
    verification: str = "verified_live"
    civil_version: Optional[str] = "Civil 3D 2020-2027"
    project: Optional[str] = None


class ClipboardUploadPayload(BaseModel):
    image_base64: str  # Dạng data:image/png;base64,... hoặc base64 thô


# ─── API Endpoints ────────────────────────────────────────────────────────────

@app.get("/api/status")
def get_system_status() -> dict[str, Any]:
    """Lấy thông tin trạng thái hệ thống, Git sync và bộ đếm ID."""
    init_db()
    remote = get_git_remote()
    branch = get_current_branch()
    next_id = peek_next_id()

    # Đếm số ca trong database
    conn = sqlite3.connect(str(config.DB_PATH))
    try:
        row = conn.execute("SELECT COUNT(*) FROM cases").fetchone()
        total_cases = row[0] if row else 0
    finally:
        conn.close()

    return {
        "status": "online",
        "total_cases": total_cases,
        "next_id": next_id,
        "git": {
            "has_remote": bool(remote),
            "remote": remote,
            "branch": branch,
        },
    }


@app.get("/api/taxonomy")
def get_taxonomy() -> dict[str, Any]:
    """Lấy danh sách các nhóm CivilTaxonomy chuẩn và metadata."""
    groups = [
        {"key": item.value, "label": item.value}
        for item in CivilTaxonomy
    ]
    return {
        "groups": groups,
        "verification_levels": [
            {"key": "verified_live", "label": "Đã kiểm chứng trực tiếp (Live)", "badge": "success"},
            {"key": "verified_offline", "label": "Kiểm chứng ngoại tuyến", "badge": "info"},
            {"key": "partial", "label": "Kiểm chứng một phần", "badge": "warning"},
            {"key": "unverified", "label": "Chưa kiểm chứng", "badge": "secondary"},
        ],
    }


@app.get("/api/cases")
def list_or_search_cases(
    q: Optional[str] = Query(default=None),
    group: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    """
    Tìm kiếm hoặc liệt kê các ca kinh nghiệm.
    Hỗ trợ instant search FTS5 và lọc theo nhóm Civil.
    """
    init_db()

    # Nếu có từ khóa tìm kiếm -> Sử dụng bộ máy search FTS5 / Semantic
    if q and q.strip():
        groups_filter = [group] if group and group.strip() else []
        results = search_cases(
            query=q.strip(),
            civil_groups=groups_filter,
            limit=limit,
        )
        return {
            "mode": "search",
            "query": q,
            "total": len(results),
            "cases": [
                {
                    "id": r.case_id,
                    "revision": r.revision,
                    "title": r.title,
                    "summary": r.summary,
                    "verification": r.verification,
                    "civil_groups": r.civil_groups,
                    "plugins": r.plugins,
                    "score": round(r.score, 3),
                    "matched_by": r.matched_by,
                }
                for r in results
            ],
        }

    # Nếu không có từ khóa -> Lấy danh sách từ SQLite
    conn = sqlite3.connect(str(config.DB_PATH))
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, revision, metadata FROM cases ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()

        cases_list = []
        for r in rows:
            meta = json.loads(r["metadata"])
            c_groups = [g.value if hasattr(g, "value") else str(g) for g in meta.get("civil_groups", [])]

            # Lọc theo group nếu có yêu cầu
            if group and group not in c_groups:
                continue

            cases_list.append({
                "id": meta.get("id"),
                "revision": meta.get("revision", 1),
                "title": meta.get("title", ""),
                "summary": meta.get("summary", ""),
                "verification": meta.get("verification", "unverified"),
                "civil_groups": c_groups,
                "plugins": meta.get("plugins", []),
                "updated_at": meta.get("updated_at", ""),
                "civil_version": meta.get("civil_version", ""),
            })

        return {
            "mode": "list",
            "total": len(cases_list),
            "cases": cases_list,
        }
    finally:
        conn.close()


@app.get("/api/cases/{case_id}")
def get_case_detail(case_id: str) -> dict[str, Any]:
    """Lấy chi tiết đầy đủ của một ca kinh nghiệm (metadata, nội dung, các bước)."""
    try:
        meta, content = get_case(case_id)
        
        # Đọc danh sách các revision có sẵn trên đĩa
        rev_dir = config.CASES_DIR / case_id / "revisions"
        revisions = []
        if rev_dir.exists():
            for f in sorted(rev_dir.glob("*.md")):
                revisions.append(f.stem)

        return {
            "status": "success",
            "metadata": meta.model_dump(mode="json"),
            "content": content.model_dump(mode="json"),
            "revisions": revisions,
        }
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy ca {case_id}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/cases")
def create_case(payload: CreateCasePayload) -> dict[str, Any]:
    """Tạo mới một ca kinh nghiệm, lưu an toàn và tự động Push lên GitHub."""
    init_db()

    # Quét bảo mật
    full_text = f"{payload.title} {payload.summary} {payload.problem} {payload.steps}"
    secrets = scan_for_secrets(full_text)
    if secrets:
        raise HTTPException(
            status_code=400,
            detail=f"Phát hiện thông tin nhạy cảm/mật khẩu: {', '.join(secrets)}",
        )

    # Cấp mã ID mới
    case_id = allocate_id()
    now = datetime.now(timezone.utc)
    idempotency_key = f"web-save-{case_id}-{uuid.uuid4().hex[:8]}"

    # Parse civil groups enum
    cg_enums = []
    for g in payload.civil_groups:
        try:
            cg_enums.append(CivilTaxonomy(g))
        except ValueError:
            pass

    ver_enum = (
        VerificationStatus(payload.verification)
        if payload.verification in VerificationStatus._value2member_map_
        else VerificationStatus.verified_live
    )

    content = RevisionContent(
        case_id=case_id,
        revision=1,
        title=payload.title.strip(),
        summary=payload.summary.strip(),
        problem=payload.problem.strip(),
        context=payload.context.strip(),
        cause=payload.cause.strip(),
        cause_is_hypothesis=payload.cause_is_hypothesis,
        steps=payload.steps.strip(),
        result=payload.result.strip(),
        warnings=payload.warnings,
        civil_groups=cg_enums,
        tags=payload.tags,
        aliases=payload.aliases,
        plugins=[{"name": p} for p in payload.plugins],
        sensitivity=payload.sensitivity,
        created_at=now,
        revision_reason="Tạo mới từ Civil Notebook Web",
    )

    metadata = CaseMetadata(
        id=case_id,
        revision=1,
        idempotency_key=idempotency_key,
        title=payload.title.strip(),
        lifecycle=LifecycleStatus.active,
        verification=ver_enum,
        semantic_status=SemanticStatus.semantic_pending,
        civil_groups=cg_enums,
        plugins=payload.plugins,
        created_at=now,
        updated_at=now,
        project=payload.project,
        civil_version=payload.civil_version,
        sensitivity=payload.sensitivity,
    )

    # Lưu nguyên tử vào đĩa và SQLite
    op_id = atomic_save(content=content, metadata=metadata)

    # Tự động commit và push Git
    git_res = git_commit_and_push(case_id=case_id, title=payload.title.strip())

    return {
        "status": "success",
        "case_id": case_id,
        "revision": 1,
        "operation_id": op_id,
        "git_sync": git_res,
        "message": f"Đã lưu thành công ca {case_id}!",
    }


@app.put("/api/cases/{case_id}")
def update_case(case_id: str, payload: UpdateCasePayload) -> dict[str, Any]:
    """Cập nhật một ca kinh nghiệm (tạo Revision mới) và tự động Push GitHub."""
    try:
        old_meta, _ = get_case(case_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy ca {case_id}")

    if old_meta.revision != payload.expected_revision:
        raise HTTPException(
            status_code=409,
            detail=f"Xung đột phiên bản: Ca trên hệ thống đang là Revision {old_meta.revision}, bạn gửi {payload.expected_revision}",
        )

    now = datetime.now(timezone.utc)
    new_rev = old_meta.revision + 1

    cg_enums = []
    for g in payload.civil_groups:
        try:
            cg_enums.append(CivilTaxonomy(g))
        except ValueError:
            pass

    ver_enum = (
        VerificationStatus(payload.verification)
        if payload.verification in VerificationStatus._value2member_map_
        else VerificationStatus.verified_live
    )

    content = RevisionContent(
        case_id=case_id,
        revision=new_rev,
        title=payload.title.strip(),
        summary=payload.summary.strip(),
        problem=payload.problem.strip(),
        context=payload.context.strip(),
        cause=payload.cause.strip(),
        cause_is_hypothesis=payload.cause_is_hypothesis,
        steps=payload.steps.strip(),
        result=payload.result.strip(),
        warnings=payload.warnings,
        civil_groups=cg_enums,
        tags=payload.tags,
        aliases=payload.aliases,
        plugins=[{"name": p} for p in payload.plugins],
        created_at=now,
        revision_reason=payload.revision_reason.strip(),
    )

    metadata = old_meta.model_copy(
        update={
            "revision": new_rev,
            "title": payload.title.strip(),
            "verification": ver_enum,
            "civil_groups": cg_enums,
            "plugins": payload.plugins,
            "updated_at": now,
            "civil_version": payload.civil_version,
            "project": payload.project,
        }
    )

    try:
        op_id = atomic_save(content=content, metadata=metadata, expected_revision=payload.expected_revision)
    except ConflictError as ce:
        raise HTTPException(status_code=409, detail=str(ce))

    # Tự động commit và push Git
    git_res = git_commit_and_push(case_id=case_id, title=f"Rev {new_rev}: {payload.title.strip()}")

    return {
        "status": "success",
        "case_id": case_id,
        "revision": new_rev,
        "operation_id": op_id,
        "git_sync": git_res,
        "message": f"Đã cập nhật ca {case_id} lên Revision {new_rev}!",
    }


@app.post("/api/upload-evidence")
async def upload_evidence(file: UploadFile = File(...)) -> dict[str, Any]:
    """Nhận file ảnh tải lên từ máy tính, lưu vào data/evidence."""
    suffix = Path(file.filename or "image.png").suffix.lower()
    if suffix not in [".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"]:
        suffix = ".png"

    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    unique_name = f"evidence_{timestamp_str}_{uuid.uuid4().hex[:6]}{suffix}"
    dest_path = config.EVIDENCE_DIR / unique_name

    contents = await file.read()
    dest_path.write_bytes(contents)

    return {
        "status": "success",
        "filename": unique_name,
        "url": f"/evidence/{unique_name}",
        "markdown": f"![Hình ảnh minh họa](/evidence/{unique_name})",
    }


@app.post("/api/upload-clipboard")
def upload_clipboard_image(payload: ClipboardUploadPayload) -> dict[str, Any]:
    """Nhận ảnh chụp màn hình CAD paste từ clipboard (Ctrl+V base64)."""
    raw_data = payload.image_base64
    if "," in raw_data:
        # Bỏ phần header data:image/png;base64,
        header, base64_data = raw_data.split(",", 1)
        ext = ".png"
        if "jpeg" in header or "jpg" in header:
            ext = ".jpg"
        elif "webp" in header:
            ext = ".webp"
    else:
        base64_data = raw_data
        ext = ".png"

    try:
        image_bytes = base64.b64decode(base64_data)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Dữ liệu ảnh base64 không hợp lệ: {e}")

    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    unique_name = f"cad_screenshot_{timestamp_str}_{uuid.uuid4().hex[:6]}{ext}"
    dest_path = config.EVIDENCE_DIR / unique_name
    dest_path.write_bytes(image_bytes)

    return {
        "status": "success",
        "filename": unique_name,
        "url": f"/evidence/{unique_name}",
        "markdown": f"![Ảnh chụp màn hình CAD](/evidence/{unique_name})",
    }


@app.post("/api/sync")
def sync_with_github() -> dict[str, Any]:
    """Kéo dữ liệu mới nhất từ GitHub về (Pull) và tự động Rebuild chỉ mục."""
    res = git_pull(rebuild_on_change=True)
    return res


@app.get("/", response_class=HTMLResponse)
def index_page():
    """Phục vụ trang chủ giao diện Civil Notebook."""
    index_file = TEMPLATES_DIR / "index.html"
    if not index_file.exists():
        return HTMLResponse("<h1>Civil Notebook Backend is Running! (Template loading...)</h1>")
    return HTMLResponse(index_file.read_text(encoding="utf-8"))
