#!/bin/bash
# 双击本文件：开一个可见的终端窗口跑管理页，日志实时打印在这里，关掉窗口即停止。
cd "$(dirname "$0")" || exit 1
env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy -u ALL_PROXY -u all_proxy \
    ./.venv/bin/python manage.py
