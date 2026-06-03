"""
拾米交易工作室 - 操作建议引擎
从 backend.py 提取，保持向后兼容。

包含：
1. generate_advice()       — 综合三大策略 + 市场环境 → 操作建议
2. calc_atr_based_levels() — ATR 动态止盈止损计算
"""

import time
import pandas as pd
from cache import cache_or_fetch
from data.fetcher import fetch_sentiment, fetch_sectors
from services.strategy import run_trend_scan, run_hybrid_scan, run_dragon_scan
from realtime_scorer import get_kline


def calc_atr_based_levels(price, atr):
    """ATR 动态止盈止损计算

    基于 ATR (Average True Range) 计算阶梯止盈目标和止损位。

    Args:
        price: float, 当前价格
        atr: float, ATR 值 (14日平均真实波幅)

    Returns:
        tuple: (t1, t2, t3, sl, trailing_start, trailing_step)
            - t1: 目标1 (2x ATR 保守止盈)
            - t2: 目标2 (3.5x ATR 中等止盈)
            - t3: 目标3 (6x ATR 让利润奔跑)
            - sl: 止损 (1.5x ATR)
            - trailing_start: 浮动止盈启动价格 (目标1)
            - trailing_step: 浮动止盈步长 (0.5x ATR)
    """
    atr_pct = atr / price
    # 目标1: 2x ATR (保守)
    t1 = price + 2 * atr
    # 目标2: 3.5x ATR (中等)
    t2 = price + 3.5 * atr
    # 目标3: 6x ATR (让利润奔跑)
    t3 = price + 6 * atr
    # 止损: 1.5x ATR
    sl = price - 1.5 * atr
    # 浮动止盈建议 (从目标1开始启动)
    trailing_start = t1
    trailing_step = 0.5 * atr  # 每上涨0.5 ATR上移一次止盈
    return t1, t2, t3, sl, trailing_start, trailing_step


def generate_advice():
    """综合三大策略 + 市场环境 → 操作建议

    使用已缓存的结果避免重复拉取 tushare。
    汇总趋势、混合、龙头三大策略的推荐股票，计算共识度、动态仓位、
    ATR 止盈止损位，并结合市场情绪给出综合操作建议。

    Returns:
        dict: {
            "market": {
                "phase": str, "sentiment_score": int,
                "up": int, "down": int, "limit_up": int, "limit_down": int,
                ...phase_advice fields
            },
            "top_sectors": list[str],
            "recommendations": list[dict] — TOP 5 推荐股票，每项含 code, name,
                consensus, strategies, signal, price, entry_zone, stop_loss,
                target_1/2/3, risk_reward, position, reason 等,
            "generated_at": str
        }
    """
    # Use cached results to avoid 60+ tushare calls
    trend = cache_or_fetch("strategy_trend", run_trend_scan, 120)
    hybrid = cache_or_fetch("strategy_hybrid", run_hybrid_scan, 120)
    dragon = cache_or_fetch("strategy_dragon", run_dragon_scan, 120)
    sentiment = cache_or_fetch("sentiment", fetch_sentiment, 30)
    sectors = cache_or_fetch("sectors", fetch_sectors, 120)

    market_phase = sentiment.get("phase", "未知")
    phase_advice = {
        "强势牛市🚀": {"position": "重仓 · 80-100%", "action": "全仓出击，顺势加仓", "risk": "低"},
        "牛市📈":     {"position": "较重 · 65-80%",  "action": "积极做多，精选主线", "risk": "低"},
        "震荡偏多↗️": {"position": "中等偏重 · 45-65%", "action": "谨慎做多，控制单票仓位", "risk": "中"},
        "震荡市➡️":  {"position": "中等 · 25-45%",  "action": "平衡仓位，高抛低吸", "risk": "中"},
        "震荡偏空↘️": {"position": "轻仓 · 10-25%",  "action": "防守为主，快进快出", "risk": "较高"},
        "熊市📉":     {"position": "空仓 · 0-10%",   "action": "空仓等待，现金为王", "risk": "高"},
        "危机模式⚠️": {"position": "空仓 · 0-5%",    "action": "空仓观望，等待系统性风险释放", "risk": "极高"},
    }
    market_advice = phase_advice.get(market_phase, {"position": "30%", "action": "谨慎", "risk": "中"})

    # Build code -> info maps
    def to_map(items, key="code"):
        return {s.get(key, ""): s for s in items if s.get(key)}

    trend_map = to_map(trend.get("picked", []))
    hybrid_map = to_map(hybrid.get("picked", []))
    dragon_map = to_map(dragon.get("picked", []))

    all_codes = set(list(trend_map.keys()) + list(hybrid_map.keys()) + list(dragon_map.keys()))

    # 动态仓位计算
    market_phase = sentiment.get("phase", "未知")
    market_factor = {
        "强势牛市🚀": 1.0, "牛市📈": 0.8, "震荡偏多↗️": 0.6,
        "震荡市➡️": 0.4, "震荡偏空↘️": 0.25, "熊市📉": 0.1,
        "危机模式⚠️": 0.0,
    }.get(market_phase, 0.3)

    def calc_position(consensus, code):
        """动态仓位：市场因子×共识基础×评分修正（上限40%，下限5%）"""
        base = {3: 25, 2: 18}.get(consensus, 10)
        # 评分修正
        score_bonus = 0
        for src in [trend_map, hybrid_map, dragon_map]:
            info = src.get(code)
            if info:
                sc = info.get("trend_score", info.get("score", info.get("leader_score", 0)))
                if sc >= 80: score_bonus = 5
                elif sc >= 60: score_bonus = 2
                elif sc <= 30: score_bonus = -5
                break
        raw = (base + score_bonus) * market_factor
        return f"{round(min(40, max(5, raw)))}%"

    recommendations = []
    for code in all_codes:
        strategies = []
        if code in trend_map:  strategies.append("趋势")
        if code in hybrid_map: strategies.append("混合")
        if code in dragon_map: strategies.append("龙头")

        consensus = len(strategies)
        if consensus < 2:
            continue

        # Get name & price from whichever source has it
        src = trend_map.get(code) or hybrid_map.get(code) or dragon_map.get(code)
        name = src.get("name", "")
        price = float(src.get("price", 0))

        # 尝试拉 kline 算 ATR
        atr = None
        max_attempts = 3
        for s_name in ["trend_map", "hybrid_map", "dragon_map"]:
            src = locals().get(s_name, {})
            s = src.get(code)
            if s and s.get("price", 0) > 0:
                try:
                    kline = get_kline(code, days=30)
                    if kline is not None and len(kline) >= 15:
                        high, low, close = kline["high"], kline["low"], kline["close"].shift(1)
                        tr = pd.concat([(kline["high"]-kline["low"]).abs(),
                                        (kline["high"]-close).abs(),
                                        (kline["low"]-close).abs()], axis=1).max(axis=1)
                        atr = float(tr.rolling(14).mean().iloc[-1])
                    break
                except:
                    pass

        if atr and atr > 0:
            t1, t2, t3, sl, trailing_start, trailing_step = calc_atr_based_levels(price, atr)
            stop_loss = f"¥{round(sl,2)} (-{round((price-sl)/price*100,1)}%)"
            entry_low = round(price * 0.985, 2)
            entry_high = round(price * 1.015, 2)
            rr1 = round((t1 - price) / (price - sl), 1) if (price - sl) > 0 else 1.0
            rr2 = round((t2 - price) / (price - sl), 1) if (price - sl) > 0 else 1.0
            t1_label = f"¥{round(t1,2)} (+{round((t1-price)/price*100,1)}%)"
            t2_label = f"¥{round(t2,2)} (+{round((t2-price)/price*100,1)}%)"
            t3_label = f"¥{round(t3,2)} (+{round((t3-price)/price*100,1)}%)"
        else:
            # 无 ATR 数据：用固定百分比（更宽）
            t1 = price * 1.10
            t2 = price * 1.20
            t3 = price * 1.35
            sl = price * 0.95
            entry_low = round(price * 0.98, 2)
            entry_high = round(price * 1.02, 2)
            stop_loss = f"¥{round(sl,2)} (-5.0%)"
            rr1 = round((t1 - price) / (price - sl), 1) if (price - sl) > 0 else 1.0
            rr2 = round((t2 - price) / (price - sl), 1) if (price - sl) > 0 else 1.0
            t1_label = f"¥{round(t1,2)} (+10.0%)"
            t2_label = f"¥{round(t2,2)} (+20.0%)"
            t3_label = f"¥{round(t3,2)} (+35.0%)"
            trailing_start = t1
            trailing_step = price * 0.03

        trailing_stop = f"从¥{round(trailing_start,2)}启动，每涨{round(trailing_step,2)}上移一次浮动止盈"

        reasons = []
        if "趋势" in strategies:
            s = trend_map[code]
            reasons.append(f"趋势评分{s.get('trend_score','?')}·{s.get('stage','?')}")
        if "混合" in strategies:
            s = hybrid_map[code]
            reasons.append(f"混合{s.get('score','?')}分·评级{s.get('grade','?')}")
        if "龙头" in strategies:
            s = dragon_map[code]
            reasons.append(f"龙头{s.get('leader_score','?')}分·{s.get('grade','?')}")

        recommendations.append({
            "code": code,
            "name": name,
            "consensus": consensus,
            "strategies": strategies,
            "signal": "⭐⭐⭐" if consensus >= 3 else "⭐⭐",
            "price": price,
            "entry_zone": f"¥{entry_low} ~ ¥{entry_high}",
            "stop_loss": stop_loss,
            "target_1": t1_label,
            "target_2": t2_label,
            "target_3": t3_label,
            "target_1_action": "减仓30%",
            "target_2_action": "减仓30%",
            "target_3_action": "清仓",
            "stop_loss_action": "止损出场",
            "risk_reward_1": rr1,
            "risk_reward_2": rr2,
            "trailing_stop": trailing_stop,
            "position": calc_position(consensus, code),
            "reason": " | ".join(reasons),
        })

    recommendations.sort(key=lambda x: (x["consensus"], -x["price"]), reverse=True)
    top_sectors = [s["name"] for s in (sectors[:5] if isinstance(sectors, list) else [])]

    return {
        "market": {
            "phase": market_phase,
            "sentiment_score": sentiment.get("sentiment_score", 0),
            "up": sentiment.get("up", 0),
            "down": sentiment.get("down", 0),
            "limit_up": sentiment.get("limit_up", 0),
            **market_advice,
        },
        "top_sectors": top_sectors,
        "recommendations": recommendations[:5],
        "generated_at": time.strftime("%H:%M:%S"),
    }
    try:
        from db import save_recommendations
        save_recommendations(recommendations[:5], market_phase, time.strftime("%Y-%m-%d %H:%M:%S"))
    except:
        pass
    return result