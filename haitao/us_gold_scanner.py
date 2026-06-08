"""海淘掘金 — 美股黄金挖掘机
掘金思路：找"催化剂 + 技术面 + 上涨空间"三者共振的美股

对标拾米翻倍掘金公式: score = base_score + catalyst_d7..d10 + room × size_boost

美股三大掘金维度:
1. 📐 技术面 (40分): 趋势强度 + 动量 + 成交量突破
2. 🔥 催化剂 (35分): 财报窗口 + 行业轮动 + 机构关注
3. 🚀 上涨空间 (25分): 非高位 + 波动弹性 + 筹码结构
"""
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd

from haitao.us_fetcher import get_quotes, get_history, calc_technical_indicators
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


# ─── 黄金股评分引擎 ─────────────────────────

def gold_score(ticker: str) -> dict:
    """对一只美股进行黄金挖掘评分（0-100）"""
    # Try full history first, fall back to whatever is available
    hist = get_history(ticker, 180)
    if hist is None or len(hist) < 5:
        hist = get_history(ticker, 60)
    if hist is None or len(hist) < 5:
        hist = get_history(ticker, 30)
    if hist is None or len(hist) < 5:
        hist = get_history(ticker, 10)
    
    if hist is None or len(hist) < 5:
        return {"ticker": ticker, "score": 0, "rating": "数据不足", "error": "insufficient data"}
    
    # Convert to DataFrame if needed
    if not isinstance(hist, pd.DataFrame):
        hist = pd.DataFrame(hist)
    
    tech = calc_technical_indicators(hist)
    days = len(hist)
    data_quality = "优" if days >= 60 else ("中" if days >= 20 else "低")
    
    close = hist["Close"].values.astype(float)
    current_price = float(close[-1])

    detail = {}
    score = 0
    signals = []

    # ─── 1. 技术面 (40分) ─────────────────────
    tech_score = _score_technical(tech, close, signals)
    score += tech_score
    detail["technical"] = tech_score

    # ─── 2. 上涨空间 (25分) ─────────────────
    room_score = _score_room(tech, close, hist, signals)
    score += room_score
    detail["room"] = room_score

    # ─── 3. 催化剂 (35分) ────────────────────
    cat_score, cat_info = _score_catalyst(ticker, tech, hist, signals)
    score += cat_score
    detail["catalyst"] = cat_score

    # ─── 总分与评级 ───────────────────────
    score = max(0, min(100, score))
    rating = _gold_rating(score)
    phase = _gold_phase(score, tech, signals)

    return {
        "ticker": ticker,
        "score": score,
        "rating": rating,
        "phase": phase,
        "data_quality": data_quality,
        "current_price": round(current_price, 2),
        "gold_signals": signals,
        "detail": detail,
        "technicals": {
            "rsi14": tech.get("rsi14"),
            "sma20": tech.get("sma20"),
            "sma60": tech.get("sma60"),
            "atr14": tech.get("atr14"),
            "vol_ratio": tech.get("volume_ratio"),
        },
        "catalyst": cat_info,
    }


def _score_technical(tech: dict, close: np.ndarray, signals: list) -> int:
    """技术面评分 (0-40)"""
    ts = 0

    # MA排列 (12分)
    sma5 = tech.get("sma5")
    sma20 = tech.get("sma20")
    sma60 = tech.get("sma60")
    if sma5 and sma20 and sma60:
        if sma5 > sma20 > sma60:
            ts += 12
            signals.append("MA多头排列 📈")
        elif sma5 > sma20:
            ts += 7
            signals.append("短期MA偏多")
        elif sma60 > sma20 > sma5:
            ts -= 3
            signals.append("⚠️ MA空头")
        else:
            ts += 3

    # 价格位置 (8分)
    pv20 = tech.get("price_vs_sma20")
    if pv20 is not None:
        if 3 <= pv20 <= 15:
            ts += 8
            signals.append("价格站稳SMA20上方")
        elif pv20 > 15:
            ts += 3
            signals.append("远离均线（可能过热）")
        elif 0 <= pv20 < 3:
            ts += 5
            signals.append("刚站上SMA20")
        elif -5 < pv20 < 0:
            ts += 2
            signals.append("SMA20附近整理")

    # RSI (8分)
    rsi = tech.get("rsi14")
    if rsi is not None:
        if 40 <= rsi <= 60:
            ts += 8
            signals.append(f"RSI中性{rsi}（有空间）")
        elif 30 <= rsi < 40:
            ts += 6
            signals.append(f"RSI超卖区{rsi}（反弹潜力）")
        elif 60 < rsi <= 75:
            ts += 5
        elif rsi > 75:
            ts -= 2
            signals.append(f"⚠️ RSI超买{rsi}")
        elif rsi < 30:
            ts += 3

    # 成交量验证 (12分)
    vr = tech.get("volume_ratio")
    if vr is not None:
        if vr > 1.8:
            ts += 12
            signals.append("🔥 放量突破")
        elif vr > 1.3:
            ts += 8
            signals.append("成交量放大")
        elif vr > 0.8:
            ts += 5
        else:
            ts += 1

    return max(-5, min(40, ts))


def _score_room(tech: dict, close: np.ndarray, df: pd.DataFrame, signals: list) -> int:
    """上涨空间评分 (0-25)"""
    rs = 0
    current_price = float(close[-1])

    # 非高位 (8分) - 距离年内高点的距离
    if len(close) >= 60:
        year_high = float(np.max(close[-60:]))
        year_low = float(np.min(close[-60:]))
        range_width = year_high - year_low
        if range_width > 0:
            position = (current_price - year_low) / range_width
            if position < 0.3:
                rs += 8
                signals.append("处于年度低位区 🎯")
            elif position < 0.5:
                rs += 6
                signals.append("中低位区")
            elif position < 0.7:
                rs += 4
                signals.append("中位区")
            else:
                rs += 1
    else:
        rs += 4

    # ATR波动弹性 (8分)
    atr = tech.get("atr14")
    if atr and current_price > 0:
        atr_pct = (atr / current_price) * 100
        if 2 <= atr_pct <= 5:
            rs += 8
            signals.append(f"ATR波动适中({atr_pct:.1f}%)")
        elif atr_pct > 5:
            rs += 5
            signals.append(f"高波动品种({atr_pct:.1f}%)")
        else:
            rs += 2

    # 布林带位置 (5分)
    bb_lower = tech.get("bb_lower")
    bb_upper = tech.get("bb_upper")
    if bb_lower and bb_upper and bb_upper > bb_lower:
        pos = (current_price - bb_lower) / (bb_upper - bb_lower)
        if pos < 0.3:
            rs += 5
            signals.append("布林下轨区域")
        elif pos < 0.5:
            rs += 3

    # 距离SMA60支撑 (4分)
    sma60 = tech.get("sma60")
    pv60 = tech.get("price_vs_sma20")  # approximate
    if sma60 and current_price > sma60:
        dist = (current_price / sma60 - 1) * 100
        if dist < 5:
            rs += 4
            signals.append("靠近SMA60支撑")

    return max(0, min(25, rs))


def _score_catalyst(ticker: str, tech: dict, df: pd.DataFrame, signals: list) -> tuple:
    """催化剂评分 (0-35) + 催化剂信息"""
    cs = 0
    cat_info = {"earnings": None, "sector": None, "momentum": None, "institutional": None}
    close = df["Close"].values.astype(float)
    vol = df["Volume"].values.astype(float) if "Volume" in df.columns else None

    # ─── 动量催化剂 (15分) ───────────────
    if len(close) >= 20:
        ret_1m = (close[-1] / close[-20] - 1) * 100
        if 5 <= ret_1m <= 20:
            cs += 12
            signals.append(f"📈 1月涨{ret_1m:.1f}%（温和启动）")
            cat_info["momentum"] = {"ret_1m_pct": round(ret_1m, 1), "signal": "温和启动"}
        elif 20 < ret_1m <= 40:
            cs += 8
            signals.append(f"1月涨{ret_1m:.1f}%（主升段）")
            cat_info["momentum"] = {"ret_1m_pct": round(ret_1m, 1), "signal": "主升段"}
        elif ret_1m < -10:
            cs += 4
            signals.append(f"1月跌{ret_1m:.1f}%（超跌反弹机会）")
            cat_info["momentum"] = {"ret_1m_pct": round(ret_1m, 1), "signal": "超跌"}
        elif 0 <= ret_1m < 5:
            cs += 6
            signals.append("横盘中（等风来）")

    # ─── 成交量激增 (8分) ────────────────
    if vol is not None and len(vol) >= 22:
        avg_vol_1m = float(np.mean(vol[-22:]))
        if avg_vol_1m > 0:
            recent_vol = float(np.mean(vol[-5:]))
            vol_surge = recent_vol / avg_vol_1m
            if vol_surge > 1.5:
                cs += 8
                signals.append("🔥 近5日成交量激增")
                cat_info["institutional"] = {"vol_surge": round(vol_surge, 2), "signal": "机构进场信号"} if vol_surge > 2 else {"vol_surge": round(vol_surge, 2), "signal": "资金关注"}
            elif vol_surge > 1.2:
                cs += 4

    # ─── 财报窗口 (7分) ─────────────────
    try:
        import yfinance as yf
        tk = yf.Ticker(ticker)
        cal = tk.calendar
        if cal is not None and not cal.empty:
            earnings_date = cal.get("Earnings Date")
            if earnings_date is not None:
                if hasattr(earnings_date, '__iter__'):
                    next_earnings = earnings_date[0] if len(earnings_date) > 0 else None
                else:
                    next_earnings = earnings_date
                if next_earnings:
                    now = datetime.now()
                    days_to = (next_earnings - now).days if hasattr(next_earnings, 'days') else 30
                    if 0 <= days_to <= 14:
                        cs += 7
                        signals.append(f"📅 {days_to}天内财报发布")
                        cat_info["earnings"] = {"days_to": days_to, "signal": f"{days_to}天内"}
                    elif days_to <= 30:
                        cs += 4
                        cat_info["earnings"] = {"days_to": days_to, "signal": f"{days_to}天"}
    except Exception:
        pass

    # ─── 估值简单判断 (5分) ─────────────
    try:
        from yfinance import Ticker as YFTicker
        info = YFTicker(ticker).info
        pe = info.get("trailingPE") or info.get("forwardPE") or 0
        if pe and 0 < pe < 30:
            cs += 5
            signals.append(f"PE合理({pe:.1f})")
            cat_info["valuation"] = {"pe": round(pe, 1), "signal": "合理"}
        elif pe and pe > 50:
            cs -= 2
            signals.append(f"⚠️ 高PE({pe:.1f})")
    except Exception:
        pass

    return max(-5, min(35, cs)), cat_info


def _gold_rating(score: int) -> str:
    """黄金评分 → 评级"""
    if score >= 75:
        return "🥇 金矿"
    elif score >= 60:
        return "🥈 银矿"
    elif score >= 45:
        return "🥉 铜矿"
    elif score >= 30:
        return "🪨 石矿"
    else:
        return "💩 废石"


def _gold_phase(score: int, tech: dict, signals: list) -> str:
    """综合评定所处阶段"""
    if score >= 75:
        return "🔥 黄金爆发期"
    elif score >= 60:
        return "⛏️ 掘金窗口期"
    elif score >= 45:
        return "🔍 值得关注"
    elif score >= 30:
        return "👀 保持观察"
    else:
        return "⏳ 等待时机"


# ─── 批量掘金扫描 ─────────────────────────

def gold_pan(tickers: List[str]) -> List[dict]:
    """批量淘金——扫描多个标的返回黄金评分排序"""
    results = []
    with ThreadPoolExecutor(max_workers=2) as ex:  # 2 concurrent to avoid Yahoo rate limit
        fut_map = {ex.submit(gold_score, t): t for t in tickers}
        for f in as_completed(fut_map):
            try:
                r = f.result(timeout=20)  # 20s per ticker max
                if "error" not in r:
                    results.append(r)
            except Exception:
                pass
    results.sort(key=lambda x: x.get("score", 0), reverse=True)
    return results


def gold_pan_hot() -> List[dict]:
    """扫描热门美股金矿"""
    from haitao.config import HOT_US_STOCKS
    return gold_pan(HOT_US_STOCKS)


def gold_pan_adr() -> List[dict]:
    """扫描中概股金矿"""
    from haitao.config import CHINESE_ADR
    return gold_pan(CHINESE_ADR)


def gold_pan_top_gainers() -> List[dict]:
    """扫描涨幅榜金矿"""
    from haitao.us_fetcher import get_hot_stocks
    hot = get_hot_stocks()
    tickers = [h["ticker"] for h in hot if h.get("change_pct", 0) is not None][:10]
    return gold_pan(tickers) if tickers else []


# ─── 掘金报告 ─────────────────────────────

def gold_report(tickers: List[str] = None) -> dict:
    """生成掘金报告——最值得买的黄金股排行"""
    from haitao.config import HOT_US_STOCKS, CHINESE_ADR
    if not tickers:
        tickers = list(set(HOT_US_STOCKS[:8] + CHINESE_ADR[:8]))
    
    results = gold_pan(tickers)
    golds = [r for r in results if r.get("score", 0) >= 60]
    silvers = [r for r in results if 45 <= r.get("score", 0) < 60]
    
    return {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "total_scanned": len(results),
        "golds": golds,
        "silvers": silvers,
        "top_pick": results[0] if results else None,
    }
