#!/bin/bash
# ═══════════════════════════════════════════════
# 观星台 · 市场事件定时扫描
# 频率: 每30分钟 (crontab: */30 * * * *)
# 输出: 刷新缓存 → 前端面板 + 高影响事件推送消息队列
# ═══════════════════════════════════════════════
set -euo pipefail

PROJECT_DIR="/root/shimi-trading-studio"
cd "$PROJECT_DIR"

echo "[观星台] $(date '+%Y-%m-%d %H:%M') 开始市场事件扫描..."

python3 -c "
import sys, json, os
sys.path.insert(0, '$PROJECT_DIR')
from data.market_events import scan_market_events

result = scan_market_events()
s = result['summary']

# 写入缓存
cache_path = os.path.join('$PROJECT_DIR', 'data', 'market_events.json')
os.makedirs(os.path.dirname(cache_path), exist_ok=True)
with open(cache_path, 'w') as f:
    json.dump(result, f, ensure_ascii=False, indent=1)

print(f'扫描完成: {s[\"total_events\"]}条事件 → {len(result[\"signals\"])}条信号')
print(f'做多: {s[\"long_signals\"]} | 做空: {s[\"short_signals\"]}')

# 打印前5条信号
for sig in result['signals'][:5]:
    evt = sig['event']
    sts = sig['stocks']
    codes = ','.join(s['code'] for s in sts[:3])
    impact = '⚠' if evt.get('impact') == 'high' else ''
    print(f'  [{evt[\"type\"]}]{impact} {evt[\"title\"][:60]} → {codes}')
"

echo "[观星台] $(date '+%Y-%m-%d %H:%M') 扫描完成"
