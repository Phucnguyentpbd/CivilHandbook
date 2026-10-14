"""
text_utils.py — Tiện ích chuẩn hóa văn bản tiếng Việt cho FTS.

Cột FTS lưu bản lowercase + NFC + không dấu để hỗ trợ tìm kiếm cả
câu có dấu lẫn không dấu. Nội dung gốc vẫn được giữ nguyên trong
cột riêng.
"""

import unicodedata
import re


def normalize_vn(text: str) -> str:
    """
    Chuẩn hóa văn bản tiếng Việt cho cột FTS:
    - Lowercase
    - NFC normalization (ghép dấu)
    - Bỏ dấu thanh và dấu kết hợp (combining characters)

    Ví dụ:
        >>> normalize_vn("Đường Hầm Trắc Ngang")
        'duong ham trac ngang'
        >>> normalize_vn("CIVIL 3D - Lỗi FTS5")
        'civil 3d - loi fts5'
    """
    # Bước 1: lowercase và thay thế đ/Đ
    text = text.lower().replace("đ", "d")
    text = unicodedata.normalize("NFC", text)
    # Bước 2: NFKD để tách dấu kết hợp ra khỏi ký tự cơ sở
    nfkd = unicodedata.normalize("NFKD", text)
    # Bước 3: Bỏ combining characters (dấu tiếng Việt, dấu huyền, sắc, v.v.)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def normalize_for_fts(text: str) -> str:
    """
    Alias của normalize_vn — dùng nhất quán trong FTS pipeline.
    """
    return normalize_vn(text)


def build_fts_text(parts: list[str]) -> str:
    """
    Ghép nhiều đoạn văn bản thành một chuỗi FTS (dùng cho index).
    Bỏ phần None/empty, join bằng space.
    """
    return " ".join(normalize_vn(p) for p in parts if p and p.strip())


def tokenize_query(query: str) -> list[str]:
    """
    Tách query thành list token không dấu, lowercase.
    Dùng để highlight hoặc debug.
    """
    normalized = normalize_vn(query)
    return [t for t in re.split(r"\s+", normalized) if t]
