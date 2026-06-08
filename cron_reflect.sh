#!/bin/bash
# Hermes cron 从 /root/.hermes/scripts/ 执行本脚本副本，必须硬编码项目路径
PROJECT_DIR="/root/shimi-trading-studio"
cd "$PROJECT_DIR"
python3 daily_reflect.py "$@" 2>&1 | tee -a "$PROJECT_DIR/reflect.log"
