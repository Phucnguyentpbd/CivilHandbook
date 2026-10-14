"""
config.py — Cấu hình trung tâm cho CivilHandbook.
Mọi path và constant đều lấy từ đây; không hardcode ở nơi khác.
"""

from pathlib import Path

# ─── Đường dẫn gốc ────────────────────────────────────────────────────────────

DATA_DIR = Path("D:/AGENT_AI/CivilHandbook/data")

CASES_DIR        = DATA_DIR / "cases"
EVIDENCE_DIR     = DATA_DIR / "evidence"
TAXONOMY_DIR     = DATA_DIR / "taxonomy"
INDEX_DIR        = DATA_DIR / "index"
BACKUP_QUEUE_DIR = DATA_DIR / "backup-queue"

DB_PATH = INDEX_DIR / "handbook.sqlite"

# ─── Embedding ────────────────────────────────────────────────────────────────

EMBEDDING_PROVIDER = "google"
EMBEDDING_MODEL    = "gemini-embedding-2"
EMBEDDING_DIM      = 768          # kích thước vector float32

# ─── Tìm kiếm ─────────────────────────────────────────────────────────────────

MAX_SEARCH_RESULTS = 5   # tối đa kéo về từ DB
MAX_CONTEXT_ITEMS  = 3   # tối đa nạp vào ngữ cảnh agent

# ─── SQLite ───────────────────────────────────────────────────────────────────

SQLITE_BUSY_TIMEOUT_MS = 5_000   # ms

# ─── Backup ───────────────────────────────────────────────────────────────────

BACKUP_DEST = Path("G:/My Drive/CodexSync/backups/CivilHandbook")
BACKUP_MAX_SNAPSHOTS = 30        # giữ tối đa 30 snapshot ngày
