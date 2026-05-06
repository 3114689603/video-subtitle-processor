@echo off
chcp 65001 >nul

:: 获取项目根目录
set "SCRIPT_DIR=%~dp0"
set "BASE_DIR=%SCRIPT_DIR%.."
cd /d "%BASE_DIR%"

title 视频字幕处理器 - 统一版
echo.
echo ========================================
echo   视频字幕处理器 - 统一版
echo   (单视频 + 多机位)
echo ========================================
echo.

:: 检查 Python
echo [1/4] 检查 Python...
py --version >nul 2>&1
if errorlevel 1 (
    echo   [错误] Python 未安装
    pause
    exit /b 1
)
echo   [OK] Python 已安装

:: 检查依赖
echo.
echo [2/4] 检查依赖...
py -c "import flask" 2>nul || py -m pip install flask flask-cors -q
py -c "import whisper" 2>nul || py -m pip install openai-whisper -q
py -c "import librosa" 2>nul || py -m pip install librosa scikit-learn soundfile -q
echo   [OK] 依赖已就绪

:: 清理端口
echo.
echo [3/4] 清理端口...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :5000') do taskkill /F /PID %%a >nul 2>&1
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8080') do taskkill /F /PID %%a >nul 2>&1
echo   [OK] 端口已清理

:: 启动服务
echo.
echo [4/4] 启动服务...
echo.

:: 启动统一后端
start "字幕处理器后端" cmd /k "cd /d %CD%\backend && py unified_app.py"

timeout /t 3 /nobreak >nul

:: 启动前端服务器
start "前端服务器" cmd /k "cd /d %CD% && py -m http.server 8080"

timeout /t 2 /nobreak >nul

:: 打开浏览器
echo   [打开] 启动浏览器...
start "" "http://localhost:8080"

echo.
echo ========================================
echo   启动完成！
echo ========================================
echo.
echo   访问地址: http://localhost:8080
echo.
echo   功能选择:
echo   - 单视频: 适合单个视频字幕识别
echo   - 多机位: 适合访谈多镜头同步
echo.
pause
