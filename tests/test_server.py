"""
tests/test_server.py — Unit tests cho FastMCP Server của CivilHandbook.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from handbook.server import (
    civil_handbook_health,
    civil_handbook_save_case,
    civil_handbook_get_case,
    civil_handbook_revise_case,
    civil_handbook_undo_change,
    civil_handbook_search_cases,
)
from handbook.storage import init_db


def test_server_health():
    res_str = civil_handbook_health()
    data = json.loads(res_str)
    assert data["status"] in ("healthy", "degraded")
    assert "storage" in data
    assert data["runtime"]["cad_execute_allowed"] is False


def test_server_save_get_revise_undo_flow():
    init_db()

    import uuid
    save_key = f"test-server-save-{uuid.uuid4()}"
    revise_key = f"test-server-revise-{uuid.uuid4()}"

    # 1. Lưu ca mới
    save_res_str = civil_handbook_save_case(
        title="Kiểm thử trắc ngang server MCP",
        summary="Thử nghiệm lưu ca từ server MCP",
        problem="Lỗi khi tạo trắc ngang Civil 3D",
        context="Civil 3D 2027",
        idempotency_key=save_key,
        civil_groups=["Sample Line/Section/Section View"],
        verification="verified_offline",
    )
    save_data = json.loads(save_res_str)
    assert save_data["status"] == "success"
    case_id = save_data["case_id"]
    assert case_id.startswith("CIV-")
    assert save_data["revision"] == 1
    op_id = save_data["operation_id"]

    # 2. Đọc ca
    get_res_str = civil_handbook_get_case(case_id=case_id)
    get_data = json.loads(get_res_str)
    assert get_data["status"] == "success"
    assert get_data["content"]["title"] == "Kiểm thử trắc ngang server MCP"

    # 3. Tìm kiếm
    search_res_str = civil_handbook_search_cases(query="trắc ngang")
    search_data = json.loads(search_res_str)
    assert search_data["status"] == "success"
    ids = [r["case_id"] for r in search_data["results"]]
    assert case_id in ids

    # 4. Revise ca
    revise_res_str = civil_handbook_revise_case(
        case_id=case_id,
        expected_revision=1,
        idempotency_key=revise_key,
        revision_reason="Cập nhật kết quả kiểm thử",
        result="Đã kiểm tra thành công trong Civil 3D 2027",
    )
    revise_data = json.loads(revise_res_str)
    assert revise_data["status"] == "success"
    assert revise_data["new_revision"] == 2

    # 5. Hoàn tác
    undo_res_str = civil_handbook_undo_change(operation_id=op_id)
    undo_data = json.loads(undo_res_str)
    assert undo_data["status"] == "success"


def test_server_rejects_secret():
    res_str = civil_handbook_save_case(
        title="Lỗi kết nối",
        summary="Server=127.0.0.1;User Id=sa;Password=supersecretpass;",
        problem="Bị lộ connection string",
        context="Civil 3D",
        idempotency_key="test-server-secret-reject",
    )
    data = json.loads(res_str)
    assert data["status"] == "rejected_security"
    assert len(data["warnings"]) > 0


def test_server_sync_tool():
    from handbook.server import civil_handbook_sync
    res_str = civil_handbook_sync()
    data = json.loads(res_str)
    assert "status" in data

