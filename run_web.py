"""
run_web.py — Khởi chạy ứng dụng Web Sổ tay Civil (Civil Notebook).
Tự động mở trình duyệt web khi server sẵn sàng.
"""

import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

# Đảm bảo import được src/handbook
ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR / "src"))

import uvicorn
from handbook.web.app import app

PORT = 8765
HOST = "127.0.0.1"
URL = f"http://{HOST}:{PORT}"


def open_browser():
    time.sleep(1.2)
    print(f"\n[Civil Notebook] Đang mở giao diện tại: {URL}")
    webbrowser.open(URL)


if __name__ == "__main__":
    print("=" * 65)
    print("   📘 CIVIL NOTEBOOK — SỔ TAY KỸ THUẬT CIVIL 3D & AUTOCAD")
    print(f"   Khởi chạy server tại: {URL}")
    print("   Nhấn Ctrl + C để dừng ứng dụng.")
    print("=" * 65)

    # Mở trình duyệt trong luồng riêng
    threading.Thread(target=open_browser, daemon=True).start()

    # Chạy uvicorn server
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
