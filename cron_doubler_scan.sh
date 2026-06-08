#!/bin/bash
# 拾米交易工作室 · 魔法师 · 每日翻倍股扫描+预警推送
# cron: 每天 15:30 (收盘后) 自动执行
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
LOGDIR="$SCRIPT_DIR/logs"
mkdir -p "$LOGDIR"
LOGFILE="$LOGDIR/doubler_scan_$(date +%Y%m%d).log"

echo "[$(date '+%Y-%m-%d %H:%M')] 🔮 魔法师每日扫描开始" | tee -a "$LOGFILE"

python3 -c "
from dotenv import load_dotenv; load_dotenv()
import json, sys, os, time

try:
    from services.doubler_scanner import recommend_current_month
    t0 = time.time()
    result = recommend_current_month()
    elapsed = time.time() - t0

    if 'error' in result:
        print(f'❌ 扫描失败: {result[\"error\"]}', file=sys.stderr)
        sys.exit(1)

    top30 = result.get('top30', [])
    trade_date = result.get('trade_date', '?')

    # 保存结果
    with open('current_month_picks_v2.json', 'w') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # Top5 摘要
    lines = []
    for i, c in enumerate(top30[:5]):
        cat = c.get('catalyst', {})
        lines.append(f\"#{i+1} {c['code']} {c['name']} {c['score']}分 ({cat.get('early_pattern','?')})\")

    # 模式分布
    from collections import Counter
    patterns = Counter()
    for c in top30:
        p = c.get('catalyst', {}).get('early_pattern', 'none')
        patterns[p] += 1

    print(f'✅ 扫描完成 ({elapsed:.0f}s) | 交易日:{trade_date}')
    print(f'Top5: {\" | \".join(lines)}')
    print(f'模式: {\", \".join(f\"{p}={c}只\" for p,c in patterns.most_common(5))}')
except Exception as e:
    print(f'❌ 异常: {e}', file=sys.stderr)
    import traceback
    traceback.print_exc()
    sys.exit(1)
" 2>&1 | tee -a "$LOGFILE"

# 检查预警是否自动推送
python3 -c "
from services.alert import check_alerts
triggered = check_alerts(force=True)
doubler_trig = [t for t in triggered if 'doubler' in str(t.get('data',{}).get('strategy',''))]
print(f'🔔 预警检查: {len(triggered)}条触发, doubler={len(doubler_trig)}条')
" 2>&1 | tee -a "$LOGFILE"

echo "[$(date '+%Y-%m-%d %H:%M')] 🔮 魔法师每日扫描完成" | tee -a "$LOGFILE"
