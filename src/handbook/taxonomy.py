"""
taxonomy.py — Quản lý taxonomy đối tượng Civil và danh mục plugin.
- Load taxonomy từ JSON.
- Scan PackageContents.xml để nhận diện plugin (chỉ đọc, không load DLL).
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from . import config


# ─── Load taxonomy ────────────────────────────────────────────────────────────

def load_civil_groups(taxonomy_dir: Path | None = None) -> list[dict[str, Any]]:
    """
    Đọc danh sách nhóm Civil từ civil_groups.json.

    Returns:
        Danh sách dict với keys: id, name, aliases.
    """
    path = (taxonomy_dir or config.TAXONOMY_DIR) / "civil_groups.json"
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_known_plugins(taxonomy_dir: Path | None = None) -> list[dict[str, Any]]:
    """
    Đọc danh sách plugin đã biết từ plugins.json.

    Returns:
        Danh sách dict với keys: name, version, bundle, dll, commands.
    """
    path = (taxonomy_dir or config.TAXONOMY_DIR) / "plugins.json"
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ─── Scan PackageContents.xml ─────────────────────────────────────────────────

_AUTOCAD_BUNDLE_DIRS: list[Path] = [
    Path(r"C:\ProgramData\Autodesk\ApplicationPlugins"),
    Path(r"C:\Users") / "Public" / "Documents" / "Autodesk" / "ApplicationPlugins",
]


def scan_package_contents(
    bundle_dirs: list[Path] | None = None,
) -> list[dict[str, Any]]:
    """
    Quét các thư mục AutoCAD bundle để tìm PackageContents.xml.
    Chỉ đọc XML — KHÔNG tải DLL, KHÔNG thay đổi bundle.

    Args:
        bundle_dirs: Danh sách thư mục gốc để tìm bundle (mặc định: vị trí chuẩn).

    Returns:
        Danh sách dict mô tả từng plugin tìm thấy:
            {name, version, description, product_code, components, bundle_path}
    """
    dirs_to_scan = bundle_dirs if bundle_dirs is not None else _AUTOCAD_BUNDLE_DIRS
    results: list[dict[str, Any]] = []

    for base_dir in dirs_to_scan:
        if not base_dir.exists():
            continue
        for xml_file in base_dir.rglob("PackageContents.xml"):
            try:
                info = _parse_package_contents(xml_file)
                if info:
                    results.append(info)
            except ET.ParseError:
                results.append(
                    {
                        "name": xml_file.parent.name,
                        "bundle_path": str(xml_file.parent),
                        "error": "XML parse error",
                        "identity": "unverified_plugin_identity",
                    }
                )
            except Exception as exc:
                results.append(
                    {
                        "name": xml_file.parent.name,
                        "bundle_path": str(xml_file.parent),
                        "error": str(exc),
                        "identity": "unverified_plugin_identity",
                    }
                )

    return results


def _parse_package_contents(xml_path: Path) -> dict[str, Any] | None:
    """Parse một file PackageContents.xml và trích xuất metadata."""
    tree = ET.parse(xml_path)
    root = tree.getroot()

    # Namespace có thể có hoặc không
    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag.split("}")[0] + "}"

    app_info = root.find(f"{ns}ApplicationPackage") or root

    # Lấy thuộc tính từ tag gốc hoặc ApplicationPackage
    name         = app_info.get("Name") or app_info.get("name") or xml_path.parent.name
    version      = app_info.get("AppVersion") or app_info.get("Version") or ""
    description  = app_info.get("Description") or ""
    product_code = app_info.get("ProductCode") or ""

    # Thu thập các component (file .dll, .arx, .lsp, ...) để ghi chú
    # Không load — chỉ ghi tên file
    components: list[str] = []
    for comp in root.iter(f"{ns}Components"):
        for runtime in comp.iter(f"{ns}RuntimeRequirements"):
            _ = runtime  # bỏ qua
        for entry in comp.iter():
            module = entry.get("ModuleName") or entry.get("moduleName") or ""
            if module and module not in components:
                components.append(module)

    return {
        "name": name,
        "version": version,
        "description": description,
        "product_code": product_code,
        "components": components,  # chỉ là danh sách tên file, không load
        "bundle_path": str(xml_path.parent),
        "identity": "package_contents_scan",
    }
