import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR / "src"))

LOG_FILE = ROOT_DIR / "web_server.log"
f = open(LOG_FILE, "a", encoding="utf-8", buffering=1)
sys.stdout = f
sys.stderr = f

import uvicorn
from handbook.web.app import app

if __name__ == "__main__":
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=8765,
        log_level="warning",
        access_log=False,
    )
