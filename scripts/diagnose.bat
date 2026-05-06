@echo off
chcp 65001 >nul
title 视频字幕处理器 - 诊断工具
echo.
echo ==========================================
echo   视频字幕处理器 - 诊断工具
echo ==========================================
echo.
cd /d "%~dp0"

echo [1/6] 检查 Python...
python --version 2>nul
if errorlevel 1 (
    echo   [X] Python 未安装！
    echo       请访问: https://www.python.org/downloads/
) else (
    echo   [OK] Python 已安装
)
echo.

echo [2/6] 检查关键文件...
if exist "app.py" (echo   [OK] app.py) else (echo   [X] 缺少 app.py)
if exist "app.js" (echo   [OK] app.js) else (echo   [X] 缺少 app.js)
if exist "index.html" (echo   [OK] index.html) else (echo   [X] 缺少 index.html)
echo.

echo [3/6] 检查 Python 依赖...
python -c "import flask" 2>nul
if errorlevel 1 (echo   [X] Flask 未安装) else (echo   [OK] Flask)

python -c "import flask_cors" 2>nul  
if errorlevel 1 (echo   [X] flask-cors 未安装) else (echo   [OK] flask-cors)
echo.

echo [4/6] 检查端口占用...
netstat -ano | findstr ":5000" >nul
if errorlevel 1 (echo   [OK] 端口 5000 空闲) else (echo   [!] 端口 5000 被占用)

netstat -ano | findstr ":8080" >nul
if errorlevel 1 (echo   [OK] 端口 8080 空闲) else (echo   [!] 端口 8080 被占用)
echo.

echo [5/6] 测试 API 服务...
curl -s http://localhost:5000/api/health >nul 2>&1
if errorlevel 1 (
    echo   [X] API 服务未运行
    echo       请先运行 start-all.bat 启动服务
) else (
    echo   [OK] API 服务运行正常
    curl -s http://localhost:5000/api/health
)
echo.

echo [6/6] 检查浏览器打开方式...
echo   [提示] 正确的打开方式：
echo         1. 运行 start-all.bat 启动服务
echo         2. 访问 http://localhost:8080
echo         3. 不要双击打开 index.html！
echo.

echo ==========================================
echo   诊断完成！
echo ==========================================
echo.
echo 常见问题：
echo 1. 如果显示"API未连接"，请确保：
echo    - 运行了 start-all.bat（不是双击HTML）
echo    - 访问的是 http://localhost:8080
echo.
echo 2. 如果端口被占用：
echo    - 运行 stop.bat 关闭旧服务
echo    - 或重启电脑后再试
echo.
pause
