@echo off
chcp 65001 >nul
title 视频字幕处理器 - 一键启动
echo.
echo ==========================================
echo   视频字幕处理器 - 一键启动
echo ==========================================
echo.

cd /d "%~dp0"

:: 检查 Python
echo [1/3] 检查 Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo   X Python 未安装！
    pause
    exit /b 1
)
echo   [OK] Python 已安装

:: 检查依赖
echo.
echo [2/3] 检查依赖...
python -c "import flask" 2>nul
if errorlevel 1 (
    echo   正在安装 Flask...
    python -m pip install flask flask-cors -q
)
echo   [OK] 依赖已就绪

:: 启动服务
echo.
echo [3/3] 启动服务...
echo.

:: 启动后端 API
echo   启动后端 API (端口 5000)...
start "后端API - 视频字幕处理器" cmd /k "python app.py"

:: 等待
timeout /t 2 /nobreak >nul

:: 启动前端服务器
echo   启动前端服务器 (端口 8080)...
start "前端服务器 - 视频字幕处理器" cmd /k "python -m http.server 8080"

:: 等待
timeout /t 2 /nobreak >nul

:: 打开浏览器
echo.
echo   打开浏览器...
start http://localhost:8080

echo.
echo ==========================================
echo   启动完成！
echo ==========================================
echo.
echo   访问: http://localhost:8080
echo.
echo   如果显示"API已连接"就成功了！
echo   如果显示"离线模式"，等 5 秒后刷新页面
echo.
pause
