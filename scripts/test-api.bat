@echo off
chcp 65001 >nul
echo ========================================
echo   API 连接测试
echo ========================================
echo.

echo [测试] 检查 http://localhost:5000/api/health
echo.

curl -s http://localhost:5000/api/health >nul 2>&1
if errorlevel 1 (
    echo   [X] API 未启动或无法连接
    echo.
    echo   解决方法：
    echo   1. 先运行 start-multicamera.bat 启动服务
    echo   2. 检查 CMD 窗口是否有报错
    echo   3. 等待 5-10 秒让服务完全启动
) else (
    echo   [OK] API 连接成功！
    echo.
    curl -s http://localhost:5000/api/health | python -m json.tool 2>nul || curl -s http://localhost:5000/api/health
)

echo.
pause
