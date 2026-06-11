#!/bin/bash
# ═══════════════════════════════════════════════
# 先知 · 事件扫描定时任务
# 频率: 每30分钟 (crontab: */30 * * * *)
# 输出: 通过微信推送到拾米交易工作室
# ═══════════════════════════════════════════════
set -euo pipefail

PROJECT_DIR="/root/shimi-trading-studio"
cd "$PROJECT_DIR"

# ─── 运行扫描 ───
echo "[先知] $(date '+%Y-%m-%d %H:%M') 开始事件扫描..."
python3 -c "
import sys, json
sys.path.insert(0, '$PROJECT_DIR')
from ml.event_predictor import scan_and_predict, save_signals

result = scan_and_predict(pages=3)
save_signals(result)

sig_count = result['summary']['total_signals']
long_count = result['summary']['long_count']
short_count = result['summary']['short_count']
high_count = result['summary']['high_confidence']

print(f'扫描完成: {result[\"total_events\"]}条事件 → {sig_count}条信号')
print(f'做多: {long_count} | 做空: {short_count} | 高置信: {high_count}')

# 输出高价值信号摘要
if result.get('deep_dives'):
    for d in result['deep_dives'][:5]:
        direction = '📈' if d['direction'] in ('long', 'short+long') else '📉'
        print(f'{direction} {d[\"signal\"]}: {d[\"title\"][:80]}')
        print(f'  标的: {\", \".join(d[\"stocks\"])}')
        print(f'  持续: {d[\"duration\"]}')
"
echo "[先知] $(date '+%Y-%m-%d %H:%M') 扫描完成"
