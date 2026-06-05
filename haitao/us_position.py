"""海淘美股 - 持仓管理引擎
对齐拾米 position_manager.py 风格：ATR浮动止盈 + 动态止损
"""
import logging
from typing import Dict, List, Optional
from datetime import datetime

import numpy as np
import pandas as pd

from haitao.us_fetcher import get_history, calc_technical_indicators

logger = logging.getLogger(__name__)


def evaluate_us_position(
    ticker: str,
    entry_price: float,
    direction: str = "buy",
    qty: int = 100,
    entry_date: str = None,
) -> dict:
    """评估单个美股持仓，返回动态止损/止盈建议

    对齐拾米 position_manager.evaluate_position():
    - ATR 浮动止盈阶梯 T1/T2/T3
    - 动态止损随价格上移
    - 阶段判定 + 操作建议

    Args:
        ticker: 股票代码 (如 "AAPL")
        entry_price: 入场价
        direction: "buy" 做多 / "sell" 做空
        qty: 持仓数量
        entry_date: 入场日期 YYYY-MM-DD

    Returns:
        dict: 含当前价、ATR、止损/止盈、浮盈、建议
    """
    df = get_history(ticker, period="6mo")
    if df is None or len(df) < 15:
        return {"error": "数据不足", "ticker": ticker}

    tech = calc_technical_indicators(df)
    close = df["Close"].values.astype(float)
    price = float(close[-1])
    atr = tech.get("atr14", 0) or 0

    if atr <= 0:
        # Fallback: calc from raw
        high = df["High"].values.astype(float) if "High" in df.columns else close
        low = df["Low"].values.astype(float) if "Low" in df.columns else close
        prev_close = close[:-1]
        tr = np.maximum(
            high[1:] - low[1:],
            np.maximum(np.abs(high[1:] - prev_close), np.abs(low[1:] - prev_close))
        )
        atr = float(np.mean(tr[-14:])) if len(tr) >= 14 else 0

    if atr <= 0:
        atr = price * 0.02  # Fallback: 2% of price

    result = {
        "ticker": ticker,
        "entry_price": round(entry_price, 2),
        "current_price": round(price, 2),
        "direction": direction,
        "atr": round(atr, 2),
        "qty": qty,
        "entry_date": entry_date or datetime.now().strftime("%Y-%m-%d"),
    }

    # === 做多 ===
    if direction == "buy":
        T1 = entry_price + 2.0 * atr
        T2 = entry_price + 3.5 * atr
        T3 = entry_price + 6.0 * atr

        # 浮动止损
        if price >= T3:
            current_sl = entry_price + 3.0 * atr
            trailing_level = 3
            sl_label = f"${round(current_sl,2)} 锁定+{round((current_sl-entry_price)/entry_price*100,1)}%"
        elif price >= T2:
            current_sl = entry_price + 1.5 * atr
            trailing_level = 2
            sl_label = f"${round(current_sl,2)} 锁定+{round((current_sl-entry_price)/entry_price*100,1)}%"
        elif price >= T1:
            current_sl = entry_price * 1.002
            trailing_level = 1
            sl_label = f"${round(current_sl,2)} 保本"
        else:
            current_sl = entry_price - 1.5 * atr
            trailing_level = 0
            sl_label = f"${round(current_sl,2)} (-{round((entry_price-current_sl)/entry_price*100,1)}%)"

        current_sl = round(current_sl, 2)
        unrealized = price - entry_price
        unrealized_pct = (price - entry_price) / entry_price * 100

        targets = [
            {"level": 1, "price": round(T1, 2),
             "gain": f"+{round((T1-entry_price)/entry_price*100,1)}%",
             "reached": price >= T1},
            {"level": 2, "price": round(T2, 2),
             "gain": f"+{round((T2-entry_price)/entry_price*100,1)}%",
             "reached": price >= T2},
            {"level": 3, "price": round(T3, 2),
             "gain": f"+{round((T3-entry_price)/entry_price*100,1)}%",
             "reached": price >= T3},
        ]

    # === 做空 ===
    else:
        T1 = entry_price - 2.0 * atr
        T2 = entry_price - 3.5 * atr
        T3 = entry_price - 6.0 * atr

        if price <= T3:
            current_sl = entry_price - 3.0 * atr
            trailing_level = 3
            sl_label = f"${round(current_sl,2)} 锁定"
        elif price <= T2:
            current_sl = entry_price - 1.5 * atr
            trailing_level = 2
            sl_label = f"${round(current_sl,2)} 锁定部分利润"
        elif price <= T1:
            current_sl = entry_price * 0.998
            trailing_level = 1
            sl_label = "保本"
        else:
            current_sl = entry_price + 1.5 * atr
            trailing_level = 0
            sl_label = f"${round(current_sl,2)} (+{round((current_sl-entry_price)/entry_price*100,1)}%)"

        current_sl = round(current_sl, 2)
        unrealized = entry_price - price
        unrealized_pct = (entry_price - price) / entry_price * 100

        targets = [
            {"level": 1, "price": round(T1, 2),
             "gain": f"{round((T1-entry_price)/entry_price*100,1)}%",
             "reached": price <= T1},
            {"level": 2, "price": round(T2, 2),
             "gain": f"{round((T2-entry_price)/entry_price*100,1)}%",
             "reached": price <= T2},
            {"level": 3, "price": round(T3, 2),
             "gain": f"{round((T3-entry_price)/entry_price*100,1)}%",
             "reached": price <= T3},
        ]

    distance_to_sl = (price - current_sl) / price * 100 if price > 0 else 0
    if direction == "sell":
        distance_to_sl = (current_sl - price) / price * 100 if price > 0 else 0

    # 操作建议
    advice = _generate_advice(trailing_level, unrealized_pct, price, current_sl, direction)

    result.update({
        "current_stop_loss": current_sl,
        "stop_loss_label": sl_label,
        "targets": targets,
        "trailing_level": trailing_level,
        "unrealized_pnl": round(unrealized * qty, 2),
        "unrealized_pnl_pct": round(unrealized_pct, 1),
        "unrealized_pnl_per_share": round(unrealized, 2),
        "distance_to_sl_pct": round(distance_to_sl, 1),
        "is_stopped_out": (price < current_sl if direction == "buy" else price > current_sl),
        "advice": advice,
    })

    return result


def _generate_advice(
    trailing_level: int,
    unrealized_pct: float,
    price: float,
    stop_loss: float,
    direction: str,
) -> str:
    """生成操作建议"""
    if direction == "buy":
        if price < stop_loss:
            return "🚨 已触发止损，建议平仓"
        if trailing_level >= 3:
            return "✅ 持有，浮动止盈已大幅上移"
        if trailing_level >= 2:
            return "✅ 持有，利润已锁定"
        if trailing_level >= 1:
            return "✅ 持有，已保本"
        if unrealized_pct > 10:
            return "✅ 持有，浮盈可观"
        if unrealized_pct > 5:
            return "✅ 持有中"
        if unrealized_pct > 0:
            return "✅ 微利持有"
        if unrealized_pct > -3:
            return "⚠️ 小幅浮亏，观察"
        if unrealized_pct > -8:
            return "⚠️ 浮亏中，关注止损位"
        return "🔴 深度浮亏，建议止损"
    else:
        if price > stop_loss:
            return "🚨 已触发止损，建议平仓"
        if trailing_level >= 3:
            return "✅ 持有，空头利润已锁定"
        if trailing_level >= 1:
            return "✅ 持有，已保本"
        if unrealized_pct > 10:
            return "✅ 持有，空头浮盈可观"
        return "✅ 持有中"


def batch_evaluate_us(positions: List[dict]) -> List[dict]:
    """批量评估美股持仓

    Args:
        positions: [{"ticker": "AAPL", "entry_price": 180, "direction": "buy", "qty": 100}]
    """
    results = []
    for pos in positions:
        r = evaluate_us_position(
            pos.get("ticker", ""),
            pos.get("entry_price", 0),
            pos.get("direction", "buy"),
            pos.get("qty", 100),
            pos.get("entry_date"),
        )
        # 注入原始数据
        r["name"] = pos.get("name", "")
        r["note"] = pos.get("note", "")
        results.append(r)
    return results
