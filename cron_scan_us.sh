#!/bin/bash
# 海淘掘金 — 美股全市场每日扫描
# 北京时间 20:00 (美东 8:00AM) 开盘前执行
# 每次扫600只，约7分钟，累计覆盖全市场

set -a
source /root/.hermes/.env
set +a

cd /root/shimi-trading-studio
source .venv/bin/activate

echo "[$(date '+%Y-%m-%d %H:%M')] 海淘全市场扫描开始..."
python3 -c "
import os, sys
os.environ['FINNHUB_KEY'] = os.environ.get('FINNHUB_KEY', '')
sys.path.insert(0, '/root/shimi-trading-studio')
from haitao.us_screener import full_scan
result = full_scan(max_batches=2)
if result:
    picks = len(result.get('results', []))
    golds = len(result.get('gold_picks', []))
    print(f'扫描完成: {picks}只候选, {golds}只金矿')
"
echo "[$(date '+%Y-%m-%d %H:%M')] 扫描结束"
