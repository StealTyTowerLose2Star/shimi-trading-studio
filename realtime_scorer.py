"""拾米交易工作室 - 真实策略评分引擎
从三个 GitHub 仓库提取的官方评分公式，适配 tushare 数据源
"""
import pandas as pd
import numpy as np
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple

import sys, os
sys.path.insert(0, os.path.dirname(__file__))
import config
from data.fetcher_core import get_ts




# 进程级 K 线缓存：{code_days: (DataFrame, timestamp)}
_kline_cache = {}
_KLINE_TTL = 1800  # 缓存 1800 秒（30 分钟）


def get_kline(code: str, days: int = 120, force: bool = False):
    """获取个股 K 线 (tushare)，带进程级缓存（1800 秒 TTL）
    Args:
        code: 股票代码
        days: 获取天数
        force: 是否强制刷新（跳过缓存）
    """
    import time
    cache_key = f"{code}_{days}"
    if not force and cache_key in _kline_cache:
        df, ts = _kline_cache[cache_key]
        if time.time() - ts < _KLINE_TTL:
            return df
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
        if df.empty or len(df) < max(days * 0.4, 10):
            _kline_cache[cache_key] = (None, time.time())
            return None
        df = df.sort_values("trade_date").reset_index(drop=True)
        df = df.rename(columns={"trade_date": "date", "vol": "volume"})
        df["date"] = pd.to_datetime(df["date"])
        if "pct_chg" not in df.columns:
            df["pct_chg"] = df["close"].pct_change() * 100
        _kline_cache[cache_key] = (df, time.time())
        return df
    except Exception:
        _kline_cache[cache_key] = (None, time.time())
        return None


def get_kline_batch(codes: list, days: int = 120) -> dict:
    """并行批量获取多只股票 K 线"""
    results = {}
    with ThreadPoolExecutor(max_workers=10) as ex:
        fut = {ex.submit(get_kline, c, days): c for c in codes}
        for f in as_completed(fut):
            results[fut[f]] = f.result()
    return results


def _native(val):
    """Convert numpy values to native Python types"""
    if isinstance(val, (np.integer, np.floating, np.bool_)):
        return val.item()
    if isinstance(val, dict):
        return {k: _native(v) for k, v in val.items()}
    if isinstance(val, (list, tuple)):
        return [_native(v) for v in val]
    return val


# ============================================================
# 1. 趋势策略 — TrendDetector
# ============================================================

def trend_detect(code: str):
    """完整趋势检测 — 官方 TrendDetector 评分公式"""
    df = get_kline(code, days=120)
    if df is None or len(df) < 60:
        return None

    close, high, low = df["close"], df["high"], df["low"]
    volume = df["volume"]

    ma5 = close.rolling(5).mean().iloc[-1]
    ma10 = close.rolling(10).mean().iloc[-1]
    ma20 = close.rolling(20).mean().iloc[-1]
    ma60 = close.rolling(60).mean().iloc[-1]
    ma_score = int((ma5 > ma10) * 25 + (ma10 > ma20) * 25 + (ma20 > ma60) * 25 + (ma5 > ma60) * 25)

    prev_high = high.iloc[-21:-1].max()
    bpct = max(0, (close.iloc[-1] - prev_high) / prev_high * 100)
    brk_score = 100 if bpct > 5 else (80 if bpct > 3 else (60 if bpct > 0 else 0))

    vol_ratio = float(volume.iloc[-1] / max(volume.iloc[-6:-1].mean(), 1))
    vol_score = 100 if vol_ratio >= 2.0 else (80 if vol_ratio >= 1.5 else (60 if vol_ratio >= 1.2 else 40))

    slope = (ma20 - close.rolling(20).mean().iloc[-6]) / max(close.rolling(20).mean().iloc[-6], 0.01) * 100
    slp_score = 100 if slope > 5 else (80 if slope > 2 else (60 if slope > 0 else 20))

    total = ma_score * 0.30 + brk_score * 0.25 + vol_score * 0.20 + slp_score * 0.25
    strength = "强趋势" if total >= 80 else ("中等趋势" if total >= 60 else ("弱趋势" if total >= 40 else "无趋势"))

    low120, high120 = float(low.iloc[-120:].min()), float(high.iloc[-120:].max())
    stage_pct = (float(close.iloc[-1]) - low120) / max(high120 - low120, 0.01) * 100
    stage = "鱼头期" if stage_pct < 30 else ("鱼身期" if stage_pct < 70 else ("鱼身末期" if stage_pct < 90 else "鱼尾期"))
    trend_formed = bool(ma_score == 100 and bpct > 0 and vol_ratio >= 1.5 and slope > 0)

    return _native({
        "code": code, "price": float(close.iloc[-1]),
        "total_score": round(total, 1), "strength": strength,
        "stage": stage, "stage_pct": round(stage_pct, 1),
        "trend_formed": trend_formed,
        "dimensions": {"ma_score": ma_score, "breakout_score": brk_score,
                       "volume_score": vol_score, "slope_score": slp_score,
                       "ma5": round(float(ma5),2), "ma10": round(float(ma10),2),
                       "ma20": round(float(ma20),2), "ma60": round(float(ma60),2),
                       "volume_ratio": round(vol_ratio,2)}
    })


# ============================================================
# 2. 混合策略 — MergedScorer 7维
# ============================================================

def hybrid_score(code: str, industry: str = ""):
    """混合策略真实评分 — 7维度 + 动量爆发加成"""
    df = get_kline(code, days=120)
    if df is None or len(df) < 60:
        return None

    close_f = float(df["close"].iloc[-1])
    pct_chg = float(df["pct_chg"].iloc[-1])
    high, low, volume = df["high"], df["low"], df["volume"]

    ma5 = df["close"].rolling(5).mean().iloc[-1]
    ma10 = df["close"].rolling(10).mean().iloc[-1]
    ma20 = df["close"].rolling(20).mean().iloc[-1]
    d1 = int((ma5 > ma10) * 25 + (ma10 > ma20) * 25 + (ma20 > low.iloc[-120:].min()) * 25 + (df["close"].iloc[-1] > ma20) * 25)

    achg = abs(pct_chg)
    d2 = 100 if achg > 9.5 else (80 if achg > 7 else (65 if achg > 5 else (50 if achg > 3 else (35 if achg > 1 else 20))))

    vr = float(volume.iloc[-1] / max(volume.iloc[-6:-1].mean(), 1))
    d5 = 100 if vr > 3 else (85 if vr > 2 else (65 if vr > 1.5 else (45 if vr > 1.0 else 25)))

    low20, high20 = float(low.iloc[-20:].min()), float(high.iloc[-20:].max())
    pos = (close_f - low20) / max(high20 - low20, 0.01) * 100
    d6 = 80 if 30 <= pos <= 70 else (60 if 20 <= pos <= 80 else (40 if 10 <= pos <= 90 else 20))

    d7, d8 = 60, 50

    burst = 0
    if pct_chg >= 5: burst += 1
    if vr >= 2.0: burst += 1
    if df["close"].iloc[-1] > high.iloc[-20:-1].max(): burst += 1
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rsi = 100 - 100 / (1 + gain.iloc[-1] / max(loss.iloc[-1], 0.001))
    if 50 <= rsi <= 70: burst += 1
    burst_bonus = 8 if burst >= 3 else 0

    technical = d1 * 0.15 + d2 * 0.25 + d5 * 0.15 + d6 * 0.08 + d7 * 0.22 + d8 * 0.15 + burst_bonus
    technical = min(100, technical)
    gl = "S+" if technical >= 55 else ("S" if technical >= 45 else ("A" if technical >= 35 else ("B" if technical >= 25 else ("C" if technical >= 15 else "D"))))
    pos_pct = {"S+":33,"S":25,"A":20,"B":15,"C":10,"D":0}.get(gl, 0)

    return _native({
        "code": code, "price": close_f, "pct_chg": round(pct_chg, 2),
        "score": round(technical, 1), "grade": gl, "position_pct": pos_pct,
        "dimensions": {"d1_trend": d1, "d2_momentum": d2, "d5_volume": d5,
                       "d6_safety": d6, "d7_sector": d7, "d8_persist": d8,
                       "burst_bonus": burst_bonus, "vol_ratio": round(vr,2), "rsi": round(rsi,1)}
    })


# ============================================================
# 3. 龙头战法 — LeaderScorer 5维
# ============================================================

def _count_consecutive_limit(df, min_pct=9.0):
    """从历史数据推算连板天数"""
    vals = df["pct_chg"].tolist()[-21:]
    count = 0
    for v in reversed(vals):
        if v >= min_pct: count += 1
        else: break
    return count


def _estimate_block_time(df):
    """从涨幅和成交量推断涨停时间"""
    pct_chg = float(df["pct_chg"].iloc[-1])
    vr = float(df["volume"].iloc[-1] / max(df["volume"].iloc[-6:-1].mean(), 1))
    if pct_chg >= 9.5:
        if vr < 0.8: return "09:35"
        elif vr < 1.2: return "09:50"
        elif vr < 2.0: return "10:30"
        else: return "14:00"
    return "15:00"


def _block_time_to_minutes(bt: str) -> int:
    try:
        h, m = bt.split(":")
        return int(h) * 60 + int(m)
    except Exception: return 570


def dragon_leader_score(code: str, extra: dict = None):
    """龙头评分 — 5维评分 (官方 LeaderScorer 公式)"""
    df = get_kline(code, days=60)
    if df is None or len(df) < 20: return None

    close_f = float(df["close"].iloc[-1])
    pct_chg = float(df["pct_chg"].iloc[-1])
    extra = extra or {}

    bc = extra.get("board_count", 0)
    if bc <= 0: bc = _count_consecutive_limit(df)
    if bc <= 0: bc = 1
    cs = 100 if bc >= 7 else (80 if bc >= 5 else (60 if bc >= 3 else (40 if bc >= 2 else 20)))

    bt = extra.get("block_time", "") or _estimate_block_time(df)
    mins = _block_time_to_minutes(bt)
    ts = 100 if mins <= 575 else (70 if mins <= 585 else (40 if mins <= 600 else 20))

    sa = extra.get("sealed_amount", 0)
    if sa <= 0:
        vr = float(df["volume"].iloc[-1] / max(df["volume"].iloc[-6:-1].mean(), 1))
        sa = 5.0 if vr < 0.5 else (2.0 if vr < 1.0 else (1.0 if vr < 2.0 else 0.3))
    ds = 100 if sa >= 3 else (80 if sa >= 1 else (60 if sa >= 0.5 else 40))

    smax = extra.get("sector_max_board", bc)
    sr = 100 if bc >= smax and smax >= 3 else (80 if bc >= smax - 1 else (60 if bc >= smax - 2 else 40))

    max_dd = 0
    for i in range(max(1, len(df) - 6), len(df)):
        dd = (df["close"].iloc[i] - df["close"].iloc[i-1]) / df["close"].iloc[i-1] * 100
        if dd < max_dd: max_dd = dd
    rs = 80 if max_dd > -3 else (60 if max_dd > -7 else 40)

    total = sr * 0.35 + cs * 0.25 + ts * 0.20 + ds * 0.15 + rs * 0.05
    grade = "总龙头" if total >= 80 else ("主线龙头" if total >= 60 else ("板块龙头" if total >= 40 else "补涨龙"))

    return _native({
        "code": code, "price": close_f, "pct_chg": round(pct_chg, 2),
        "leader_score": round(total), "grade": grade, "board_count": bc,
        "dimensions": {"sector_rank": sr, "consecutive": cs, "limit_time": ts,
                       "drive_effect": ds, "resistance": rs,
                       "block_time": bt, "sealed_amount": round(sa, 2)}
    })


# ============================================================
# 4. 多均线重合判断 — MA Convergence Score
# ============================================================

def ma_convergence_score(code: str) -> dict:
    """多均线重合/趋近判断

    检测 MA5/MA10/MA20/MA60 是否处于收敛状态（均线间距缩小）+ 同频向上。
    这是股价突破前的重要信号：均线由发散→收敛→向上发散。

    Returns:
        dict: {
            "score": 0-100,  越高越好
            "converged": bool,  是否已重合
            "converging": bool,  是否正在趋近
            "all_up": bool,     各MA是否同频向上
            "gap_pct": float,   间距百分比
            "gap_trend": str,   间距趋势
            "detail": str       文字描述
        }
    """
    df = get_kline(code, days=120)
    if df is None or len(df) < 60:
        return {"score": 0, "converged": False, "converging": False,
                "all_up": False, "detail": "数据不足"}

    close = df["close"]
    ma5s = close.rolling(5).mean()
    ma10s = close.rolling(10).mean()
    ma20s = close.rolling(20).mean()
    ma60s = close.rolling(60).mean()

    ma5, ma10, ma20, ma60 = ma5s.iloc[-1], ma10s.iloc[-1], ma20s.iloc[-1], ma60s.iloc[-1]
    if pd.isna(ma60):
        return {"score": 20, "converged": False, "converging": False,
                "all_up": False, "detail": "上市不足60天"}

    gap_10_20 = abs(ma10 - ma20) / ma20 * 100 if ma20 > 0 else 0
    gap_5_10 = abs(ma5 - ma10) / ma10 * 100 if ma10 > 0 else 0
    avg_gap = (gap_10_20 + gap_5_10) / 2

    gap_10_20_prev = abs(ma10s.iloc[-6] - ma20s.iloc[-6]) / ma20s.iloc[-6] * 100 if ma20s.iloc[-6] > 0 else 0
    gap_5_10_prev = abs(ma5s.iloc[-6] - ma10s.iloc[-6]) / ma10s.iloc[-6] * 100 if ma10s.iloc[-6] > 0 else 0
    avg_gap_prev = (gap_10_20_prev + gap_5_10_prev) / 2

    gap_narrowing = avg_gap < avg_gap_prev * 0.9
    gap_stable = avg_gap <= 3.0

    def _ma_slope(ma_series, period=5):
        if len(ma_series) < period + 1:
            return 0
        return (ma_series.iloc[-1] - ma_series.iloc[-period-1]) / ma_series.iloc[-period-1] * 100

    slp5 = _ma_slope(ma5s)
    slp10 = _ma_slope(ma10s)
    slp20 = _ma_slope(ma20s)
    slopes = [slp5, slp10, slp20]
    all_up = all(s > 0.1 for s in slopes)
    avg_slope = sum(slopes) / len(slopes)

    score = 0
    parts = []

    if gap_stable:
        score += 40
        parts.append("均线已重合")
    elif gap_narrowing:
        score += 25
        parts.append(f"间距收敛({avg_gap:.1f}%)")
    elif avg_gap < 5:
        score += 15
        parts.append(f"间距适中({avg_gap:.1f}%)")
    else:
        parts.append(f"间距偏大({avg_gap:.1f}%)")

    if all_up:
        score += 35
        parts.append("同频向上")
    elif avg_slope > 0:
        score += 15
        parts.append("MA方向有分歧")
    else:
        parts.append("MA向下")

    if gap_narrowing and all_up:
        score += 25
        parts.append("收敛+同频↑最优形态")
    elif gap_narrowing:
        score += 10

    score = min(100, max(0, score))

    return {
        "score": score,
        "converged": gap_stable and all_up,
        "converging": gap_narrowing,
        "all_up": all_up,
        "avg_slope": round(avg_slope, 2),
        "gap_pct": round(avg_gap, 2),
        "gap_trend": "收敛" if gap_narrowing else ("发散" if avg_gap > avg_gap_prev * 1.1 else "持平"),
        "ma5": round(float(ma5), 2), "ma10": round(float(ma10), 2),
        "ma20": round(float(ma20), 2), "ma60": round(float(ma60), 2),
        "detail": " · ".join(parts) if parts else "未形成有效形态",
    }


# ============================================================
# 5. 多周期MACD分析
# ============================================================

def macd_analysis(code: str) -> dict:
    """多周期MACD金叉/趋近金叉分析

    同时分析日线(20天)和周线(60天≈12周)两个周期的MACD状态。

    Returns:
        dict: {
            "daily_golden_cross": bool,    日线是否金叉
            "daily_approaching": bool,     日线趋近金叉
            "weekly_golden_cross": bool,   周线是否金叉
            "weekly_approaching": bool,    周线趋近金叉
            "multi_period_bullish": bool,  多周期共振看多
            "score": 0-100,               综合评分
            "signal": str                 信号描述
        }
    """
    df = get_kline(code, days=150)
    if df is None or len(df) < 60:
        return {"score": 0, "signal": "数据不足"}

    close = df["close"]

    # ─── 日线MACD ───
    ema12 = close.ewm(span=12).mean()
    ema26 = close.ewm(span=26).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9).mean()
    hist = (dif - dea) * 2

    d_dif = float(dif.iloc[-1])
    d_dea = float(dea.iloc[-1])
    d_hist = float(hist.iloc[-1])
    d_dif_1 = float(dif.iloc[-2]) if len(dif) > 1 else d_dif
    d_dea_1 = float(dea.iloc[-2]) if len(dea) > 1 else d_dea

    daily_gc = d_dif > d_dea and d_dif_1 <= d_dea_1
    daily_gc_active = d_dif > d_dea
    daily_approaching = d_dif < d_dea and d_dea > 0 and d_dif > d_dea * 0.95

    # ─── 周线MACD ───
    try:
        df_idx = pd.to_datetime(df["trade_date"])
        weekly_close = close.copy()
        weekly_close.index = df_idx
        weekly_close = weekly_close.resample("W").last().dropna()
    except Exception:
        weekly_close = close.iloc[-1:]

    if len(weekly_close) >= 14:
        w_ema12 = weekly_close.ewm(span=12).mean()
        w_ema26 = weekly_close.ewm(span=26).mean()
        w_dif = w_ema12 - w_ema26
        w_dea = w_dif.ewm(span=9).mean()
        w_hist = (w_dif - w_dea) * 2

        w_dif_v = float(w_dif.iloc[-1])
        w_dea_v = float(w_dea.iloc[-1])
        w_hist_v = float(w_hist.iloc[-1])

        if len(w_dif) > 1:
            w_dif_1 = float(w_dif.iloc[-2])
            w_dea_1 = float(w_dea.iloc[-2])
        else:
            w_dif_1, w_dea_1 = w_dif_v, w_dea_v

        weekly_gc = w_dif_v > w_dea_v and w_dif_1 <= w_dea_1
        weekly_gc_active = w_dif_v > w_dea_v
        weekly_approaching = w_dif_v < w_dea_v and w_dea_v > 0 and w_dif_v > w_dea_v * 0.95
    else:
        weekly_gc = weekly_gc_active = weekly_approaching = False
        w_dif_v = w_dea_v = w_hist_v = 0

    # ─── 综合 ───
    multi_bullish = daily_gc_active and weekly_gc_active
    score = 0
    signals = []

    if daily_gc:
        score += 30
        signals.append("日线金叉")
    elif daily_approaching:
        score += 18
        signals.append("日线趋近金叉")
    elif daily_gc_active:
        score += 20
        signals.append("日线金叉持续")

    if weekly_gc:
        score += 35
        signals.append("周线金叉")
    elif weekly_approaching:
        score += 20
        signals.append("周线趋近金叉")
    elif weekly_gc_active:
        score += 25
        signals.append("周线金叉持续")

    if multi_bullish:
        score += 35
        signals.append("多周期共振↑")

    if d_hist > 0:
        score += 5
        signals.append("红柱")
    elif d_hist < 0:
        score -= 5
        signals.append("绿柱")

    score = min(100, max(0, score))

    return {
        "score": round(score, 1),
        "signal": " · ".join(signals) if signals else "无MACD信号",
        "daily_golden_cross": daily_gc,
        "daily_gc_active": daily_gc_active,
        "daily_approaching": daily_approaching,
        "daily_dif": round(d_dif, 4), "daily_dea": round(d_dea, 4), "daily_hist": round(d_hist, 4),
        "weekly_golden_cross": weekly_gc,
        "weekly_gc_active": weekly_gc_active,
        "weekly_approaching": weekly_approaching,
        "weekly_dif": round(w_dif_v, 4), "weekly_dea": round(w_dea_v, 4), "weekly_hist": round(w_hist_v, 4),
        "multi_period_bullish": multi_bullish,
    }
