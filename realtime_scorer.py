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


def get_ts():
    import tushare as ts
    return ts.pro_api(config.TUSHARE_TOKEN)


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
    except:
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
    except: return 570


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
