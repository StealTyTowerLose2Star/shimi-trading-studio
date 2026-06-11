#!/bin/bash
# 拾米交易工作室 - 复盘定时发送
# Hermes cron 从 /root/.hermes/scripts/ 执行本脚本副本，必须硬编码项目路径

PROJECT_DIR="/root/shimi-trading-studio"
cd "$PROJECT_DIR"
python3 review_sender.py "$1" 2>/dev/null | sed '/^[0-9].*| INFO.*logger/d; /^[0-9].*| WARNING/d'
