#!/bin/bash
# ═══════════════════════════════════════════════
# 观星台 · yfinance 财报日历每日刷新
# 频率: 每天 09:00 (crontab: 0 9 * * *)
# yfinance Tickers批量调用较慢 (~90s), 不适合30分钟周期
# ═══════════════════════════════════════════════
set -euo pipefail

PROJECT_DIR="/root/shimi-trading-studio"
cd "$PROJECT_DIR"

echo "[观星台·yfinance] $(date '+%Y-%m-%d %H:%M') 开始财报日历扫描..."

python3 -c "
import sys, json, os
sys.path.insert(0, '$PROJECT_DIR')
from dotenv import load_dotenv; load_dotenv()
from data.market_events import fetch_yfinance_earnings, map_events_to_stocks

# 只抓yfinance
yf_events = fetch_yfinance_earnings()

# 读取现有缓存
cache_path = os.path.join('$PROJECT_DIR', 'data', 'market_events.json')
existing = {}
if os.path.exists(cache_path):
    with open(cache_path) as f:
        existing = json.load(f)

# 去重合并: 保留非yfinance信号，替换yfinance信号
old_signals = existing.get('signals', [])
non_yf = [s for s in old_signals if s['event'].get('source') != 'yfinance']

# 为yfinance事件生成信号
yf_signals_all = map_events_to_stocks(yf_events)
for sig in yf_signals_all:
    sig['event']['market'] = 'US'
    sig['event']['source'] = 'yfinance'
yf_display = yf_signals_all[:4]

# 合并: CN8 + EM4 + US4 + YF4
cn_s = [s for s in non_yf if s['event'].get('source') in (None,'','cninfo')]
em_s = [s for s in non_yf if s['event'].get('source') == 'eastmoney']
us_s = [s for s in non_yf if s['event'].get('market') == 'US' and s['event'].get('source') != 'yfinance']
new_signals = cn_s[:8] + em_s[:4] + us_s[:4] + yf_display

# 更新total（不重复计算）
old_total = existing.get('summary',{}).get('total_events',0)
new_summary = existing.get('summary',{})
new_summary['affected_stocks'] = len(new_signals)

with open(cache_path, 'w') as f:
    json.dump({**existing, 'signals': new_signals, 'summary': new_summary,
               'sources_status': {**existing.get('sources_status',{}),
                                  'yfinance': {'ok': True, 'count': len(yf_events)}}}, f,
              ensure_ascii=False, indent=1)

print(f'yfinance: {len(yf_events)}条 → 信号{len(yf_display)}条 → 缓存更新完成')
"

echo "[观星台·yfinance] $(date '+%Y-%m-%d %H:%M') 完成"
