#!/bin/bash
# 拾米交易工作室 - 复盘定时发送包装脚本
# 被 cron 调用，先激活 venv 再执行
# Hermes cron 从 /root/.hermes/scripts/ 执行本脚本副本，必须硬编码项目路径
PROJECT_DIR="/root/shimi-trading-studio"
cd "$PROJECT_DIR"
python3 review_sender.py "$1" 2>> "$PROJECT_DIR/review_sender.log"
