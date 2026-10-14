"""
models.py — Pydantic v2 models cho CivilHandbook.
Định nghĩa tất cả enum, schema metadata, schema nội dung revision
và các request DTO (SaveRequest, ReviseRequest, SearchRequest, UndoRequest).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ─── Enum trạng thái ──────────────────────────────────────────────────────────

class VerificationStatus(str, Enum):
    """Mức kiểm chứng của một ca kinh nghiệm."""
    verified_live    = "verified_live"    # thấy hậu điều kiện trực tiếp trong Civil
    verified_offline = "verified_offline" # kiểm tra qua build/test/tài liệu chính thức
    partial          = "partial"          # mới kiểm tra được một phần
    unverified       = "unverified"       # hướng dẫn / giả thuyết


class LifecycleStatus(str, Enum):
    """Trạng thái vòng đời của một ca."""
    active     = "active"
    superseded = "superseded"
    deprecated = "deprecated"
    deleted    = "deleted"


class SemanticStatus(str, Enum):
    """Trạng thái lập chỉ mục embedding."""
    indexed          = "indexed"
    semantic_pending = "semantic_pending"
    local_only       = "local_only"       # không gọi cloud embedding


# ─── Taxonomy ─────────────────────────────────────────────────────────────────

class CivilTaxonomy(str, Enum):
    """18 nhóm phân loại đối tượng Civil 3D (v1)."""
    drawing_units_crs            = "Drawing/Units/CRS"
    points_survey                = "Points/Survey"
    surface                      = "Surface"
    alignment                    = "Alignment"
    profile_profile_view         = "Profile/Profile View"
    sample_line_section_view     = "Sample Line/Section/Section View"
    assembly_subassembly_sac     = "Assembly/Subassembly/SAC"
    corridor                     = "Corridor"
    feature_line_grading         = "Feature Line/Grading"
    parcels                      = "Parcels"
    pipe_pressure_network        = "Pipe/Pressure Network"
    quantity_earthwork           = "Quantity/Earthwork"
    styles_labels                = "Styles/Labels"
    data_shortcuts_references    = "Data Shortcuts/References"
    layout_viewport_plot         = "Layout/Viewport/Plot"
    map_gis                      = "Map/GIS"
    automation_api               = "Automation/API"
    plugin_mcp                   = "Plugin/MCP"


# ─── Metadata (case.json) ─────────────────────────────────────────────────────

class CaseMetadata(BaseModel):
    """Metadata lưu trong case.json — thay đổi khi tạo revision mới."""

    model_config = {"extra": "forbid"}

    id: str = Field(..., pattern=r"^CIV-\d{4}$", description="Mã mục, ví dụ CIV-0001")
    revision: int = Field(..., ge=1, description="Revision hiện hành (bắt đầu từ 1)")
    idempotency_key: str = Field(..., min_length=1, max_length=256)
    title: str = Field(..., min_length=1, max_length=500)

    lifecycle: LifecycleStatus    = LifecycleStatus.active
    verification: VerificationStatus = VerificationStatus.unverified
    semantic_status: SemanticStatus  = SemanticStatus.semantic_pending

    civil_groups: list[CivilTaxonomy] = Field(default_factory=list)
    plugins: list[str]                = Field(default_factory=list)

    created_at: datetime
    updated_at: datetime
    last_verified_at: Optional[datetime] = None

    related_to: list[str]     = Field(default_factory=list)
    conflicts_with: list[str] = Field(default_factory=list)
    supersedes: Optional[str] = None

    sensitivity: str = Field(default="default", description="default | project_confidential | local_only")
    project: Optional[str]       = None
    civil_version: Optional[str] = None

    @field_validator("civil_groups", mode="before")
    @classmethod
    def deduplicate_groups(cls, v: Any) -> list:
        seen: set = set()
        result = []
        for item in v:
            key = item.value if isinstance(item, CivilTaxonomy) else item
            if key not in seen:
                seen.add(key)
                result.append(item)
        return result


# ─── Nội dung revision (Markdown frontmatter + body) ─────────────────────────

class RevisionContent(BaseModel):
    """Nội dung đầy đủ một revision — lưu thành file Markdown bất biến."""

    model_config = {"extra": "forbid"}

    # ── Trường bắt buộc ───────────────────────────────────────────────────
    case_id: str  = Field(..., pattern=r"^CIV-\d{4}$")
    revision: int = Field(..., ge=1)

    title: str   = Field(..., min_length=1, max_length=500)
    summary: str = Field(..., min_length=1, max_length=2000,
                         description="Tóm tắt cô đọng — dùng để embed lên cloud")

    problem: str = Field(default="",
                         description="Vấn đề / triệu chứng")
    context: str = Field(default="",
                         description="Bối cảnh: phiên bản Civil/AutoCAD/QGIS, loại bản vẽ")

    # ── Nguyên nhân ───────────────────────────────────────────────────────
    cause: str = Field(default="",
                       description="Nguyên nhân đã xác định (ghi rõ nếu là giả thuyết)")
    cause_is_hypothesis: bool = False

    # ── Xử lý ─────────────────────────────────────────────────────────────
    steps: str = Field(default="", description="Các bước xử lý")
    result: str = Field(default="", description="Kết quả và bằng chứng kiểm tra")

    # ── Những cách đã thử nhưng thất bại ─────────────────────────────────
    failed_attempts: list[str] = Field(default_factory=list)

    # ── Cảnh báo / phạm vi / hoàn tác ────────────────────────────────────
    warnings: list[str]   = Field(default_factory=list)
    scope_notes: str      = Field(default="")
    undo_procedure: str   = Field(default="")

    # ── Taxonomy & alias ──────────────────────────────────────────────────
    civil_groups: list[CivilTaxonomy] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list,
                               description="Các tên gọi tương đương Anh–Việt")
    tags: list[str]    = Field(default_factory=list)

    # ── Plugin ────────────────────────────────────────────────────────────
    plugins: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Danh sách plugin: {name, version, commands, bundle, dll, module_hash}"
        ),
    )

    # ── Nguồn ─────────────────────────────────────────────────────────────
    sources: list[str] = Field(default_factory=list,
                               description="Chat/task ID, URL, đường dẫn, ảnh, tài liệu")

    # ── Quan hệ ───────────────────────────────────────────────────────────
    related_to: list[str]     = Field(default_factory=list)
    conflicts_with: list[str] = Field(default_factory=list)
    supersedes: Optional[str] = None

    # ── Mức nhạy cảm ──────────────────────────────────────────────────────
    sensitivity: str = Field(default="default")

    # ── Thời gian ─────────────────────────────────────────────────────────
    created_at: datetime
    revision_reason: str = Field(default="initial",
                                 description="Lý do tạo revision (ví dụ: 'sửa nguyên nhân')")


# ─── Request DTOs ─────────────────────────────────────────────────────────────

class SaveRequest(BaseModel):
    """Yêu cầu lưu ca mới."""

    model_config = {"extra": "forbid"}

    idempotency_key: str = Field(..., min_length=1, max_length=256)
    content: RevisionContent
    metadata_overrides: dict[str, Any] = Field(
        default_factory=dict,
        description="Ghi đè metadata nếu cần (ví dụ: sensitivity, project, civil_version)",
    )


class ReviseRequest(BaseModel):
    """Yêu cầu thêm revision mới cho ca đã có."""

    model_config = {"extra": "forbid"}

    case_id: str          = Field(..., pattern=r"^CIV-\d{4}$")
    expected_revision: int = Field(..., ge=1,
                                   description="Revision hiện tại mà client đang dựa vào (optimistic lock)")
    idempotency_key: str  = Field(..., min_length=1, max_length=256)
    content: RevisionContent
    revision_reason: str  = Field(..., min_length=1, max_length=500)


class SearchRequest(BaseModel):
    """Tham số tìm kiếm lai."""

    model_config = {"extra": "forbid"}

    query: str = Field(..., min_length=1, max_length=1000)
    civil_groups: list[CivilTaxonomy]      = Field(default_factory=list)
    plugins: list[str]                     = Field(default_factory=list)
    civil_version: Optional[str]           = None
    project: Optional[str]                 = None
    verification: list[VerificationStatus] = Field(default_factory=list)
    lifecycle: list[LifecycleStatus]       = Field(
        default_factory=lambda: [LifecycleStatus.active]
    )
    max_results: int = Field(default=5, ge=1, le=10)
    include_local_only: bool = False


class UndoRequest(BaseModel):
    """Yêu cầu hoàn tác thao tác lưu / sửa."""

    model_config = {"extra": "forbid"}

    operation_id: str = Field(..., min_length=1)
    idempotency_key: str = Field(..., min_length=1, max_length=256)
