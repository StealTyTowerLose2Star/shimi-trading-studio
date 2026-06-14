#!/bin/bash
# ───────────────────────────────────────────────
# 拾米交易工作室 · 通讯员 · 每日收盘摘要
# cron: 0 18 * * 1-5
# ───────────────────────────────────────────────

PROJECT_DIR="/root/shimi-trading-studio"
cd "$PROJECT_DIR"

python3 << 'PYEOF' 2>/dev/null | sed '/^[0-9].*| INFO.*logger/d; /^[0-9].*| WARNING/d'
import sys, os, json
sys.path.insert(0, '/root/shimi-trading-studio')

from dotenv import load_dotenv
load_dotenv('/root/shimi-trading-studio/.env')

from datetime import datetime
from message_templates import format_daily_digest

market_data = {"a_stock": {}, "us": {}, "alerts": [], "doubler": {}}

# ─── A股市场情绪 ───
try:
    from data.fetcher import fetch_sentiment
    s = fetch_sentiment()
    if s and isinstance(s, dict):
        market_data["a_stock"]["phase"] = s.get("phase", "?")
        market_data["a_stock"]["description"] = s.get("description", "")
        market_data["a_stock"]["total"] = s.get("total", 0)
        market_data["a_stock"]["up"] = s.get("up", 0)
        market_data["a_stock"]["down"] = s.get("down", 0)
        market_data["a_stock"]["limit_up"] = s.get("limit_up", 0)
        market_data["a_stock"]["limit_down"] = s.get("limit_down", 0)
        market_data["a_stock"]["volume_ratio"] = s.get("volume_ratio", 1.0)
        market_data["a_stock"]["position_ratio"] = s.get("position_ratio", 50)
except Exception:
    pass

# ─── 指数 ───
try:
    from data.fetcher_indices import fetch_indices
    indices = fetch_indices() or []
    market_data["a_stock"]["indices"] = indices
except Exception:
    pass

# ─── 翻倍股 ───
pick_file = os.path.join('/root/shimi-trading-studio', 'current_month_picks_v2.json')
if os.path.exists(pick_file):
    try:
        with open(pick_file) as f:
            picks = json.load(f)
        top = picks.get('top30', [])[:5]
        if top:
            market_data["doubler"]["top5"] = [
                {"code": p.get("code","?"), "name": p.get("name","?"),
                 "score": p.get("score",0),
                 "pattern": p.get("catalyst",{}).get("early_pattern","?")}
                for p in top
            ]
    except Exception:
        pass

# ─── 美股 (跳过 — 存在循环导入) ───
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

# ─── 告警 ───
try:
    from services.alert import check_alerts
    alerts = check_alerts() or []
    market_data["alerts"] = alerts
except Exception:
    pass

# 输出 → Hermes 推送到微信
digest = format_daily_digest(market_data)
print(digest)
PYEOF
