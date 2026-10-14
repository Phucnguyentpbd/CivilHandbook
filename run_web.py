"""
run_web.py — Khởi chạy ứng dụng Web Sổ tay Civil (Civil Notebook).
Tương thích hoàn hảo với pythonw.exe (chạy ngầm không cửa sổ),
tự động kiểm tra cổng và mở cửa sổ Desktop App Window.
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

# Xử lý stdout/stderr khi chạy qua pythonw.exe (tránh crash ngầm)
LOG_FILE = ROOT_DIR / "web_server.log"
if sys.stdout is None:
    try:
        sys.stdout = open(LOG_FILE, "a", encoding="utf-8", buffering=1)
        sys.stderr = sys.stdout
    except Exception:
        pass

PORT = 8765
HOST = "127.0.0.1"
URL = f"http://{HOST}:{PORT}"

EDGE_PATH = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
CHROME_PATH = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")


def is_server_running(host: str, port: int) -> bool:
    """Kiểm tra server đã thực sự lắng nghe trên cổng này chưa."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.3)
            return s.connect_ex((host, port)) == 0
    except Exception:
        return False


def open_app_window_now():
    """Mở giao diện dưới dạng Desktop App Window (không thanh URL) cực đẹp."""
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


def wait_and_open_app():
    """Vòng lặp đợi server bind port thành công rồi mới mở cửa sổ."""
    for _ in range(40):  # Đợi tối đa 20 giây (40 x 0.5s)
        if is_server_running(HOST, PORT):
            time.sleep(0.3)
            open_app_window_now()
            return
        time.sleep(0.5)

    open_app_window_now()


if __name__ == "__main__":
    # 1. Nếu server đã đang chạy ngầm sẵn -> Chỉ mở cửa sổ App và thoát
    if is_server_running(HOST, PORT):
        open_app_window_now()
        sys.exit(0)

    # 2. Nếu chưa chạy -> Bật luồng đợi server sẵn sàng mới mở App
    threading.Thread(target=wait_and_open_app, daemon=True).start()

    # 3. Khởi động uvicorn server (không dùng log config mặc định để an toàn với pythonw)
    import uvicorn
    from handbook.web.app import app

    uvicorn.run(
        app,
        host=HOST,
        port=PORT,
        log_level="warning",
        access_log=False,
    )
