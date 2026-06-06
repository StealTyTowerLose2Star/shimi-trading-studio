"""
拾米交易工作室 — PnL 盈亏分析服务模块
从 backend.py 提取，保持向后兼容
"""
import json
from datetime import datetime, timedelta

from db import get_db


def get_kline(code, days=120):
    """获取个股K线数据"""
    from position_manager import get_kline as _get_kline
    return _get_kline(code, days=days)


def compute_pnl_report(user_id=None):
    """
    计算逐日PnL报告（60天滚动 + 月度汇总）
    从 backend.py api_pnl_report 提取
    """
    from position_manager import get_kline as _get_kline
    import pandas as pd
    import numpy as np

    conn = get_db()
    try:
        if user_id:
            trades = conn.execute(
                "SELECT * FROM trades WHERE user_id=? ORDER BY date",
                (user_id,)
            ).fetchall()
        else:
            trades = conn.execute(
                "SELECT * FROM trades ORDER BY date"
            ).fetchall()
        trades = [dict(t) for t in trades]
    finally:
        conn.close()

    if not trades:
        return {"error": "no trades"}

    today = datetime.now()
    start_date = today - timedelta(days=60)
    date_range = pd.date_range(start=start_date, end=today, freq='D')
    daily_pnl = {}
    monthly_pnl = {}

    for t in trades:
        entry_date = t.get("date", "")
        if not entry_date:
            continue
        try:
            entry_dt = datetime.strptime(entry_date, "%Y-%m-%d")
        except ValueError:
            continue

        try:
            kline = _get_kline(t["code"], days=120)
        except Exception:
            continue

        if kline is None or kline.empty:
            continue

        kline["trade_date_dt"] = pd.to_datetime(kline["trade_date"], format="%Y%m%d")
        kline = kline.sort_values("trade_date_dt")
        mask = kline["trade_date_dt"] >= entry_dt
        post_entry = kline[mask]

        if post_entry.empty:
            continue

        prev_close = post_entry["close"].iloc[0]
        for _, row in post_entry.iloc[1:].iterrows():
            day = row["trade_date_dt"].strftime("%Y-%m-%d")
            close = float(row["close"])
            # 当日盈亏
            day_pnl = (close - prev_close) * t.get("qty", 100)
            if t.get("direction") == "sell":
                day_pnl = -day_pnl
            daily_pnl[day] = daily_pnl.get(day, 0) + day_pnl
            # 月度汇总
            month = day[:7]
            monthly_pnl[month] = monthly_pnl.get(month, 0) + day_pnl
            prev_close = close

        # 已平仓处理
        if t.get("exit_price") and t.get("exit_date"):
            exit_date = t["exit_date"]
            if exit_date not in daily_pnl:
                # final PnL on exit day
                final_pnl = (t["exit_price"] - t["entry_price"]) * t.get("qty", 100)
                if t.get("direction") == "sell":
                    final_pnl = -final_pnl
                daily_pnl[exit_date] = daily_pnl.get(exit_date, 0) + final_pnl

    # 构建返回
    daily = [{"date": d.strftime("%Y-%m-%d"), "pnl": round(daily_pnl.get(d.strftime("%Y-%m-%d"), 0), 2)}
             for d in date_range if d.strftime("%Y-%m-%d") in daily_pnl]

    monthly = [{"month": m, "pnl": round(v, 2)}
               for m, v in sorted(monthly_pnl.items())]

    total = sum(d["pnl"] for d in daily)

    return {
        "daily": daily, "monthly": monthly,
        "total_pnl": round(total, 2),
        "days": len(daily),
        "generated_at": datetime.now().isoformat(),
    }
