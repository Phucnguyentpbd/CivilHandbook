"""
secret_scanner.py — Quét văn bản để phát hiện thông tin nhạy cảm
trước khi lưu vào kho hoặc gửi lên cloud.

Phát hiện:
- API key / Bearer token
- Mật khẩu dạng key=value
- Connection string (SQL Server, PostgreSQL, MySQL, MongoDB)
- Cookie header
- Path traversal (../ hoặc .."+"\\)"
"""

from __future__ import annotations

import re
from typing import Sequence

# ─── Pattern definitions ──────────────────────────────────────────────────────

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "api_key",
        re.compile(
            r"(?i)(api[_\-]?key|apikey|access[_\-]?key|secret[_\-]?key)"
            r"\s*[=:]\s*['\"]?[\w\-]{16,}['\"]?",
            re.IGNORECASE,
        ),
    ),
    (
        "bearer_token",
        re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]+=*", re.IGNORECASE),
    ),
    (
        "password",
        re.compile(
            r"(?i)(password|passwd|pwd|secret)\s*[=:]\s*['\"]?[^\s'\",;]{4,}['\"]?",
            re.IGNORECASE,
        ),
    ),
    (
        "connection_string_sql",
        re.compile(
            r"(?i)(Server|Data Source|Initial Catalog|User Id|Password)\s*=",
            re.IGNORECASE,
        ),
    ),
    (
        "connection_string_uri",
        re.compile(
            r"(?i)(postgres|mysql|mongodb|mssql|sqlite)://[^\s\"']{5,}",
            re.IGNORECASE,
        ),
    ),
    (
        "cookie_header",
        re.compile(r"(?i)\bCookie\s*:\s*\S+", re.IGNORECASE),
    ),
    (
        "google_api_key",
        re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b"),
    ),
    (
        "path_traversal",
        re.compile(r"(\.\.[/\\]){1,}"),
    ),
]


def scan(text: str) -> list[str]:
    """
    Quét văn bản và trả về danh sách cảnh báo nếu phát hiện dữ liệu nhạy cảm.

    Args:
        text: Văn bản cần kiểm tra.

    Returns:
        Danh sách chuỗi cảnh báo (rỗng = không phát hiện vấn đề).
    """
    warnings: list[str] = []
    for label, pattern in _PATTERNS:
        match = pattern.search(text)
        if match:
            # Chỉ hiện 30 ký tự đầu của match để không lộ bí mật
            snippet = match.group(0)[:30].replace("\n", " ")
            warnings.append(
                f"[SECRET_SCANNER] Phát hiện '{label}': '{snippet}...'"
            )
    return warnings


def scan_many(texts: Sequence[str]) -> list[str]:
    """Quét nhiều đoạn văn bản và gộp cảnh báo."""
    all_warnings: list[str] = []
    for text in texts:
        all_warnings.extend(scan(text))
    return all_warnings


def has_secrets(text: str) -> bool:
    """Trả True nếu phát hiện ít nhất một bí mật."""
    return bool(scan(text))
