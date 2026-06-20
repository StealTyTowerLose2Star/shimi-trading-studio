#!/bin/bash
# ───────────────────────────────────────────────
# 拾米交易工作室 · 通讯员 · 每日收盘摘要
# cron: 0 18 * * 1-5
# ───────────────────────────────────────────────

PROJECT_DIR="/root/shimi-trading-studio"
cd "$PROJECT_DIR"

python3 << 'PYEOF' 2>/dev/null | sed '/^[0-9].*| INFO.*logger/d; /^[0-9].*| WARNING/d'
import sys, os, json, sqlite3
sys.path.insert(0, '/root/shimi-trading-studio')

from dotenv import load_dotenv
load_dotenv('/root/shimi-trading-studio/.env')

from datetime import datetime
from message_templates import format_daily_digest
from message_queue import enqueue

market_data = {"a_stock": {}, "us": {}, "doubler": {}, "account": {}, "plans": [], "sectors": []}

# ─── 市场情绪 ───
try:
    from data.fetcher import fetch_sentiment
    s = fetch_sentiment()
    if s and isinstance(s, dict):
        for k in ('phase','total','up','down','limit_up','limit_down','volume_ratio','position_ratio'):
            market_data["a_stock"][k] = s.get(k, 0)
except Exception:
    pass

# ─── 指数 ───
try:
    from data.fetcher_indices import fetch_indices
    market_data["a_stock"]["indices"] = fetch_indices() or []
except Exception:
    pass

# ─── 热门板块 ───
try:
    from data.fetcher_indices import fetch_sector_flow
    flow = fetch_sector_flow() or []
    market_data["sectors"] = flow[:3]
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
                 "pattern": p.get("catalyst",{}).get("early_pattern","")}
                for p in top
            ]
    except Exception:
        pass

# ─── 交易日志 ───
try:
    conn = sqlite3.connect('shimi.db')
    trades = conn.execute(
        "SELECT date,code,name,direction,entry_price,qty,exit_price,stop_loss,target_1,target_2,target_3,note "
        "FROM trades WHERE user_id = 3 ORDER BY created_at DESC LIMIT 5"
    ).fetchall()
    market_data["trades"] = [
        {"date": t[0], "code": t[1], "name": t[2], "direction": t[3],
         "entry_price": t[4], "qty": t[5], "exit_price": t[6],
         "stop_loss": t[7], "tp1": t[8], "tp2": t[9], "tp3": t[10], "note": t[11]}
        for t in trades
    ]
    conn.close()
except Exception:
    pass

# ─── 市场事件 (收集后并入日报) ───
try:
    events_raw = []
    ep = os.path.join('/root/shimi-trading-studio', 'data', 'market_events.json')
    if os.path.exists(ep):
        with open(ep) as f:
            events_raw = json.load(f)
        events_raw = events_raw if isinstance(events_raw, list) else events_raw.get("events", [])
    kw = ["中标","重组","重大","ST","退市","停牌","复牌","减持","增持","回购","业绩预告","立案"]
    market_data["events"] = [
        e for e in events_raw
        if e.get("impact") == "high" or any(k in e.get("title","") for k in kw)
    ][:5]
except Exception:
    pass

# ─── 美股 ───
try:
    from haitao.us_fetcher import get_indices
    us_indices = get_indices()
    if us_indices:
        sp = next((i for i in us_indices if "S&P" in str(i.get("name",""))), None)
        if sp:
            market_data["us"]["sp500_change"] = sp.get("change_pct", 0)
except Exception:
    pass

# 入队 → Server酱推送
digest = format_daily_digest(market_data)
if digest:
    enqueue(f"📊 拾米日报 {datetime.now().strftime('%m-%d')}", digest, priority="normal")
    print(f"[daily_digest] ✅ 已入队 ({len(digest)}字符)")
else:
    print("[daily_digest] ⚠️ 无数据，跳过")
PYEOF
