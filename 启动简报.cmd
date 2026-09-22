@echo off
title 每日简报 — 关掉这个窗口就停止服务
cd /d "%~dp0"

netstat -ano | findstr /C:":8787 " | findstr /C:"LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo.
    echo   服务已经在运行了，直接打开界面...
    start "" "http://127.0.0.1:8787"
    timeout /t 2 >nul
    exit /b
)

echo.
echo   ============================================
echo     每日简报 . 本地阅读界面
echo   ============================================
echo     浏览器会自动打开 http://127.0.0.1:8787
echo     关掉这个窗口 = 停止服务
echo.
echo   正在启动...
echo.

".venv\Scripts\python.exe" -m newspipe serve --open --port 8787

echo.
echo   服务已停止。按任意键关闭窗口。
pause >nul