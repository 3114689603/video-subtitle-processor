@echo off
chcp 65001 >nul
title 视频字幕处理器 - 停止服务
echo.
echo ==========================================
echo   视频字幕处理器 - 停止服务
echo ==========================================
echo.

echo [1/2] 查找并停止相关进程...

:: 停止 Python 进程（针对这个项目）
for /f "tokens=2" %%a in ('tasklist ^| findstr "python.exe"') do (
    echo   停止进程 PID: %%a
    taskkill /F /PID %%a >nul 2>&1
)

:: 关闭相关的 CMD 窗口
echo.
echo [2/2] 关闭服务窗口...
taskkill /FI "WINDOWTITLE eq 后端API - 视频字幕处理器*" /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq 前端服务器 - 视频字幕处理器*" /F >nul 2>&1

echo.
echo ==========================================
echo   所有服务已停止！
echo ==========================================
echo.
pause
