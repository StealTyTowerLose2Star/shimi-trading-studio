#!/bin/bash
# 拾米交易工作室 - 服务启动包装脚本
# 加载 .env 中的 TUSHARE_TOKEN 再启动
set -euo pipefail
set -a
source /root/.hermes/.env 2>/dev/null || true
source /root/shimi-trading-studio/.env 2>/dev/null || true
set +a
cd /root/shimi-trading-studio
exec python3 backend.py
