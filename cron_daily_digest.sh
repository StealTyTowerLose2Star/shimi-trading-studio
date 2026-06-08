#!/bin/bash
# ───────────────────────────────────────────────
# 拾米交易工作室 · 通讯员 · 每日收盘摘要
# cron: 0 18 * * 1-5 /root/shimi-trading-studio/cron_daily_digest.sh
# ───────────────────────────────────────────────
set -euo pipefail

# Hermes cron 从 /root/.hermes/scripts/ 执行本脚本副本，必须硬编码路径
PROJECT_DIR="/root/shimi-trading-studio"
cd "$PROJECT_DIR"

echo "[$(date '+%Y-%m-%d %H:%M')] 📡 通讯员 · 生成每日收盘摘要"

# 收集市场数据并生成摘要
python3 << 'PYEOF'
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import datetime
from message_templates import format_daily_digest
from message_queue import enqueue

try:
    market_data = {"a_stock": {}, "us": {}, "alerts": []}

    # A股市场数据
    try:
        from data.fetcher import get_latest_date, fetch_sentiment
        sentiment = fetch_sentiment()
        if sentiment and isinstance(sentiment, dict):
            market_data["a_stock"]["phase"] = sentiment.get("description", "?")
    except Exception:
        pass

    # 美股数据
    try:
        from haitao.us_fetcher import get_indices
        us_indices = get_indices()
        if us_indices:
            sp500 = next((i for i in us_indices if "S&P" in str(i.get("name",""))), None)
            if sp500:
                market_data["us"]["sp500_change"] = sp500.get("change_pct", 0)
            vix = next((i for i in us_indices if "VIX" in str(i.get("name",""))), None)
            if vix:
                market_data["us"]["vix"] = vix.get("price", "?")
    except Exception:
        pass

    # 告警检查
    try:
        from services.alert import check_alerts
        triggered = check_alerts()
        market_data["alerts"] = triggered
    except Exception:
        pass

    # 格式化并发送
    digest = format_daily_digest(market_data)
    enqueue("📋 每日收盘摘要", digest, priority="normal")
    # 输出到 stdout — hermes cron --deliver weixin 会推送到微信
    print("📋 每日收盘摘要")
    print(digest)

except Exception as e:
    print(f"❌ 摘要生成失败: {e}")
    sys.exit(1)
PYEOF

echo "[$(date '+%Y-%m-%d %H:%M')] ✅ 完成"
