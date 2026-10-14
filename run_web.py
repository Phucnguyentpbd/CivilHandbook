"""
run_web.py — Khởi chạy ứng dụng Web Sổ tay Civil (Civil Notebook).
Hỗ trợ kiểm tra cổng thông minh: Nếu server đã chạy thì chỉ mở cửa sổ ứng dụng,
không bao giờ bị xung đột cổng hay tạo cửa sổ đen CMD.
"""

import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR / "src"))

PORT = 8765
HOST = "127.0.0.1"
URL = f"http://{HOST}:{PORT}"

EDGE_PATH = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
CHROME_PATH = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")


def is_server_running(host: str, port: int) -> bool:
    """Kiểm tra server đã chạy trên cổng này chưa."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            return s.connect_ex((host, port)) == 0
    except Exception:
        return False


def open_app_window():
    """Mở giao diện dưới dạng Desktop App Window (không thanh URL) cực đẹp."""
    time.sleep(0.8)
    # 1. Thử mở qua Microsoft Edge dạng App Mode
    if EDGE_PATH.exists():
        try:
            subprocess.Popen([str(EDGE_PATH), f"--app={URL}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except Exception:
            pass

    # 2. Thử Chrome App Mode
    if CHROME_PATH.exists():
        try:
            subprocess.Popen([str(CHROME_PATH), f"--app={URL}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except Exception:
            pass

    # 3. Fallback mở trình duyệt mặc định
    webbrowser.open(URL)


if __name__ == "__main__":
    # Nếu server đã đang chạy ngầm rồi -> Chỉ mở cửa sổ App và thoát
    if is_server_running(HOST, PORT):
        open_app_window()
        sys.exit(0)

    # Nếu chưa chạy -> Khởi động server và mở cửa sổ App
    threading.Thread(target=open_app_window, daemon=True).start()

    import uvicorn
    from handbook.web.app import app

    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
