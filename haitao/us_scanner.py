"""海淘美股 - 技术评分与趋势分析引擎
对齐拾米 realtime_scorer.py 风格：阶段判定 + 多因子评分
"""
import logging
from typing import Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

import yfinance as yf
import pandas as pd
import numpy as np

from haitao.us_fetcher import (
    get_quotes, get_history, calc_technical_indicators, _set_cache, _cached,
)
from haitao.config import CACHE_TTL_SCAN

logger = logging.getLogger(__name__)


def score_stock(ticker: str, period: str = "6mo") -> dict:
    """对单只美股进行多因子评分（0-100）

    评分维度（对齐拾米 trend_detect + hybrid_score 逻辑）:
    - 趋势强度 (30分): MA排列 + 价格位置
    - 动量因子 (25分): RSI + MACD
    - 成交量验证 (20分): 量比 + 均量趋势
    - 波动率评估 (15分): ATR百分比 + 布林带位置
    - 阶段判定 (10分): 鱼头/鱼身/鱼尾

    Returns:
        dict with score, phase, signals, and details
    """
    df = get_history(ticker, period=period)
    if df is None or len(df) < 20:
        return {
            "ticker": ticker, "score": 0, "phase": "数据不足",
            "error": "insufficient data"
        }

    tech = calc_technical_indicators(df)
    if not tech:
        return {"ticker": ticker, "score": 0, "phase": "数据不足", "error": "calc failed"}

    close = df["Close"].values.astype(float)
    current_price = float(close[-1])

    score = 0
    signals = []
    details = {}

    # ─── 1. 趋势强度 (30分) ─────────────────────
    trend_score = 0
    sma5 = tech.get("sma5")
    sma20 = tech.get("sma20")
    sma60 = tech.get("sma60")

    # MA 多头排列
    ma_aligned = False
    if sma5 and sma20 and sma60:
        if sma5 > sma20 > sma60:
            ma_aligned = True
            trend_score += 15
            signals.append("MA多头排列")
        elif sma60 > sma20 > sma5:
            trend_score -= 5
            signals.append("⚠️ MA空头排列")
        elif sma5 > sma20:
            trend_score += 8
            signals.append("短期偏多")

    # 价格位于MA之上
    price_vs_sma20 = tech.get("price_vs_sma20")
    if price_vs_sma20 is not None:
        if price_vs_sma20 > 5:
            trend_score += 10
        elif price_vs_sma20 > 1:
            trend_score += 6
        elif price_vs_sma20 > -1:
            trend_score += 3
        elif price_vs_sma20 < -5:
            trend_score -= 3

    # SMA100 支撑
    sma100 = tech.get("sma100")
    if sma100 and current_price > sma100:
        trend_score += 5
        signals.append("站上SMA100")
    elif sma100 and current_price < sma100 * 0.9:
        trend_score -= 3

    trend_score = max(-5, min(30, trend_score))
    score += trend_score
    details["trend"] = {"score": trend_score, "ma_aligned": ma_aligned}

    # ─── 2. 动量因子 (25分) ─────────────────────
    momentum_score = 0
    rsi = tech.get("rsi14")
    if rsi is not None:
        if 40 <= rsi <= 60:
            momentum_score += 10
            signals.append(f"RSI中性({rsi})")
        elif 30 <= rsi < 40:
            momentum_score += 15
            signals.append(f"RSI超卖反弹({rsi})")
        elif 60 < rsi <= 75:
            momentum_score += 12
            signals.append(f"RSI偏强({rsi})")
        elif rsi > 75:
            momentum_score += 5
            signals.append(f"⚠️ RSI超买({rsi})")
        elif rsi < 30:
            momentum_score += 8
            signals.append(f"RSI深度超卖({rsi})")

    macd = tech.get("macd")
    if macd and macd.get("macd") is not None and macd.get("signal") is not None:
        if macd["macd"] > macd["signal"]:  # MACD金叉
            momentum_score += 8
            signals.append("MACD金叉")
        else:
            momentum_score -= 2

    # 短期涨幅
    if len(close) >= 5:
        ret_5d = (close[-1] / close[-5] - 1) * 100
        if 1 <= ret_5d <= 10:
            momentum_score += 5
        elif ret_5d > 10:
            momentum_score += 2
            signals.append(f"⚠️ 5日涨{ret_5d:.1f}%过热")
        elif ret_5d < -5:
            momentum_score -= 2

    momentum_score = max(-5, min(25, momentum_score))
    score += momentum_score
    details["momentum"] = {"score": momentum_score, "rsi": rsi}

    # ─── 3. 成交量验证 (20分) ─────────────────
    vol_score = 0
    vol_ratio = tech.get("volume_ratio")
    if vol_ratio is not None:
        if vol_ratio > 1.5:
            vol_score += 12
            signals.append("放量")
        elif vol_ratio > 1.2:
            vol_score += 7
        elif vol_ratio < 0.5:
            vol_score -= 3
            signals.append("缩量")
        else:
            vol_score += 4

    # 成交量趋势（最近5日 vs 之前20日）
    if len(close) >= 30 and "Volume" in df.columns:
        recent_vol = df["Volume"].values[-5:].astype(float)
        prior_vol = df["Volume"].values[-30:-5].astype(float)
        if np.mean(prior_vol) > 0:
            vol_ratio_5d = np.mean(recent_vol) / np.mean(prior_vol)
            if vol_ratio_5d > 1.3:
                vol_score += 8
                if "放量" not in signals:
                    signals.append("持续放量")

    vol_score = max(-5, min(20, vol_score))
    score += vol_score
    details["volume"] = {"score": vol_score, "vol_ratio": vol_ratio}

    # ─── 4. 波动率评估 (15分) ─────────────────
    vola_score = 0
    atr = tech.get("atr14")
    if atr and current_price > 0:
        atr_pct = (atr / current_price) * 100
        if 1 <= atr_pct <= 3:
            vola_score += 8
        elif atr_pct < 1:
            vola_score += 5
            signals.append("低波动")
        else:
            vola_score += 2
            signals.append(f"高波动(ATR{atr_pct:.1f}%)")

    # 布林带位置
    bb_upper = tech.get("bb_upper")
    bb_lower = tech.get("bb_lower")
    if bb_upper and bb_lower and bb_upper > bb_lower:
        bb_width = bb_upper - bb_lower
        bb_pos = (current_price - bb_lower) / bb_width if bb_width > 0 else 0.5
        if bb_pos < 0.2:
            vola_score += 5
            signals.append("布林下轨(超卖)")
        elif bb_pos > 0.8:
            vola_score += 2
            signals.append("布林上轨(超买)")
        elif 0.35 <= bb_pos <= 0.65:
            vola_score += 3
        else:
            vola_score += 1

    vola_score = max(-5, min(15, vola_score))
    score += vola_score
    details["volatility"] = {"score": vola_score}

    # ─── 5. 阶段判定 (10分 + 标签) ─────────────
    phase_score = 0
    phase = _judge_phase(tech, df)
    if phase == "🐟 鱼头":
        phase_score += 10
        signals.append("鱼头初现")
    elif phase == "🐟 鱼身":
        phase_score += 8
        signals.append("主升鱼身")
    elif phase == "🐟 鱼尾":
        phase_score -= 3
        signals.append("⚠️ 鱼尾区域")
    else:
        phase_score += 3

    score += phase_score
    details["phase"] = {"score": phase_score, "phase": phase}

    # ─── 总分 ──────────────────────────────
    score = max(0, min(100, score))
    rating = _get_rating(score)

    return {
        "ticker": ticker,
        "score": score,
        "rating": rating,
        "phase": phase,
        "current_price": current_price,
        "signals": signals,
        "details": details,
        "indicators": tech,
    }


def _judge_phase(tech: dict, df: pd.DataFrame) -> str:
    """判定个股所处阶段：鱼头/鱼身/鱼尾/调整

    逻辑（对齐拾米 philosophy: 数据驱动，反对经验主义）:
    鱼头：SMA5>SMA20 金叉初成 + RSI在40-60 + 价格突破SMA60
    鱼身：MA多头排列 + RSI在50-75 + 成交量放大
    鱼尾：RSI>75超买 + MACD顶背离或死叉 + 放量滞涨
    调整：SMA5<SMA20 + RSI<40
    """
    if df is None or len(df) < 30:
        return "数据不足"

    close = df["Close"].values.astype(float)
    sma5 = tech.get("sma5")
    sma20 = tech.get("sma20")
    sma60 = tech.get("sma60")
    rsi = tech.get("rsi14")
    macd = tech.get("macd")

    if not all([sma5, sma20, sma60]):
        return "数据不足"

    current_price = float(close[-1])
    price_vs_sma60 = (current_price / sma60 - 1) * 100 if sma60 else 0

    # 鱼尾判定（优先，安全第一）
    if rsi and rsi > 75:
        # 检查是否放量滞涨
        if len(close) >= 5:
            ret_5d = (close[-1] / close[-5] - 1) * 100
            vol_ratio = tech.get("volume_ratio", 1)
            if 0 < ret_5d < 3 and (vol_ratio or 1) > 1.2:
                return "🐟 鱼尾（放量滞涨）"
        if macd and macd.get("macd") is not None and macd.get("signal") is not None:
            if macd["macd"] < macd["signal"]:
                return "🐟 鱼尾（MACD死叉）"
        return "🐟 鱼尾（超买）"

    # 鱼身判定
    if sma5 > sma20 > sma60:
        if rsi and 50 <= rsi <= 75:
            vol_ratio = tech.get("volume_ratio", 1)
            if vol_ratio and vol_ratio > 1.0:
                return "🐟 鱼身（主升）"
            return "🐟 鱼身"
        elif rsi and rsi < 50 and price_vs_sma60 > 2:
            return "🐟 鱼身（回调中）"
        return "🐟 鱼身"

    # 鱼头判定
    if sma5 > sma20 and current_price > sma60:
        if rsi and 40 <= rsi <= 60:
            return "🐟 鱼头（初升）"
        return "🐟 鱼头"

    # 调整 / 下降
    if sma20 > sma5 and current_price < sma60:
        if rsi and rsi < 40:
            return "📉 调整（弱势）"
        return "📉 调整"

    if sma5 > sma20:
        return "🐟 鱼头"

    return "📊 盘整"


def _get_rating(score: int) -> str:
    """评分 → 评级"""
    if score >= 80:
        return "强烈推荐 ⭐⭐⭐"
    elif score >= 65:
        return "推荐 ⭐⭐"
    elif score >= 50:
        return "关注 ⭐"
    elif score >= 35:
        return "观望"
    else:
        return "回避"


# ─── 批量扫描 ──────────────────────────────

def scan_watchlist(tickers: List[str]) -> List[dict]:
    """扫描跟踪列表中的美股，返回评分排序"""
    results = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        fut_map = {ex.submit(score_stock, t): t for t in tickers}
        for f in as_completed(fut_map):
            r = f.result()
            if "error" not in r or not r.get("error"):
                results.append(r)
    results.sort(key=lambda x: x.get("score", 0), reverse=True)
    return results


def scan_top_gainers(limit: int = 20) -> List[dict]:
    """扫描当日涨幅最大的美股"""
    cached = _cached("us_gainers", CACHE_TTL_SCAN)
    if cached is not None:
        return cached[:limit]

    # Get hot stocks
    from haitao.us_fetcher import get_hot_stocks
    hot = get_hot_stocks()
    gainers = [h for h in hot if h.get("change_pct", 0) is not None and h["change_pct"] > 0]
    gainers.sort(key=lambda x: x.get("change_pct", 0), reverse=True)

    # Score top gainers
    tickers = [g["ticker"] for g in gainers[:15]]
    scored = scan_watchlist(tickers)

    _set_cache("us_gainers", scored, CACHE_TTL_SCAN)
    return scored[:limit]


def scan_adr_picks() -> List[dict]:
    """扫描中概股，筛选值得关注的"""
    cached = _cached("us_adr_picks", CACHE_TTL_SCAN)
    if cached is not None:
        return cached

    from haitao.config import CHINESE_ADR
    results = scan_watchlist(CHINESE_ADR)
    picks = [r for r in results if r.get("score", 0) >= 50]
    _set_cache("us_adr_picks", picks, CACHE_TTL_SCAN)
    return picks


def clear_cache():
    from haitao.us_fetcher import clear_cache as _clr
    _clr()
