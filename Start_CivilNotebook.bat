@echo off
title Civil Notebook - So tay Ky thuat Civil 3D
chcp 65001 > nul
cd /d "%~dp0"

echo =================================================================
echo   📘 DANG KHOI CHAY SO TAY DIEN TU CIVIL 3D (CIVIL NOTEBOOK)
echo =================================================================
echo.
echo * Tu dong mo trinh duyet tai: http://127.0.0.1:8765
echo * Nhan Ctrl + C de dung ung dung.
echo.

python run_web.py

pause
