@echo off
chcp 65001 >nul
echo ==========================================
echo    视频字幕处理器 - 启动脚本
echo ==========================================
echo.
echo 正在启动服务...
echo.

REM 获取脚本所在目录（项目根目录）
set BASE_DIR=%~dp0

echo 项目目录: %BASE_DIR%
echo.

REM 启动后端服务（在新窗口）
echo [1/2] 启动后端服务 (端口 5000)...
start "视频字幕处理器 - 后端" cmd /k "cd /d "%BASE_DIR%backend" && echo 正在启动后端服务... && python unified_app.py"

REM 等待2秒确保后端先启动
timeout /t 2 /nobreak >nul

REM 启动前端服务（从项目根目录启动，这样可以访问 uploads 和 outputs）
echo [2/2] 启动前端服务 (端口 8080)...
start "视频字幕处理器 - 前端" cmd /k "cd /d "%BASE_DIR%" && echo 正在启动前端服务... && python -m http.server 8080"

timeout /t 3 /nobreak >nul

echo.
echo ==========================================
echo  服务启动完成！
echo ==========================================
echo.
echo 后端地址: http://localhost:5000
echo 前端地址: http://localhost:8080/frontend/index.html
echo.
echo 请在浏览器中访问: http://localhost:8080/frontend/index.html
echo.
echo 关闭服务: 直接关闭两个黑色命令窗口即可
echo.
echo 注意: 如果之前打开过 http://localhost:8080，
echo       请改为访问 http://localhost:8080/frontend/index.html
echo.
pause
