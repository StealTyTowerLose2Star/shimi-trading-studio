#!/bin/bash
# 拾米交易工作室 - 服务启动包装脚本
# 加载 .env 中的 TUSHARE_TOKEN 再启动
set -a
source /root/.hermes/.env
set +a
cd /root/shi-mi-dashboard
source venv/bin/activate
exec python3 backend.py
