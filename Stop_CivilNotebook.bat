@echo off
title Dung Civil Notebook
chcp 65001 > nul

echo Dang dung tien trinh Civil Notebook chay ngam...

for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8765 ^| findstr LISTENING') do (
    taskkill /F /PID %%a > nul 2>&1
    echo Da tat server Civil Notebook (PID: %%a).
)

echo Hoan tat.
timeout /t 2 > nul
