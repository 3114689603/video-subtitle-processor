@echo off
chcp 65001 >nul

:: 获取脚本所在目录的父目录（项目根目录）
set "SCRIPT_DIR=%~dp0"
set "BASE_DIR=%SCRIPT_DIR%.."

:: 切换到项目根目录
cd /d "%BASE_DIR%"

echo ========================================
echo 多机位访谈字幕处理器
echo ========================================
echo.

:: 检查 Python
py --version >nul 2>&1
if errorlevel 1 (
    echo [错误] Python 未安装或未添加到 PATH
    pause
    exit /b 1
)

:: 检查依赖
echo [检查] 检查依赖...
py -c "import flask" 2>nul || (
    echo [安装] 安装 Flask...
    py -m pip install flask flask-cors -q
)

py -c "import whisper" 2>nul || (
    echo [安装] 安装 Whisper...
    py -m pip install openai-whisper -q
)

py -c "import librosa" 2>nul || (
    echo [安装] 安装音频处理库...
    py -m pip install librosa scikit-learn soundfile -q
)

:: 清理端口
echo [清理] 检查端口占用...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :5000') do (
    taskkill /F /PID %%a >nul 2>&1
)
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8080') do (
    taskkill /F /PID %%a >nul 2>&1
)

echo.
echo [启动] 启动多机位后端服务...
echo 工作目录: %CD%
start "多机位后端" cmd /k "cd /d %CD%\backend && py multi_camera.py"

timeout /t 3 /nobreak >nul

echo [启动] 启动前端服务器...
start "前端服务器" cmd /k "cd /d %CD% && py -m http.server 8080"

timeout /t 2 /nobreak >nul

echo [打开] 启动浏览器...
start "" "http://localhost:8080/frontend/multi/index.html"

echo.
echo ========================================
echo 启动完成！
echo 后端: http://localhost:5000
echo 前端: http://localhost:8080
echo ========================================
pause
