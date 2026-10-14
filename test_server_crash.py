import sys
import traceback
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR / "src"))

crash_log = ROOT_DIR / "crash.log"

try:
    import uvicorn
    from handbook.web.app import app

    print("Starting uvicorn...")
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="info")
except Exception as e:
    with open(crash_log, "w", encoding="utf-8") as f:
        f.write(f"Exception: {e}\n")
        traceback.print_exc(file=f)
