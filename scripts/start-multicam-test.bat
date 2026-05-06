@echo off
chcp 65001 >nul
cd /d "%~dp0.."

echo ================================================
echo   多机位字幕拼接测试 - 启动
echo ================================================
echo.

:: 检查端口
netstat -ano | findstr ":5000" >nul
if not errorlevel 1 (
    echo [警告] 端口 5000 已被占用，正在关闭...
    for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5000" ^| findstr "LISTENING"') do (
        taskkill /F /PID %%a >nul 2>&1
    )
    timeout /t 1 /nobreak >nul
)

echo [启动] 后端服务（包含前端页面）...
start "多机位后端" cmd /k "cd /d %CD%\backend && echo 后端启动中... && python multicam_concat.py"

echo [等待] 服务启动中...
timeout /t 4 /nobreak >nul

:: 打开浏览器
echo [打开] 浏览器...
start http://localhost:5000

echo.
echo ================================================
echo   启动完成！
echo   访问地址: http://localhost:5000
echo.
echo   关闭方式: 关闭CMD窗口或 taskkill /F /IM python.exe
echo ================================================
echo.
pause