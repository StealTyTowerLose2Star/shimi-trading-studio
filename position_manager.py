"""
拾米交易工作室 - 持仓管理引擎
动态调整每笔交易的止损位、目标位、浮动止盈
"""
import pandas as pd
import numpy as np
from typing import Dict, List, Optional

import sys, os
sys.path.insert(0, os.path.dirname(__file__))
import config
from data.fetcher_core import get_ts




def get_kline(code: str, days: int = 60, force: bool = False):
    """获取个股 K 线"""
    try:
        pro = get_ts()
        ts_code = code if code.endswith((".SZ", ".SH", ".BJ")) else (
            f"{code}.SZ" if code.startswith(("0", "3")) else
            f"{code}.SH" if code.startswith(("6")) else
            f"{code}.BJ"
        )
        from datetime import datetime, timedelta
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=days + 30)).strftime("%Y%m%d")
        df = pro.daily(ts_code=ts_code, start_date=start, end_date=end,
                       fields="trade_date,open,high,low,close,vol,amount")
        if df.empty or len(df) < 10:
            return None
        df = df.sort_values("trade_date").reset_index(drop=True)
        return df
    except Exception:
        return None


def calc_atr(df: pd.DataFrame, period: int = 14) -> float:
    """计算 ATR"""
    if df is None or len(df) < period + 2:
        return 0
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        (df["high"] - df["low"]).abs(),
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs()
    ], axis=1).max(axis=1)
    return float(tr.rolling(period).mean().iloc[-1])


def evaluate_position(code: str, entry_price: float, direction: str = "buy",
                      entry_date: str = None) -> Dict:
    """评估单个持仓位，返回动态止损和止盈建议

    Args:
        code: 股票代码 (如 "000151")
        entry_price: 入场价
        direction: "buy" (做多) 或 "sell" (做空)
        entry_date: 入场日期 YYYYMMDD

    Returns:
        dict: 含当前价、ATR、动态止损/目标、浮动止盈进度
    """
    df = get_kline(code, days=60, force=True)
    if df is None or len(df) < 15:
        return {"error": "数据不足", "code": code}

    price = float(df["close"].iloc[-1])
    atr = calc_atr(df)

    if atr <= 0:
        return {"error": "ATR 计算失败", "code": code}

    # === 做多 ===
    if direction == "buy":
        # 浮动止盈阶梯 (ATR 倍数)
        T1_price = entry_price + 2.0 * atr     # 止盈1
        T2_price = entry_price + 3.5 * atr     # 止盈2
        T3_price = entry_price + 6.0 * atr     # 止盈3

        # 浮动止盈逻辑: 根据当前价格决定止损位
        if price >= T3_price:
            # 突破T3 → SL锁定在 T2_price 附近
            current_sl = entry_price + 3.0 * atr
            trailing_level = 3
            sl_label = f"¥{round(current_sl,2)} 锁定+{round((current_sl-entry_price)/entry_price*100,1)}%"
        elif price >= T2_price:
            # 突破T2 → SL锁定在 entry + 1×ATR (锁定部分利润)
            current_sl = entry_price + 1.0 * atr
            trailing_level = 2
            sl_label = f"¥{round(current_sl,2)} 锁定+{round((current_sl-entry_price)/entry_price*100,1)}%"
        elif price >= T1_price:
            # 突破T1 → SL移到保本
            current_sl = entry_price * 1.001  # 略高于成本
            trailing_level = 1
            sl_label = f"¥{round(current_sl,2)} 保本"
        else:
            # 未突破T1 → 用初始止损 1.5×ATR
            current_sl = entry_price - 1.5 * atr
            trailing_level = 0
            sl_label = f"¥{round(current_sl,2)} (-{round((entry_price-current_sl)/entry_price*100,1)}%)"

        current_sl = round(current_sl, 2)

        # 止盈目标 vs 当前价
        targets = [
            {"level": 1, "price": round(T1_price, 2),
             "gain": f"+{round((T1_price-entry_price)/entry_price*100,1)}%",
             "reached": price >= T1_price},
            {"level": 2, "price": round(T2_price, 2),
             "gain": f"+{round((T2_price-entry_price)/entry_price*100,1)}%",
             "reached": price >= T2_price},
            {"level": 3, "price": round(T3_price, 2),
             "gain": f"+{round((T3_price-entry_price)/entry_price*100,1)}%",
             "reached": price >= T3_price},
        ]

        # 浮盈百分比
        unrealized_pnl_pct = (price - entry_price) / entry_price * 100

        # 距止损的距离
        distance_to_sl = (price - current_sl) / price * 100 if price > 0 else 0

        return {
            "code": code, "entry_price": round(entry_price, 2),
            "current_price": round(price, 2),
            "direction": "buy",
            "atr": round(atr, 2),
            "current_stop_loss": current_sl,
            "stop_loss_label": sl_label,
            "targets": targets,
            "trailing_level": trailing_level,
            "unrealized_pnl": round(price - entry_price, 2),
            "unrealized_pnl_pct": round(unrealized_pnl_pct, 1),
            "distance_to_sl_pct": round(distance_to_sl, 1),
            "is_stopped_out": price < current_sl,
        }

    # === 做空 === (反向逻辑)
    else:
        T1_price = entry_price - 2.0 * atr
        T2_price = entry_price - 3.5 * atr
        T3_price = entry_price - 6.0 * atr

        if price <= T3_price:
            current_sl = entry_price - 3.0 * atr
            trailing_level = 3
            sl_label = f"¥{round(current_sl,2)} 锁定"
        elif price <= T2_price:
            current_sl = entry_price - 1.0 * atr
            trailing_level = 2
            sl_label = f"¥{round(current_sl,2)} 锁定部分利润"
        elif price <= T1_price:
            current_sl = entry_price * 0.999
            trailing_level = 1
            sl_label = "保本"
        else:
            current_sl = entry_price + 1.5 * atr
            trailing_level = 0
            sl_label = f"¥{round(current_sl,2)} (+{round((current_sl-entry_price)/entry_price*100,1)}%)"

        current_sl = round(current_sl, 2)

        targets = [
            {"level": 1, "price": round(T1_price, 2),
             "gain": f"{round((T1_price-entry_price)/entry_price*100,1)}%",
             "reached": price <= T1_price},
            {"level": 2, "price": round(T2_price, 2),
             "gain": f"{round((T2_price-entry_price)/entry_price*100,1)}%",
             "reached": price <= T2_price},
            {"level": 3, "price": round(T3_price, 2),
             "gain": f"{round((T3_price-entry_price)/entry_price*100,1)}%",
             "reached": price <= T3_price},
        ]

        unrealized_pnl_pct = (entry_price - price) / entry_price * 100
        distance_to_sl = (current_sl - price) / price * 100 if price > 0 else 0

        return {
            "code": code, "entry_price": round(entry_price, 2),
            "current_price": round(price, 2),
            "direction": "sell",
            "atr": round(atr, 2),
            "current_stop_loss": current_sl,
            "stop_loss_label": sl_label,
            "targets": targets,
            "trailing_level": trailing_level,
            "unrealized_pnl": round(entry_price - price, 2),
            "unrealized_pnl_pct": round(unrealized_pnl_pct, 1),
            "distance_to_sl_pct": round(distance_to_sl, 1),
            "is_stopped_out": price > current_sl,
        }


def batch_evaluate(positions: List[Dict]) -> List[Dict]:
    """批量评估持仓

    Args:
        positions: [{"code": "000151", "entry_price": 13.31, "direction": "buy"}]

    Returns:
        list: 每个持仓的评估结果
    """
    results = []
    for pos in positions:
        r = evaluate_position(
            pos.get("code", ""),
            pos.get("entry_price", 0),
            pos.get("direction", "buy"),
            pos.get("entry_date"),
        )
        # 注入原数据
        r["name"] = pos.get("name", "")
        r["qty"] = pos.get("qty", 0)
        r["note"] = pos.get("note", "")
        results.append(r)
    return results
