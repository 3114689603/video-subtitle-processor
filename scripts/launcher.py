#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
视频字幕处理器 - 智能启动器
一键启动前后端服务
"""

import subprocess
import sys
import os
import time
import socket
import webbrowser
from urllib.request import urlopen
from urllib.error import URLError

# 颜色代码
GREEN = '\033[92m'
YELLOW = '\033[93m'
RED = '\033[91m'
BLUE = '\033[94m'
RESET = '\033[0m'

def print_banner():
    print(f"{BLUE}")
    print("=" * 50)
    print("  视频字幕处理器 - 智能启动器")
    print("=" * 50)
    print(f"{RESET}")

def check_port(port, host='127.0.0.1'):
    """检查端口是否被占用"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except:
        return False

def kill_port(port):
    """关闭占用指定端口的进程"""
    try:
        result = subprocess.run(
            f'netstat -ano | findstr :{port}',
            shell=True, capture_output=True, text=True
        )
        if result.stdout:
            for line in result.stdout.strip().split('\n'):
                parts = line.split()
                if len(parts) >= 5:
                    pid = parts[-1]
                    subprocess.run(f'taskkill /F /PID {pid}', shell=True, capture_output=True)
            time.sleep(1)
            return True
    except:
        pass
    return False

def wait_for_service(url, timeout=30):
    """等待服务启动"""
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            response = urlopen(url, timeout=2)
            return True
        except URLError:
            time.sleep(0.5)
    return False

def main():
    print_banner()
    
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    
    # 检查 Python
    print(f"{YELLOW}[1/5]{RESET} 检查 Python...")
    try:
        subprocess.run([sys.executable, '--version'], check=True, capture_output=True)
        print(f"  {GREEN}[OK]{RESET} Python 已安装")
    except:
        print(f"  {RED}[X]{RESET} Python 未安装！")
        input("按回车键退出...")
        return
    
    # 检查端口
    print(f"\n{YELLOW}[2/5]{RESET} 检查端口...")
    if check_port(5000):
        print(f"  {YELLOW}[!]{RESET} 端口 5000 被占用，尝试释放...")
        kill_port(5000)
    if check_port(8080):
        print(f"  {YELLOW}[!]{RESET} 端口 8080 被占用，尝试释放...")
        kill_port(8080)
    print(f"  {GREEN}[OK]{RESET} 端口检查完成")
    
    # 检查依赖
    print(f"\n{YELLOW}[3/5]{RESET} 检查依赖...")
    try:
        import flask
        import flask_cors
        print(f"  {GREEN}[OK]{RESET} 依赖已安装")
    except ImportError:
        print(f"  {YELLOW}[→]{RESET} 正在安装依赖...")
        subprocess.run([sys.executable, '-m', 'pip', 'install', 'flask', 'flask-cors', '-q'])
        print(f"  {GREEN}[OK]{RESET} 安装完成")
    
    # 启动后端
    print(f"\n{YELLOW}[4/5]{RESET} 启动后端 API (端口: 5000)...")
    backend_cmd = f'start "后端API - 视频字幕处理器" cmd /k "{sys.executable} app.py"'
    subprocess.run(backend_cmd, shell=True)
    
    # 等待后端启动
    print(f"  等待后端启动...", end='', flush=True)
    if wait_for_service('http://localhost:5000/api/health', timeout=30):
        print(f" {GREEN}[OK]{RESET}")
    else:
        print(f" {YELLOW}[!]{RESET} 启动较慢，继续等待...")
    
    # 启动前端
    print(f"\n{YELLOW}[5/5]{RESET} 启动前端服务器 (端口: 8080)...")
    frontend_cmd = f'start "前端服务器 - 视频字幕处理器" cmd /k "{sys.executable} -m http.server 8080"'
    subprocess.run(frontend_cmd, shell=True)
    time.sleep(2)
    
    # 打开浏览器
    print(f"\n{GREEN}正在打开浏览器...{RESET}")
    webbrowser.open('http://localhost:8080')
    
    print(f"\n{GREEN}{'='*50}{RESET}")
    print(f"{GREEN}  启动完成！{RESET}")
    print(f"{GREEN}{'='*50}{RESET}")
    print(f"\n  前端页面: http://localhost:8080")
    print(f"  API地址: http://localhost:5000")
    print(f"\n  {BLUE}[提示]{RESET} 如果页面显示 \"API已连接\"，说明成功了！")
    print(f"\n  关闭方式:")
    print(f"  - 关闭弹出的两个 CMD 窗口")
    print(f"  - 或运行 stop.bat")
    
    input(f"\n{YELLOW}按回车键退出此窗口...{RESET}")

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n\n{YELLOW}已取消启动{RESET}")
    except Exception as e:
        print(f"\n{RED}发生错误: {e}{RESET}")
        input("按回车键退出...")
