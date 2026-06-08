"""
海淘美股翻倍预测引擎 (US Doubler Predictor)

公式: potential = catalyst × room × size_boost
  - catalyst:  催化剂综合强度 (0-100, 复用 score_doubler 结果)
  - room:       剩余上涨空间 (0.1-1.0, 基于价格在近期区间的位置)
  - size_boost: 小盘弹性加成 (市值越小弹性越大, 1.0-2.0)

与 us_doubler_scanner.score_doubler 的区别:
  score_doubler 对单只股票做「当前是否具备翻倍潜力」的评分
  本引擎对候选池做「翻倍概率排序」—— 综合考虑催化剂、空间、弹性

用法:
    from magician.doubler_predictor import predict_doublers, predict_batch

    result = predict_doublers(["NVDA", "AMD", "TSLA"])
    # result["NVDA"]["potential"]  — 预测潜力值
    # result["NVDA"]["catalyst"]   — 催化剂强度
    # result["NVDA"]["room"]       — 上涨空间评分
    # result["NVDA"]["size_boost"] — 规模弹性加成

    ranked = predict_batch(["NVDA", "AMD", "TSLA"])
    # ranked[0]["potential"]  — 最高的潜力值
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple

import numpy as np

from haitao.us_fetcher import get_history, calc_technical_indicators
from magician.config import (
    DOUBLER_SEED_POOL,
    CATALYST_WEIGHTS,
    DOUBLER_SCORE_THRESHOLD,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# 内部工具函数
# ═══════════════════════════════════════════════════════════════

def _estimate_market_cap(ticker: str) -> Optional[float]:
    """估算一只美股市值（亿美元）

    通过 get_history 获取最近收盘价，结合粗略的流通股估算。
    由于无实时 API 提供市值，这里用价格 x 典型流通股数近似。

    Returns:
        float: 市值（亿美元），估算失败返回 None
    """
    df = get_history(ticker, days=5)
    if df is None or len(df) < 2:
        return None

    close = float(df["Close"].iloc[-1])
    # 基于价格区间估算典型流通股数（粗略参考值）
    # 高价股（>200）通常流通股较少，低价股（<10）通常流通股较多
    if close > 500:
        shares = 1.5   # 15亿股级别（如 NVDA ～25亿股）
    elif close > 200:
        shares = 2.0   # 20亿股级别（如 AMD ～16亿股）
    elif close > 100:
        shares = 3.0   # 30亿股级别
    elif close > 50:
        shares = 5.0   # 50亿股级别
    elif close > 20:
        shares = 8.0   # 80亿股级别
    elif close > 10:
        shares = 12.0  # 120亿股级别
    else:
        shares = 15.0  # 150亿股级别（低价股如 INTC）

    # 市值 = 股价 × 流通股（亿美元）
    cap = close * shares / 100
    return round(cap, 1)


def _room_score(close: float) -> Tuple[float, dict]:
    """计算剩余上涨空间评分 (0.1-1.0)

    基于价格绝对值判断理论上涨空间：
      - 低价股（<10）空间最大: 1.0
      - 高价股（>500）空间最小: 0.1
      - 中间线性递减

    逻辑: 低价翻倍比高价翻倍容易得多
          （100倍的股票翻倍需要涨500 -> 1000，
           5块的股票翻倍只需要涨5 -> 10）

    Returns:
        (room_score, details_dict)
    """
    if close <= 0:
        return 0.1, {"reason": "价格异常", "close": close}

    if close < 5:
        room = 1.0
        tier = "超低价 (<$5)"
    elif close < 10:
        room = 0.95
        tier = "极低价 ($5-10)"
    elif close < 20:
        room = 0.85
        tier = "低价 ($10-20)"
    elif close < 50:
        room = 0.70
        tier = "中等偏低 ($20-50)"
    elif close < 100:
        room = 0.55
        tier = "中等 ($50-100)"
    elif close < 200:
        room = 0.40
        tier = "中等偏高 ($100-200)"
    elif close < 500:
        room = 0.25
        tier = "高价 ($200-500)"
    else:
        room = 0.10
        tier = "超高价格 (>$500)"

    return round(room, 2), {"reason": tier, "close": close}


def _size_boost(market_cap: Optional[float]) -> float:
    """根据市值计算小盘弹性加成

    Args:
        market_cap: 市值（亿美元），None 时使用默认值 1.0

    Returns:
        size_boost: 弹性加成系数
    """
    if market_cap is None:
        return 1.0

    if market_cap < 5:
        return 2.0    # 微型股: 极大弹性
    elif market_cap < 10:
        return 1.7    # 小盘股: 大弹性
    elif market_cap < 20:
        return 1.5    # 中小盘
    elif market_cap < 50:
        return 1.3    # 中盘
    elif market_cap < 100:
        return 1.1    # 中大盘
    elif market_cap < 500:
        return 1.0    # 大盘
    else:
        return 0.7    # 超大盘: 弹性受限


def _catalyst_score(ticker: str) -> Tuple[float, dict]:
    """计算催化剂综合强度 (0-100, 核心指标)

    使用技术指标 + 价格动量模拟催化剂评分:
      - 趋势强度 (权重 30): SMA多头排列 + 价格位置
      - 动量因子 (权重 25): RSI + 价格相对SMA
      - 量能确认 (权重 20): 成交量比
      - 波动弹性 (权重 15): ATR相对值
      - 价格健康 (权重 10): 布林带位置

    Returns:
        (catalyst_score, details_dict)
    """
    df = get_history(ticker, days=180)
    if df is None or len(df) < 20:
        return 0.0, {"error": "数据不足"}

    tech = calc_technical_indicators(df)
    if not tech:
        return 0.0, {"error": "技术指标计算失败"}

    close_vals = df["Close"].values.astype(float)
    cur_close = float(close_vals[-1])

    score = 0.0
    details = {}

    # ── 1. 趋势强度 (0-30) ───────────────────
    s5 = tech.get("sma5")
    s20 = tech.get("sma20")
    s60 = tech.get("sma60")
    trend_score = 0.0

    if s5 and s20 and s60:
        # 多头排列: s5 > s20 > s60
        if s5 > s20 > s60:
            trend_score = 25.0
        elif s5 > s20:
            trend_score = 18.0
        elif s5 > s60:
            trend_score = 12.0
        else:
            trend_score = 5.0

        # 价格在SMA20之上加成分
        pct_vs_s20 = tech.get("price_vs_sma20")
        if pct_vs_s20 is not None and pct_vs_s20 > 0:
            trend_score = min(trend_score + min(pct_vs_s20 * 0.5, 5.0), 30.0)

    score += trend_score
    details["trend"] = round(trend_score, 1)

    # ── 2. 动量因子 (0-25) ───────────────────
    rsi = tech.get("rsi14")
    momentum_score = 0.0
    if rsi is not None:
        # RSI 50-70: 温和上涨趋势, 最佳启动区间
        if 50 <= rsi <= 70:
            momentum_score = 20.0 + (rsi - 50) * 0.3  # 50→20分, 70→26分
        elif 40 <= rsi < 50:
            momentum_score = 10.0  # 偏弱但有反弹潜力
        elif 70 < rsi <= 85:
            momentum_score = 15.0  # 强势但接近超买
        elif rsi > 85:
            momentum_score = 5.0   # 严重超买
        else:
            momentum_score = 5.0   # 弱势

        momentum_score = min(momentum_score, 25.0)

    # 价格 vs SMA5 加成
    pv5 = tech.get("price_vs_sma5")
    if pv5 is not None and pv5 > 2:
        momentum_score = min(momentum_score + 3.0, 25.0)

    score += momentum_score
    details["momentum"] = round(momentum_score, 1)

    # ── 3. 量能确认 (0-20) ───────────────────
    vr = tech.get("volume_ratio")
    vol_score = 0.0
    if vr is not None:
        if vr >= 2.0:
            vol_score = 18.0
        elif vr >= 1.5:
            vol_score = 15.0
        elif vr >= 1.2:
            vol_score = 12.0
        elif vr >= 0.8:
            vol_score = 8.0
        elif vr >= 0.5:
            vol_score = 5.0
        else:
            vol_score = 2.0

    score += vol_score
    details["volume"] = round(vol_score, 1)

    # ── 4. 波动弹性 (0-15) ───────────────────
    atr = tech.get("atr14")
    vol_elasticity = 0.0
    if atr and cur_close > 0:
        atr_pct = atr / cur_close * 100
        if 2.0 <= atr_pct <= 5.0:
            vol_elasticity = 13.0  # 理想波动区间
        elif 1.0 <= atr_pct < 2.0:
            vol_elasticity = 10.0  # 波动偏低
        elif 5.0 < atr_pct <= 8.0:
            vol_elasticity = 8.0   # 高波动
        elif atr_pct > 8.0:
            vol_elasticity = 4.0   # 极速波动
        else:
            vol_elasticity = 6.0   # 波动不足

        # ATR相对股价比例适中: 有弹性又不至于风险过大
        score += vol_elasticity
        details["volatility"] = round(vol_elasticity, 1)

    # ── 5. 价格健康度 (0-10) ─────────────────
    bb_upper = tech.get("bb_upper")
    bb_lower = tech.get("bb_lower")
    health = 0.0

    if bb_upper and bb_lower and bb_upper > bb_lower:
        # 价格在布林带下半区: 有上涨空间
        if cur_close <= bb_lower:
            health = 9.0  # 下轨附近: 超卖反弹潜力
        elif cur_close <= (bb_lower + bb_upper) / 2:
            health = 7.0  # 中轨以下: 空间充足
        elif cur_close <= bb_upper:
            health = 5.0  # 中轨到上轨: 空间一般
        else:
            health = 2.0  # 突破上轨: 短期透支

    score += health
    details["health"] = round(health, 1)
    details["total"] = round(score, 1)

    return round(score, 1), details


def _analyze_single(ticker: str) -> Optional[dict]:
    """分析单个股票的翻倍潜力

    Args:
        ticker: 股票代码

    Returns:
        dict 包含 potential, catalyst, room, size_boost 等字段,
        或 None (数据不足时)
    """
    df = get_history(ticker, days=180)
    if df is None or len(df) < 20:
        logger.debug(f"[predictor] {ticker}: 历史数据不足")
        return None

    close = float(df["Close"].iloc[-1])

    # 1) 催化剂评分
    catalyst, cat_details = _catalyst_score(ticker)

    # 2) 上涨空间
    room, room_details = _room_score(close)

    # 3) 市值与弹性
    mc = _estimate_market_cap(ticker)
    boost = _size_boost(mc)

    # 4) 潜力 = 催化剂 × 空间 × 弹性
    potential = round(catalyst * room * boost, 1)

    # 5) 当前价格 vs 近期范围 (附加信息)
    recent_low = float(np.min(df["Low"].tail(60).values)) if len(df) >= 60 else float(np.min(df["Low"].values))
    recent_high = float(np.max(df["High"].tail(60).values)) if len(df) >= 60 else float(np.max(df["High"].values))
    price_position = round((close - recent_low) / (recent_high - recent_low) * 100, 1) if recent_high > recent_low else 50.0

    return {
        "ticker": ticker,
        "close": round(close, 2),
        "potential": potential,
        "catalyst": catalyst,
        "catalyst_details": cat_details,
        "room": room,
        "room_details": room_details,
        "market_cap_yi": mc,
        "size_boost": boost,
        "price_position_pct": price_position,
        "recent_60d_low": round(recent_low, 2),
        "recent_60d_high": round(recent_high, 2),
    }


# ═══════════════════════════════════════════════════════════════
# 公开 API
# ═══════════════════════════════════════════════════════════════

def predict_doublers(tickers: List[str]) -> Dict[str, Optional[dict]]:
    """预测一组美股的翻倍潜力

    Args:
        tickers: 股票代码列表

    Returns:
        dict: {ticker: result_dict_or_None, ...}
            result_dict 包含:
                - potential:   翻倍潜力总分
                - catalyst:    催化剂综合强度 (0-100)
                - room:        剩余上涨空间 (0.1-1.0)
                - size_boost:  小盘弹性加成 (0.7-2.0)
                - close:       最新收盘价
                - market_cap_yi: 估算市值（亿美元）
                - catalyst_details: 催化剂细分
                - room_details: 空间评分详情
    """
    clean = list({t.strip().upper() for t in tickers if t.strip()})
    if not clean:
        return {}

    results: Dict[str, Optional[dict]] = {}
    pool = ThreadPoolExecutor(max_workers=3)

    futures = {pool.submit(_analyze_single, t): t for t in clean}

    for f in as_completed(futures):
        t = futures[f]
        try:
            result = f.result()
            results[t] = result
        except Exception as e:
            logger.error(f"[predictor] {t} 分析失败: {e}")
            results[t] = None

    pool.shutdown(wait=False)
    return results


def predict_batch(tickers: List[str]) -> List[dict]:
    """批量预测并返回按潜力排序的结果列表

    Args:
        tickers: 股票代码列表

    Returns:
        list[dict]: 按 potential 降序排列的结果列表
            仅包含分析成功的股票（失败的被排除）
    """
    raw = predict_doublers(tickers)

    candidates = []
    for ticker, result in raw.items():
        if result is not None and result.get("potential", 0) > 0:
            candidates.append(result)

    candidates.sort(key=lambda x: -x["potential"])
    return candidates
