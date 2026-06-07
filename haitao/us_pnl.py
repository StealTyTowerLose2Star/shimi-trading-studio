"""
HiTao 美股 - 盈亏追踪模块
职责: 计算美股交易的盈亏统计

架构: haitao/us_pnl.py → haitao/us_trade_db.py → db/core.py
"""
from datetime import datetime
from collections import defaultdict
from typing import Dict

from haitao.us_trade_db import get_us_trades


def calculate_pnl(period: str = "month") -> Dict:
    """计算美股交易盈亏

    Returns:
        {"period": str, "report": list, "summary": dict}
    """
    trades = get_us_trades()
    closed = [t for t in trades if t.get("exit_price")]

    buckets = defaultdict(lambda: {"trades": 0, "won": 0, "pnl": 0.0})

    for t in closed:
        exit_date = t.get("exit_date") or t.get("date", "")
        if not exit_date:
            continue

        if period == "month":
            key = exit_date[:7]
        elif period == "year":
            key = exit_date[:4]
        else:
            key = exit_date

        direction = t.get("direction", "buy")
        entry = float(t["entry_price"])
        exit_p = float(t["exit_price"])
        qty = float(t["qty"])

        if direction == "buy":
            pnl = (exit_p - entry) * qty
        elif direction == "sell":
            pnl = (entry - exit_p) * qty
        else:
            pnl = 0

        buckets[key]["trades"] += 1
        if pnl > 0:
            buckets[key]["won"] += 1
        buckets[key]["pnl"] += pnl

    report = []
    for k in sorted(buckets, reverse=True):
        b = buckets[k]
        report.append({
            "period": k,
            "trades": b["trades"],
            "won": b["won"],
            "win_rate": round(b["won"] / b["trades"] * 100, 1) if b["trades"] > 0 else 0,
            "pnl": round(b["pnl"], 2),
        })

    total_pnl = sum(b["pnl"] for b in buckets.values())
    total_trades = sum(b["trades"] for b in buckets.values())
    total_won = sum(b["won"] for b in buckets.values())

    return {
        "period": period,
        "report": report,
        "summary": {
            "total_trades": total_trades,
            "total_won": total_won,
            "win_rate": round(total_won / total_trades * 100, 1) if total_trades > 0 else 0,
            "total_pnl": round(total_pnl, 2),
            "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        },
    }
