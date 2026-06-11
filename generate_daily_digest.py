#!/usr/bin/env python3
"""
拾米通讯员 · 每日收盘摘要生成器
被 cron_daily_digest.sh 调用，只输出消息正文到 stdout
"""
import sys, os

# 阶段1: 导入期 — 吞掉所有 stdout 噪音
_real_stdout_fd = os.dup(1)
os.dup2(os.open(os.devnull, os.O_WRONLY), 1)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import datetime
from message_templates import format_daily_digest
from message_queue import enqueue

# 预导入所有可能产生噪音的模块
from data.fetcher import fetch_sentiment
from haitao.us_fetcher import get_indices
from services.alert import check_alerts

# 阶段1结束，恢复 stdout
os.dup2(_real_stdout_fd, 1)
os.close(_real_stdout_fd)

# 阶段2: 干活 — 此时 stdout 已干净
market_data = {"a_stock": {}, "us": {}, "alerts": []}

try:
    sentiment = fetch_sentiment()
    if sentiment and isinstance(sentiment, dict):
        market_data["a_stock"]["phase"] = sentiment.get("description", "?")
except Exception:
    pass

try:
    us_indices = get_indices()
    if us_indices:
        sp500 = next((i for i in us_indices if "S&P" in str(i.get("name", ""))), None)
        if sp500:
            market_data["us"]["sp500_change"] = sp500.get("change_pct", 0)
        vix = next((i for i in us_indices if "VIX" in str(i.get("name", ""))), None)
        if vix:
            market_data["us"]["vix"] = vix.get("price", "?")
except Exception:
    pass

try:
    triggered = check_alerts()
    market_data["alerts"] = triggered
except Exception:
    pass

digest = format_daily_digest(market_data)
enqueue("📋 每日收盘摘要", digest, priority="normal")
print(digest)
