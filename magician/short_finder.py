"""海淘掘金 — 美股做空机会发现器
做空思路: 找"过度延伸 + 技术转弱 + 资金背离"三重共振的美股

对标拾米做空评分矩阵:
  S1 过度延伸(20) — RSI超买 + 远离均线 + 布林上轨外
  S2 量能背离(15) — 价涨量缩 / 放量滞涨
  S3 财报前波动(10) — 财报将至 + 隐含波动率飙升
  S4 估值泡沫(10) — PE极端偏高
  技术面偏空(30) — 死叉 + 下跌趋势 + MACD空头 + 支撑破位
  缺口分析(15) — 盘前跳空衰竭 / 岛形反转

总分: 0-100, 阈值: config.SHORT_SCORE_THRESHOLD (60)
"""
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd

from haitao.us_fetcher import get_quotes, get_history, calc_technical_indicators, get_pre_post_market
from magician.config import SHORT_SCORE_THRESHOLD, CATALYST_WEIGHTS
from haitao.us_fetcher import _cached, _set_cache

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# 核心评分引擎
# ═══════════════════════════════════════════════════════════════

def _short_score(ticker: str) -> dict:
    """对一只美股进行做空评分 (0-100)

    6维做空评分体系:
      S1 过度延伸(20) — 超买 + 远离均线
      S2 量能背离(15) — 价量背离 / 放量滞涨
      S3 财报前波动(10) — 财报窗口 + 波动率飙升
      S4 估值泡沫(10) — PE极端高估
      技术偏空(30) — 趋势 + MACD + 支撑
      缺口分析(15) — 跳空衰竭 + 缺口回补概率

    Returns:
        dict with full scoring breakdown
    """
    # ── 1. 获取数据 ────────────────────────────
    df = get_history(ticker, days=180)
    if df is None or len(df) < 20:
        return _empty_result(ticker, "数据不足(需20+日K)")
    df = _ensure_df(df)

    tech = calc_technical_indicators(df)
    if not tech:
        return _empty_result(ticker, "技术指标计算失败")

    close = df["Close"].values.astype(float)
    volume = df["Volume"].values.astype(float) if "Volume" in df.columns else None
    high = df["High"].values.astype(float) if "High" in df.columns else close
    low = df["Low"].values.astype(float) if "Low" in df.columns else close
    current_price = float(close[-1])
    signals = []

    # ── 2. 各维度评分 ────────────────────────
    score_s1, details_s1 = _detect_overextension(tech, close, high, current_price, signals)
    score_s2, details_s2 = _detect_volume_divergence(tech, close, volume, signals)
    score_s3, details_s3 = _score_earnings_volatility(ticker, tech, signals)
    score_s4, details_s4 = _score_valuation_bubble(ticker, tech, current_price, signals)
    score_tech, details_tech = _score_technical_short(tech, close, volume, df, signals)
    score_gap, details_gap = _premarket_gap_analysis(ticker, current_price, signals)

    # ── 3. 汇总 ──────────────────────────────
    total = score_s1 + score_s2 + score_s3 + score_s4 + score_tech + score_gap
    total = max(0, min(100, total))
    rating = _short_rating(total)

    detail = {
        "S1_overextension": {"score": score_s1, "max": 20, "detail": details_s1},
        "S2_volume_stagnation": {"score": score_s2, "max": 15, "detail": details_s2},
        "S3_earnings_volatility": {"score": score_s3, "max": 10, "detail": details_s3},
        "S4_valuation_bubble": {"score": score_s4, "max": 10, "detail": details_s4},
        "technical_bearish": {"score": score_tech, "max": 30, "detail": details_tech},
        "gap_analysis": {"score": score_gap, "max": 15, "detail": details_gap},
    }

    return {
        "ticker": ticker,
        "score": total,
        "rating": rating,
        "signals": signals,
        "detail": detail,
        "current_price": round(current_price, 2),
        "technicals": {
            "rsi14": tech.get("rsi14"),
            "sma20": tech.get("sma20"),
            "sma60": tech.get("sma60"),
            "volume_ratio": tech.get("volume_ratio"),
            "price_vs_sma20": tech.get("price_vs_sma20"),
            "macd": tech.get("macd"),
        },
        "short_score_threshold": SHORT_SCORE_THRESHOLD,
    }


def _ensure_df(df) -> pd.DataFrame:
    """Ensure data is a pandas DataFrame"""
    if not isinstance(df, pd.DataFrame):
        return pd.DataFrame(df)
    return df


def _empty_result(ticker: str, reason: str) -> dict:
    return {
        "ticker": ticker,
        "score": 0,
        "rating": "数据不足",
        "signals": [reason],
        "detail": {},
        "current_price": 0,
        "technicals": {},
        "error": reason,
        "short_score_threshold": SHORT_SCORE_THRESHOLD,
    }


# ═══════════════════════════════════════════════════════════════
# S1: 过度延伸 (0-20)
# ═══════════════════════════════════════════════════════════════

def _detect_overextension(
    tech: dict, close: np.ndarray, high: np.ndarray,
    current_price: float, signals: list,
) -> tuple:
    """检测过度延伸 — 做空信号 (0-20分)

    子因子:
      - RSI超买 (>70)          5分
      - 价格远离SMA20 (>15%)    5分
      - 价格远离SMA60 (>30%)    4分
      - 布林上轨外触碰          3分
      - 连续阳线 > 7个交易日     3分
    """
    score = 0
    detail = {}

    # ── RSI 超买 ─────────────────────────
    rsi = tech.get("rsi14")
    if rsi is not None:
        if rsi > 80:
            score += 5
            signals.append(f"🔴 RSI严重超买({rsi})")
            detail["rsi_overbought"] = "严重超买"
        elif rsi > 70:
            score += 4
            signals.append(f"RSI超买{rsi}")
            detail["rsi_overbought"] = "超买"
        elif rsi > 65:
            score += 2
            detail["rsi_overbought"] = "偏高"

    # ── 远离SMA20 ──────────────────────
    pv20 = tech.get("price_vs_sma20")
    if pv20 is not None:
        if pv20 > 25:
            score += 5
            signals.append(f"🔴 远离SMA20(+{pv20:.1f}%)")
            detail["far_from_sma20"] = round(pv20, 1)
        elif pv20 > 15:
            score += 4
            signals.append(f"远离SMA20(+{pv20:.1f}%)")
            detail["far_from_sma20"] = round(pv20, 1)
        elif pv20 > 10:
            score += 2
            detail["far_from_sma20"] = round(pv20, 1)

    # ── 远离SMA60 ──────────────────────
    sma60 = tech.get("sma60")
    if sma60 and sma60 > 0:
        pv60 = (current_price / sma60 - 1) * 100
        if pv60 > 50:
            score += 4
            signals.append(f"🔴 远高于SMA60(+{pv60:.1f}%)")
            detail["far_from_sma60"] = round(pv60, 1)
        elif pv60 > 30:
            score += 3
            signals.append(f"高于SMA60(+{pv60:.1f}%)")
            detail["far_from_sma60"] = round(pv60, 1)
        elif pv60 > 20:
            score += 2
            detail["far_from_sma60"] = round(pv60, 1)

    # ── 布林上轨 ────────────────────────
    bb_upper = tech.get("bb_upper")
    if bb_upper and current_price > bb_upper:
        score += 3
        signals.append("🔴 突破布林上轨")
        detail["bb_upper_break"] = round(current_price / bb_upper - 1, 3)

    # ── 连续上涨天数检测 ────────────────
    if len(close) >= 10:
        streaks = 0
        max_streak = 0
        for i in range(len(close) - 1, max(len(close) - 15, 0), -1):
            if close[i] > close[i - 1]:
                streaks += 1
                max_streak = max(max_streak, streaks)
            else:
                streaks = 0
        if max_streak >= 7:
            score += 3
            signals.append(f"🔴 连涨{max_streak}天(获利回吐风险)")
            detail["consecutive_up_days"] = max_streak
        elif max_streak >= 5:
            score += 2
            detail["consecutive_up_days"] = max_streak

    return min(20, score), detail


# ═══════════════════════════════════════════════════════════════
# S2: 量能背离 (0-15)
# ═══════════════════════════════════════════════════════════════

def _detect_volume_divergence(
    tech: dict, close: np.ndarray, volume: Optional[np.ndarray], signals: list,
) -> tuple:
    """检测量能背离 — 做空信号 (0-15分)

    子因子:
      - 放量滞涨 (价不涨量放大)        5分
      - 价涨量缩 (上涨缩量)            5分
      - 近期量能萎缩 (低于均值)         3分
      - OBV/MFI背离 (简化)            2分
    """
    score = 0
    detail = {}

    if volume is None or len(volume) < 25:
        return score, {"note": "volume data insufficient"}

    # ── 放量滞涨 ────────────────────────
    # 近期(5日)价格变化 vs 量能变化
    recent_ret = (close[-1] / close[-6] - 1) * 100 if len(close) >= 6 else 0
    vol_5d = float(np.mean(volume[-5:]))
    vol_20d = float(np.mean(volume[-25:-5])) if len(volume) >= 25 else vol_5d

    if vol_20d > 0:
        vol_ratio = vol_5d / vol_20d
        # 放量但价格不涨或微跌
        if vol_ratio > 1.5 and recent_ret < 1:
            score += 5
            signals.append("🔴 放量滞涨(量比{:.2f},涨幅{:.1f}%)".format(vol_ratio, recent_ret))
            detail["volume_surge_no_price"] = {"vol_ratio": round(vol_ratio, 2), "return_pct": round(recent_ret, 1)}
        # 放量下跌
        elif vol_ratio > 1.3 and recent_ret < -2:
            score += 4
            signals.append("🔴 放量下跌(量比{:.2f})".format(vol_ratio))
            detail["volume_surge_down"] = {"vol_ratio": round(vol_ratio, 2), "return_pct": round(recent_ret, 1)}

    # ── 价涨量缩 (上涨缩量) ────────────
    if len(volume) >= 10:
        vol_recent = float(np.mean(volume[-5:]))
        vol_before = float(np.mean(volume[-10:-5]))
        if vol_before > 0 and vol_recent < vol_before * 0.7 and recent_ret > 3:
            score += 5
            signals.append("🔴 上涨缩量(量萎缩{:.0f}%)".format((1 - vol_recent / vol_before) * 100))
            detail["rising_on_fading_volume"] = round(vol_recent / vol_before, 2)

    # ── 近期量能低于均值 ───────────────
    vr = tech.get("volume_ratio")
    if vr is not None:
        if vr < 0.6:
            score += 3
            signals.append("成交量低迷(量比{:.2f})".format(vr))
            detail["low_volume_ratio"] = round(vr, 2)
        elif vr < 0.8:
            score += 1

    # ── 简化OBV背离检测 ────────────────
    # 价格创新高但量没跟上 (近10日)
    if len(close) >= 15 and len(volume) >= 15:
        recent_close = close[-10:]
        recent_vol = volume[-10:]
        if np.max(recent_close) == close[-1]:  # 价格创10日新高
            vol_at_high = float(recent_vol[np.argmax(recent_close)])
            avg_vol_10d = float(np.mean(recent_vol))
            if avg_vol_10d > 0 and vol_at_high < avg_vol_10d * 0.8:
                score += 2
                signals.append("🔴 OBV背离(新高无量)")
                detail["obv_divergence"] = round(vol_at_high / avg_vol_10d, 2)

    return min(15, score), detail


# ═══════════════════════════════════════════════════════════════
# S3: 财报前波动 (0-10)
# ═══════════════════════════════════════════════════════════════

def _score_earnings_volatility(ticker: str, tech: dict, signals: list) -> tuple:
    """财报前波动评分 (0-10)

    财报即将发布 + 波动率扩张 = 做空窗口
    子因子:
      - 财报14天内发布         5分
      - ATR异常扩张 (> 5%)     3分
      - 隐含波动率膨胀信号      2分
    """
    score = 0
    detail = {}
    today = datetime.now()

    # ── 财报日期检测 ────────────────────
    try:
        import yfinance as yf
        tk = yf.Ticker(ticker)
        cal = tk.calendar
        if cal is not None:
            try:
                is_empty = cal.empty if hasattr(cal, 'empty') else False
            except Exception:
                is_empty = True
            if is_empty:
                cal = None
        if cal is not None:
            earnings = cal.get("Earnings Date")
            if earnings is not None:
                if hasattr(earnings, '__iter__') and not isinstance(earnings, str):
                    next_date = earnings.iloc[0] if hasattr(earnings, 'iloc') else earnings[0]
                else:
                    next_date = earnings

                if not hasattr(next_date, 'date'):
                    try:
                        next_date = datetime.fromisoformat(str(next_date))
                    except Exception:
                        next_date = None

                if next_date is not None and hasattr(next_date, 'date'):
                    days_to = (next_date - today).days
                    if 0 <= days_to <= 3:
                        score += 5
                        signals.append(f"📅 {days_to}天后财报(爆发窗口)")
                        detail["earnings_days"] = days_to
                    elif 4 <= days_to <= 7:
                        score += 4
                        signals.append(f"{days_to}天后财报(逼近)")
                        detail["earnings_days"] = days_to
                    elif 8 <= days_to <= 14:
                        score += 3
                        signals.append(f"{days_to}天后财报")
                        detail["earnings_days"] = days_to
    except ImportError:
        logger.debug("yfinance not available for earnings calendar")
    except Exception as e:
        logger.debug(f"Earnings fetch fail {ticker}: {e}")

    # ── ATR波动率检测 ──────────────────
    atr = tech.get("atr14")
    cp = tech.get("current_price", 1)
    if atr and cp > 0:
        atr_pct = (atr / cp) * 100
        if atr_pct > 8:
            score += 3
            signals.append(f"📊 ATR极高({atr_pct:.1f}%)(波动风险)")
            detail["atr_pct"] = round(atr_pct, 1)
        elif atr_pct > 5:
            score += 2
            signals.append(f"ATR偏高({atr_pct:.1f}%)")
            detail["atr_pct"] = round(atr_pct, 1)
        elif atr_pct > 3.5:
            score += 1
            detail["atr_pct"] = round(atr_pct, 1)

    return min(10, score), detail


# ═══════════════════════════════════════════════════════════════
# S4: 估值泡沫 (0-10)
# ═══════════════════════════════════════════════════════════════

def _score_valuation_bubble(
    ticker: str, tech: dict, current_price: float, signals: list,
) -> tuple:
    """估值泡沫评分 (0-10)

    子因子:
      - PE > 100 (或无PE) + 高增长预期破灭   5分
      - 市净率PB > 20                          3分
      - 价格脱离基本面 (无营收支撑)            2分
    """
    score = 0
    detail = {}

    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info
        pe = info.get("trailingPE") or info.get("forwardPE")
        pb = info.get("priceToBook")
        mkt_cap = info.get("marketCap", 0)
        revenue = info.get("totalRevenue", 0)

        # ── PE极端 ──────────────────────────
        if pe is not None and pe > 0:
            if pe > 200:
                score += 5
                signals.append(f"💰 PE极端({pe:.0f})(泡沫风险)")
                detail["pe"] = round(pe, 1)
            elif pe > 100:
                score += 4
                signals.append(f"PE极高({pe:.0f})")
                detail["pe"] = round(pe, 1)
            elif pe > 60:
                score += 2
                signals.append(f"PE偏高({pe:.0f})")
                detail["pe"] = round(pe, 1)
        else:
            # 无PE + 未盈利 = 高风险
            if revenue == 0 or (revenue and mkt_cap > 0 and mkt_cap / revenue > 50):
                score += 3
                signals.append("💰 未盈利高估值")
                detail["no_pe_premium"] = True

        # ── PB极端 ──────────────────────────
        if pb is not None and pb > 0:
            if pb > 30:
                score += 3
                signals.append(f"PB极高({pb:.1f})")
                detail["pb"] = round(pb, 1)
            elif pb > 15:
                score += 2
                detail["pb"] = round(pb, 1)

        # ── 价格/营收比暴高 ──────────────
        if mkt_cap > 0 and revenue > 0:
            ps = mkt_cap / revenue
            if ps > 50:
                score += 2
                signals.append(f"市销率极高({ps:.1f})")
                detail["ps"] = round(ps, 1)
            elif ps > 20:
                score += 1
                detail["ps"] = round(ps, 1)

    except ImportError:
        logger.debug("yfinance not available for valuation")
    except Exception as e:
        logger.debug(f"Valuation fetch fail {ticker}: {e}")

    return min(10, score), detail


# ═══════════════════════════════════════════════════════════════
# 技术面偏空 (0-30)
# ═══════════════════════════════════════════════════════════════

def _score_technical_short(
    tech: dict, close: np.ndarray, volume: Optional[np.ndarray],
    df: pd.DataFrame, signals: list,
) -> tuple:
    """技术面偏空评分 (0-30)

    子因子:
      - MA空头排列/死叉           8分
      - MACD死叉/绿柱放大         7分
      - 下跌趋势 (连跌/低点更低)   6分
      - RSI下跌趋势              4分
      - 支撑位破位                3分
      - 高位放巨量长上影          2分
    """
    score = 0
    detail = {}

    # ── MA排列 (8分) ──────────────────────
    sma5 = tech.get("sma5")
    sma20 = tech.get("sma20")
    sma60 = tech.get("sma60")

    if sma5 and sma20 and sma60:
        if sma5 < sma20 < sma60:
            score += 8
            signals.append("📉 MA空头排列(5<20<60)")
            detail["ma_arrangement"] = "full_bearish"
        elif sma5 < sma20:
            score += 5
            signals.append("MA短期空头(5<20)")
            detail["ma_arrangement"] = "short_bearish"
        elif sma20 < sma60:
            score += 3
            detail["ma_arrangement"] = "mid_bearish"

    # ── MACD (7分) ──────────────────────
    macd = tech.get("macd")
    if macd:
        macd_line = macd.get("macd", 0)
        signal = macd.get("signal", 0)
        hist = macd.get("hist", 0)
        # MACD死叉 (MACD < Signal)
        if macd_line < signal and hist < 0:
            if hist < -2:
                score += 7
                signals.append(f"📉 MACD死叉(绿柱{hist})")
                detail["macd"] = "death_cross_strong"
            else:
                score += 5
                signals.append("MACD死叉")
                detail["macd"] = "death_cross"
        elif macd_line < 0:
            score += 3
            signals.append("MACD零轴下方")
            detail["macd"] = "below_zero"
        elif hist < 0 and hist > -0.5:
            score += 2
            detail["macd"] = "histogram_slight_bearish"

    # ── 下跌趋势 (6分) ──────────────────
    if len(close) >= 20:
        ret_1m = (close[-1] / close[-20] - 1) * 100
        ret_10d = (close[-1] / close[-10] - 1) * 100 if len(close) >= 10 else 0

        if ret_10d < -5 and ret_1m < -10:
            score += 6
            signals.append(f"📉 下跌趋势(10日{ret_10d:.1f}%/20日{ret_1m:.1f}%)")
            detail["downtrend"] = {"ret_10d": round(ret_10d, 1), "ret_20d": round(ret_1m, 1)}
        elif ret_10d < -3:
            score += 4
            signals.append(f"近10日下跌{ret_10d:.1f}%")
            detail["downtrend"] = {"ret_10d": round(ret_10d, 1)}
        elif ret_1m < -5:
            score += 3
            detail["downtrend"] = {"ret_20d": round(ret_1m, 1)}

        # 更低低点检测 (lower lows)
        if len(close) >= 40:
            low_30d = float(np.min(close[-30:]))
            low_60d = float(np.min(close[-60:-30]))
            if low_60d > 0 and low_30d < low_60d * 0.97:
                score += 2
                signals.append("创30日新低")
                detail["lower_low"] = "30日低点 < 60日低点"

    # ── RSI趋势 (4分) ────────────────────
    rsi = tech.get("rsi14")
    if rsi is not None:
        if rsi < 30:
            # 超卖区但仍在下跌 = 极度弱势
            score += 4
            signals.append(f"RSI跌入超卖区({rsi})")
            detail["rsi_trend"] = "oversold(weak)"
        elif rsi < 40:
            score += 3
            signals.append(f"RSI弱势({rsi})")
            detail["rsi_trend"] = "weak"

        # RSI下降趋势检测
        if len(close) >= 30:
            # 简化: 当前RSI低于14日前
            try:
                df_sub = df.tail(28).copy()
                tech_14d_ago = calc_technical_indicators(df_sub)
                if tech_14d_ago and tech_14d_ago.get("rsi14") is not None:
                    rsi_before = tech_14d_ago["rsi14"]
                    if rsi < rsi_before - 10:
                        score += 2
                        detail["rsi_decline"] = round(rsi_before - rsi, 1)
            except Exception:
                pass

    # ── 支撑位破位 (3分) ─────────────────
    sma20_val = tech.get("sma20")
    sma60_val = tech.get("sma60")
    current_price = float(close[-1])
    if sma20_val and current_price < sma20_val:
        score += 2
        signals.append("📉 跌破SMA20支撑")
        detail["support_break"] = "sma20"
    if sma60_val and current_price < sma60_val:
        score += 1
        detail["support_break"] = "sma60"

    # ── 高位放量长上影 (2分) ────────────
    if len(close) >= 5 and "High" in df.columns and "Open" in df.columns:
        hvals = df["High"].values.astype(float)
        last = -1
        upper_shadow = float(hvals[last]) - max(float(close[last]), float(df["Open"].values[last]))
        body = abs(float(close[last]) - float(df["Open"].values[last]))
        if body > 0 and upper_shadow / body > 2 and upper_shadow > current_price * 0.03:
            score += 2
            signals.append("📉 高位长上影线(抛压)")
            detail["long_upper_shadow"] = round(upper_shadow / body, 1)

    return min(30, score), detail


# ═══════════════════════════════════════════════════════════════
# 缺口分析 (0-15)
# ═══════════════════════════════════════════════════════════════

def _premarket_gap_analysis(
    ticker: str, current_price: float, signals: list,
) -> tuple:
    """盘前/盘后缺口分析 — 做空加分 (0-15分)

    子因子:
      - 盘前大幅跳空高开 (>3%)       5分 (跳空衰竭做空)
      - 盘后回落 (高开低走)          4分
      - 岛形反转形态                 3分
      - 盘中缺口回补概率高           3分
    """
    score = 0
    detail = {}

    # ── 盘前跳空检测 ────────────────────
    try:
        pp = get_pre_post_market(extra=[ticker])
        if pp and "top_movers" in pp:
            mover = None
            for m in pp["top_movers"]:
                if m.get("ticker") == ticker:
                    mover = m
                    break

            if mover:
                pre_chg = mover.get("change_pct", 0)
                if pre_chg and isinstance(pre_chg, (int, float)):
                    # 跳空高开 - 可能衰竭
                    if pre_chg > 5:
                        score += 5
                        signals.append(f"🔴 盘前跳空+{pre_chg:.1f}%(衰竭风险)")
                        detail["premarket_gap"] = round(pre_chg, 1)
                    elif pre_chg > 3:
                        score += 4
                        signals.append(f"盘前跳空+{pre_chg:.1f}%(关注衰竭)")
                        detail["premarket_gap"] = round(pre_chg, 1)
                    elif pre_chg > 2:
                        score += 2
                        detail["premarket_gap"] = round(pre_chg, 1)

                    # 盘前高开低走信号 (price vs previous close对比)
                    pp_price = mover.get("price", current_price)
                    prev_close = mover.get("change")
                    if isinstance(prev_close, (int, float)) and prev_close != 0:
                        implied_gap_pct = (pp_price / (pp_price - prev_close) - 1) * 100 \
                            if abs(prev_close) > 0 else 0
                        # 简化的盘后回落检测
                        if pre_chg > 2 and implied_gap_pct < 0:
                            score += 4
                            signals.append("🔴 盘后回落(高开低走)")
                            detail["postmarket_fade"] = round(implied_gap_pct, 1)
    except Exception as e:
        logger.debug(f"Pre-market gap fetch fail {ticker}: {e}")

    # ── 岛形反转形态检测 (简单版) ──────
    # 跳空上涨后立即跳空下跌 = 岛形
    # 需要日线数据, 这里用简化方法
    try:
        df = get_history(ticker, days=15)
        if df is not None and len(df) >= 5:
            if not isinstance(df, pd.DataFrame):
                df = pd.DataFrame(df)
            close_arr = df["Close"].values.astype(float)
            open_arr = df["Open"].values.astype(float) if "Open" in df.columns else close_arr
            high_arr = df["High"].values.astype(float) if "High" in df.columns else close_arr
            low_arr = df["Low"].values.astype(float) if "Low" in df.columns else close_arr

            if len(close_arr) >= 5:
                # 检测: 最近2天有跳空缺口 (今日最低 > 昨日最高 = 向上跳空)
                # 然后今日低开跌破昨日收盘 = 反转
                gap_up = low_arr[-1] > high_arr[-2] if len(close_arr) >= 2 else False
                gap_down = high_arr[-1] < low_arr[-2] if len(close_arr) >= 2 else False
                if gap_up and (close_arr[-1] < open_arr[-1]):  # 跳空高开但收阴
                    score += 3
                    signals.append("🔴 跳空高开收阴(反转信号)")
                    detail["island_reversal"] = "gap_up_bear_day"
                elif gap_down and (close_arr[-1] < open_arr[-1]):  # 跳空低开继续跌
                    score += 2
                    detail["island_reversal"] = "gap_down_continue"

                # 连续跳空衰竭 (连跳2天以上)
                gaps = 0
                for i in range(min(5, len(close_arr) - 1)):
                    if low_arr[-(i+1)] > high_arr[-(i+2)]:
                        gaps += 1
                    else:
                        break
                if gaps >= 2:
                    extra = score  # avoid double counting
                    add = max(0, 3 - extra)
                    if add > 0:
                        score += add
                        signals.append(f"🔴 连续{gaps}天向上跳空(衰竭风险)")
                        detail["consecutive_gaps"] = gaps
    except Exception as e:
        logger.debug(f"Island reversal detect fail {ticker}: {e}")

    # ── 缺口回补概率 (参考历史) ────────
    try:
        df_full = get_history(ticker, days=60)
        if df_full is not None and len(df_full) >= 10:
            if not isinstance(df_full, pd.DataFrame):
                df_full = pd.DataFrame(df_full)
            high_arr = df_full["High"].values.astype(float)
            low_arr = df_full["Low"].values.astype(float)

            # 检测短期历史中跳空缺口是否频繁回补
            recent_gaps = 0
            gaps_filled = 0
            for i in range(2, min(20, len(high_arr))):
                # 向上跳空
                if low_arr[-i] > high_arr[-(i+1)]:
                    recent_gaps += 1
                    # 检查是否后续回补 (3日内价格跌回缺口区间)
                    gap_top = high_arr[-(i+1)]
                    gap_bottom = low_arr[-i]
                    for j in range(1, min(4, i)):
                        if low_arr[-(i-j)] <= gap_top <= high_arr[-(i-j)]:
                            gaps_filled += 1
                            break
            if recent_gaps > 0 and gaps_filled / recent_gaps > 0.5:
                score += 3
                signals.append("历史缺口回补率高(做空有利)")
                detail["gap_fill_rate"] = round(gaps_filled / recent_gaps, 2)
    except Exception as e:
        logger.debug(f"Gap fill analysis fail {ticker}: {e}")

    return min(15, score), detail


# ═══════════════════════════════════════════════════════════════
# 评级
# ═══════════════════════════════════════════════════════════════

def _short_rating(score: int) -> str:
    """做空评级"""
    if score >= 85:
        return "强烈做空"
    elif score >= SHORT_SCORE_THRESHOLD:
        return "建议做空"
    elif score >= 40:
        return "关注"
    elif score >= 20:
        return "轻微"
    return "观望"


# ═══════════════════════════════════════════════════════════════
# 公开接口
# ═══════════════════════════════════════════════════════════════

def find_short_opportunities(tickers: List[str]) -> List[dict]:
    """主入口 — 对一组ticker进行做空评分, 返回按分数降序排列

    Args:
        tickers: 美股代码列表

    Returns:
        按做空分数降序的评分结果列表
    """
    results = []
    for t in tickers:
        try:
            r = _short_score(t)
            if r.get("score", 0) > 0:
                results.append(r)
        except Exception as e:
            logger.warning(f"Short scoring failed for {t}: {e}")
            continue

    results.sort(key=lambda x: x.get("score", 0), reverse=True)
    return results


def scan_short_candidates(tickers: List[str]) -> dict:
    """批量扫描做空候选 — 返回结构化报告

    与 find_short_opportunities 的区别:
      - 额外汇总统计 (平均分/通过率)
      - 阈值过滤 (只返回分数 >= SHORT_SCORE_THRESHOLD 的)
      - 含各维度分布统计

    Args:
        tickers: 美股代码列表

    Returns:
        dict with candidates, metadata, dimension breakdown
    """
    results = []
    passed = []

    # 并行获取 (控制并发数)
    with ThreadPoolExecutor(max_workers=5) as pool:
        fut_map = {pool.submit(_short_score, t): t for t in tickers}
        for fut in as_completed(fut_map):
            try:
                r = fut.result()
                if r.get("score", 0) > 0:
                    results.append(r)
                    if r["score"] >= SHORT_SCORE_THRESHOLD:
                        passed.append(r)
            except Exception as e:
                t = fut_map[fut]
                logger.debug(f"Scan fail {t}: {e}")

    results.sort(key=lambda x: x.get("score", 0), reverse=True)
    passed.sort(key=lambda x: x.get("score", 0), reverse=True)

    # ── 维度统计 ────────────────────────
    dim_keys = ["S1_overextension", "S2_volume_stagnation",
                "S3_earnings_volatility", "S4_valuation_bubble",
                "technical_bearish", "gap_analysis"]
    dim_max = {"S1_overextension": 20, "S2_volume_stagnation": 15,
               "S3_earnings_volatility": 10, "S4_valuation_bubble": 10,
               "technical_bearish": 30, "gap_analysis": 15}

    dim_stats = {}
    for dk in dim_keys:
        scores = [r.get("detail", {}).get(dk, {}).get("score", 0) for r in results]
        max_v = dim_max.get(dk, 10)
        dim_stats[dk] = {
            "avg": round(float(np.mean(scores)), 1) if scores else 0,
            "max": max_v,
            "avg_pct": round(float(np.mean(scores)) / max_v * 100, 1) if (scores and max_v > 0) else 0,
            "count_gt_half": sum(1 for s in scores if s >= max_v * 0.5),
        }

    scores_list = [r.get("score", 0) for r in results]
    total_analyzed = len(tickers)

    return {
        "candidates": passed,
        "all_results": results,
        "metadata": {
            "total_analyzed": total_analyzed,
            "total_scored": len(results),
            "passed_threshold": len(passed),
            "threshold": SHORT_SCORE_THRESHOLD,
            "avg_score": round(float(np.mean(scores_list)), 1) if scores_list else 0,
            "max_score": max(scores_list) if scores_list else 0,
            "min_score": min(scores_list) if scores_list else 0,
        },
        "dimension_stats": dim_stats,
        "rating_distribution": {
            "强烈做空(85+)": sum(1 for s in scores_list if s >= 85),
            "建议做空(60-84)": sum(1 for s in scores_list if SHORT_SCORE_THRESHOLD <= s < 85),
            "关注(40-59)": sum(1 for s in scores_list if 40 <= s < SHORT_SCORE_THRESHOLD),
            "轻微(20-39)": sum(1 for s in scores_list if 20 <= s < 40),
            "观望(<20)": sum(1 for s in scores_list if s < 20),
        },
    }


# ═══════════════════════════════════════════════════════════════
# 独立运行调试
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    import sys
    test_tickers = sys.argv[1:] if len(sys.argv) > 1 else [
        "TSLA", "NVDA", "META", "AMD", "PLTR", "COIN", "MSTR"
    ]

    print(f"🔍 做空机会扫描: {', '.join(test_tickers)}")
    print("=" * 60)

    for t in test_tickers:
        r = _short_score(t)
        s = r.get("score", 0)
        rt = r.get("rating", "?")
        pr = r.get("current_price", 0)
        sigs = r.get("signals", [])
        detail = r.get("detail", {})

        print(f"\n{'─' * 50}")
        print(f"{t:8s} | 评分: {s:3d}/100 | {rt} | ${pr}")
        print(f"{'─' * 50}")

        for dk, dv in detail.items():
            if isinstance(dv, dict):
                sc = dv.get("score", 0)
                mx = dv.get("max", "?")
                print(f"  {dk:25s}: {sc:2d}/{mx}")
            else:
                print(f"  {dk:25s}: {dv}")
        for sig in sigs:
            print(f"  {'':>3s}{sig}")
