#!/bin/bash
# 🧼 舒肤佳安全守卫 — Cron 调度包装脚本
# 每日定时运行安全巡检并保存报告
# Hermes cron 从 /root/.hermes/scripts/ 执行本脚本，必须硬编码路径
set -e

PROJECT_DIR="/root/shimi-trading-studio"
cd "$PROJECT_DIR"

TIMESTAMP=$(date '+%Y%m%d-%H%M%S')
REPORT_DIR="/root/.hermes/cache/security"
mkdir -p "$REPORT_DIR"
REPORT_FILE="$REPORT_DIR/report-${TIMESTAMP}.json"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 🧼 舒肤佳安全守卫启动..."

# 运行完整扫描
python3 hermes_security_guardian.py --output "$REPORT_FILE" 2>&1

EXIT_CODE=$?

# 如果发现高危，触发告警
if [ $EXIT_CODE -ge 2 ]; then
    SCORE=$(python3 -c "import json; d=json.load(open('$REPORT_FILE')); print(d.get('overall_score','?')); print(d.get('overall_grade','?')); print(d.get('summary',{}).get('high_severity',0))" 2>/dev/null)
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ⚠️ 发现高危漏洞！评分: ${SCORE}"
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] ✅ 安全巡检完成，报告: $REPORT_FILE"
exit $EXIT_CODE
