#!/bin/bash
# ───────────────────────────────────────────────
# 拾米交易工作室 · 哨兵 · 告警检查脚本
# cron: */5 * * * * /root/shimi-trading-studio/cron_alert_check.sh
# ───────────────────────────────────────────────
set -euo pipefail

# Hermes cron 从 /root/.hermes/scripts/ 执行本脚本副本，必须硬编码项目路径
PROJECT_DIR="/root/shimi-trading-studio"
cd "$PROJECT_DIR"

python3 -c "
from services.alert import check_alerts
result = check_alerts()
if result:
    print(f'[alert] {len(result)} alerts triggered')
    for r in result:
        print(f'  [{r[\"type\"]}] {r[\"message\"]}')
" 2>/dev/null || true
