@echo off
chcp 65001 >nul
title 视频字幕处理器 - 一键启动
echo.
echo ==========================================
echo   视频字幕处理器 - 一键启动
echo ==========================================
echo.

:: 获取项目根目录
set "SCRIPT_DIR=%~dp0"
set "BASE_DIR=%SCRIPT_DIR%.."
cd /d "%BASE_DIR%"

echo [工作目录] %CD%

:: 检查 Python
echo [1/4] 检查 Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo   X Python 未安装！请先安装 Python
    echo   下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)
echo   [OK] Python 已安装

:: 检查端口占用
echo.
echo [2/4] 检查端口...
netstat -ano | findstr ":5000" >nul
if not errorlevel 1 (
    echo   [!] 端口 5000 已被占用，尝试关闭占用进程...
    for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5000"') do (
        taskkill /F /PID %%a >nul 2>&1
    )
    timeout /t 1 /nobreak >nul
)
netstat -ano | findstr ":8080" >nul
if not errorlevel 1 (
    echo   [!] 端口 8080 已被占用，尝试关闭占用进程...
    for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8080"') do (
        taskkill /F /PID %%a >nul 2>&1
    )
    timeout /t 1 /nobreak >nul
)
echo   [OK] 端口检查完成

:: 检查依赖
echo.
echo [3/4] 检查依赖...
python -c "import flask" 2>nul
if errorlevel 1 (
    echo   [→] 正在安装 Flask...
    python -m pip install flask flask-cors -q
)
echo   [OK] 依赖已就绪

:: 启动服务
echo.
echo [4/4] 启动服务...
echo.
echo ==========================================
echo   正在启动服务...
echo ==========================================
echo.

:: 启动后端 API（后台运行）
start "后端API - 视频字幕处理器" cmd /k "cd /d %CD%\backend && echo 启动后端API... && python single_camera.py"

:: 等待后端启动
timeout /t 3 /nobreak >nul

:: 启动前端服务器（后台运行）
start "前端服务器 - 视频字幕处理器" cmd /k "cd /d %CD% && echo 启动前端服务器... && python -m http.server 8080"

:: 等待前端启动
timeout /t 2 /nobreak >nul

:: 检查服务是否启动成功
echo.
echo   检查服务状态...
curl -s http://localhost:5000/api/health >nul 2>&1
if errorlevel 1 (
    echo   [!] API 可能还没准备好，再等 3 秒...
    timeout /t 3 /nobreak >nul
)

:: 打开浏览器
echo   [OK] 正在打开浏览器...
start "" "http://localhost:8080/frontend/single/index.html"

echo.
echo ==========================================
echo   启动完成！
echo ==========================================
echo.
echo   前端页面: http://localhost:8080/frontend/single/
echo   API地址: http://localhost:5000
echo   健康检查: http://localhost:5000/api/health
echo.
echo   [提示] 如果页面显示"API已连接"，说明成功了！
echo.
echo   关闭方式:
echo   1. 关闭弹出的两个CMD窗口
echo   2. 或运行 scripts\stop.bat
echo.
pause
