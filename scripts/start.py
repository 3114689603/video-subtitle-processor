#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Video Subtitle Processor - Quick Start Script
一键启动 API 服务器
"""

import subprocess
import sys
import os


def main():
    print("=" * 50)
    print("Video Subtitle Processor - API Server")
    print("=" * 50)
    print()

    # 检查依赖
    print("[1/2] 检查依赖...")
    try:
        import flask

        print("  ✓ Flask 已安装")
    except ImportError:
        print("  → 正在安装 Flask...")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "flask", "flask-cors", "-q"]
        )
        print("  ✓ 安装完成")

    print()
    print("[2/2] 启动 API 服务...")
    print("=" * 50)
    print()
    print("服务地址: http://localhost:5000")
    print("健康检查: http://localhost:5000/api/health")
    print()
    print("请保持此窗口运行！")
    print("按 Ctrl+C 停止服务")
    print("=" * 50)
    print()

    # 启动 Flask
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    subprocess.call([sys.executable, "app.py"])


if __name__ == "__main__":
    main()
