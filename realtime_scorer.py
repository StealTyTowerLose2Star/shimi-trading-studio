"""拾米交易工作室 - 真实策略评分引擎 (v2)
从三个 GitHub 仓库提取的官方评分公式，适配 tushare 数据源
"""
import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Tuple

TUSHARE_TOKEN = "b5e768c112082f5a38f3400244859d3f0ef9d917296600068d6cbf49"


def get_ts():
    import tushare as ts
    return ts.pro_api(TUSHARE_TOKEN)


def get_kline(code: str, days: int = 120):
    """获取个股 K 线 (tushare)，从最新交易日往回取 days 根"""
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
            return None
        df = df.sort_values("trade_date").reset_index(drop=True)
        df = df.rename(columns={"trade_date": "date", "vol": "volume"})
        df["date"] = pd.to_datetime(df["date"])
        if "pct_chg" not in df.columns:
            df["pct_chg"] = df["close"].pct_change() * 100
        return df
    except:
        return None


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

    # MA alignment (30%)
    ma5, ma10, ma20, ma60 = (
        close.rolling(5).mean().iloc[-1],
        close.rolling(10).mean().iloc[-1],
        close.rolling(20).mean().iloc[-1],
        close.rolling(60).mean().iloc[-1],
    )
    ma_score = (ma5 > ma10) * 25 + (ma10 > ma20) * 25 + (ma20 > ma60) * 25 + (ma5 > ma60) * 25

    # Breakout (25%)
    prev_high = high.iloc[-21:-1].max()
    bpct = max(0, (close.iloc[-1] - prev_high) / prev_high * 100)
    if bpct > 5: brk_score = 100
    elif bpct > 3: brk_score = 80
    elif bpct > 0: brk_score = 60
    else: brk_score = 0

    # Volume (20%)
    vol_ratio = volume.iloc[-1] / max(volume.iloc[-6:-1].mean(), 1)
    if vol_ratio >= 2.0: vol_score = 100
    elif vol_ratio >= 1.5: vol_score = 80
    elif vol_ratio >= 1.2: vol_score = 60
    else: vol_score = 40

    # Slope (25%)
    slope_ = (ma20 - close.rolling(20).mean().iloc[-6]) / close.rolling(20).mean().iloc[-6] * 100
    if slope_ > 5: slp_score = 100
    elif slope_ > 2: slp_score = 80
    elif slope_ > 0: slp_score = 60
    else: slp_score = 20

    total = ma_score * 0.30 + brk_score * 0.25 + vol_score * 0.20 + slp_score * 0.25
    strength = "强趋势" if total >= 80 else ("中等趋势" if total >= 60 else ("弱趋势" if total >= 40 else "无趋势"))

    # Stage
    low120, high120 = low.iloc[-120:].min(), high.iloc[-120:].max()
    stage_pct = (close.iloc[-1] - low120) / max(high120 - low120, 0.01) * 100
    if stage_pct < 30: stage = "鱼头期"
    elif stage_pct < 70: stage = "鱼身期"
    elif stage_pct < 90: stage = "鱼身末期"
    else: stage = "鱼尾期"

    trend_formed = bool(ma_score == 100 and bpct > 0 and vol_ratio >= 1.5 and slope_ > 0)
    return _native({
        "code": code,
        "price": float(close.iloc[-1]),
        "total_score": round(total, 1),
        "strength": strength,
        "stage": stage,
        "stage_pct": round(float(stage_pct), 1),
        "trend_formed": trend_formed,
        "dimensions": {
            "ma_score": int(ma_score), "breakout_score": int(brk_score),
            "volume_score": int(vol_score), "slope_score": int(slp_score),
            "ma5": round(float(ma5), 2), "ma10": round(float(ma10), 2),
            "ma20": round(float(ma20), 2), "ma60": round(float(ma60), 2),
            "breakout_pct": round(float(bpct), 2), "volume_ratio": round(float(vol_ratio), 2),
        }
    })


# ============================================================
# 2. 混合策略 — MergedScorer 7维
# ============================================================

def hybrid_score(code: str, industry: str = ""):
    """混合策略真实评分 — 7维度 + 动量爆发加成"""
    df = get_kline(code, days=120)
    if df is None or len(df) < 60:
        return None

    close = float(df["close"].iloc[-1])
    pct_chg = float(df["pct_chg"].iloc[-1])
    high, low, volume = df["high"], df["low"], df["volume"]

    # d1 趋势结构 (15%)
    ma5 = df["close"].rolling(5).mean().iloc[-1]
    ma10 = df["close"].rolling(10).mean().iloc[-1]
    ma20 = df["close"].rolling(20).mean().iloc[-1]
    d1 = int((ma5 > ma10) * 25 + (ma10 > ma20) * 25 + (ma20 > low.iloc[-120:].min()) * 25 + (df["close"].iloc[-1] > ma20) * 25)

    # d2 动量 (25%)
    achg = abs(pct_chg)
    d2 = 100 if achg > 9.5 else (80 if achg > 7 else (65 if achg > 5 else (50 if achg > 3 else (35 if achg > 1 else 20))))

    # d5 量能 (15%)
    vr = float(volume.iloc[-1] / max(volume.iloc[-6:-1].mean(), 1))
    d5 = 100 if vr > 3 else (85 if vr > 2 else (65 if vr > 1.5 else (45 if vr > 1.0 else 25)))

    # d6 安全边际 (8%)
    low20, high20 = float(low.iloc[-20:].min()), float(high.iloc[-20:].max())
    pos = (close - low20) / max(high20 - low20, 0.01) * 100
    d6 = 80 if 30 <= pos <= 70 else (60 if 20 <= pos <= 80 else (40 if 10 <= pos <= 90 else 20))

    # d7 板块 (22%) — simplified
    d7 = 60
    # d8 持续性 (15%) — simplified
    d8 = 50

    # 动量爆发加成
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

    if technical >= 55: gl = "S+"
    elif technical >= 45: gl = "S"
    elif technical >= 35: gl = "A"
    elif technical >= 25: gl = "B"
    elif technical >= 15: gl = "C"
    else: gl = "D"

    pos_pct = {gl: p for gl, p in [("S+",33),("S",25),("A",20),("B",15),("C",10),("D",0)]}.get(gl, 0)
    return _native({
        "code": code, "price": close, "pct_chg": round(pct_chg, 2),
        "score": round(technical, 1), "grade": gl, "position_pct": pos_pct,
        "dimensions": {"d1_trend": d1, "d2_momentum": d2, "d5_volume": d5,
                       "d6_safety": d6, "d7_sector": d7, "d8_persist": d8,
                       "burst_bonus": burst_bonus, "vol_ratio": round(vr, 2), "rsi": round(rsi, 1)}
    })


# ============================================================
# 3. 龙头战法 — LeaderScorer (真实 5 维评分)
# ============================================================

def _count_consecutive_limit(df, min_pct=9.0):
    """从历史数据推算连板天数"""
    vals = df["pct_chg"].tolist()
    vals = vals[-21:]  # 最近 21 日
    count = 0
    for v in reversed(vals):
        if v >= min_pct:
            count += 1
        else:
            break
    return count


def _estimate_block_time(df):
    """估算涨停时间 — 从涨幅和成交量推断"""
    pct_chg = float(df["pct_chg"].iloc[-1])
    vol_ratio = float(df["volume"].iloc[-1] / max(df["volume"].iloc[-6:-1].mean(), 1))
    if pct_chg >= 9.5:
        # 越早涨停量比越低（缩量涨停更强）
        if vol_ratio < 0.8:
            return "09:35"  # 一字板/秒板
        elif vol_ratio < 1.2:
            return "09:50"  # 早盘板
        elif vol_ratio < 2.0:
            return "10:30"  # 上午板
        else:
            return "14:00"  # 尾盘板
    return "15:00"


def _estimate_sealed_amount(pct_chg, vol_ratio):
    """估算封单金额档次"""
    if pct_chg >= 9.5:
        if vol_ratio < 0.5:
            return 5.0  # 5亿级（缩量一字板）
        elif vol_ratio < 1.0:
            return 2.0  # 2亿级
        elif vol_ratio < 2.0:
            return 1.0  # 1亿级
        else:
            return 0.3  # 3000万级
    return 0


def _block_time_to_minutes(bt: str) -> int:
    """涨停时间字符串转分钟数"""
    try:
        h, m = bt.split(":")
        return int(h) * 60 + int(m)
    except:
        return 570  # 9:30


def dragon_leader_score(code: str, extra: dict = None):
    """龙头评分 — 真实 5 维评分 (官方 LeaderScorer 公式)

    Args:
        code: 股票代码
        extra: 额外数据字典，可选键: board_count, block_time, sealed_amount, sector_max_board
    """
    df = get_kline(code, days=60)
    if df is None or len(df) < 20:
        return None

    close = float(df["close"].iloc[-1])
    pct_chg = float(df["pct_chg"].iloc[-1])
    extra = extra or {}

    # ---- 1. 连板高度分 (权重 25%) ----
    bc = extra.get("board_count", 0)
    if bc <= 0:
        bc = _count_consecutive_limit(df)
    # 即使今天没涨停，如果昨天连板也算
    if bc <= 0:
        bc = 1 if pct_chg >= 5 else 1
    if bc >= 7: cs = 100
    elif bc >= 5: cs = 80
    elif bc >= 3: cs = 60
    elif bc >= 2: cs = 40
    else: cs = 20

    # ---- 2. 涨停时间分 (权重 20%) ----
    bt = extra.get("block_time", "")
    if not bt:
        bt = _estimate_block_time(df)
    mins = _block_time_to_minutes(bt)
    if mins <= 575:   # 9:35 前
        ts = 100
    elif mins <= 585: # 9:45 前
        ts = 70
    elif mins <= 600: # 10:00 前
        ts = 40
    else:
        ts = 20

    # ---- 3. 带动效应分 (权重 15%) ----
    sa = extra.get("sealed_amount", 0)
    if sa <= 0:
        sa = _estimate_sealed_amount(pct_chg, float(
            df["volume"].iloc[-1] / max(df["volume"].iloc[-6:-1].mean(), 1)
        ))
    if sa >= 3: ds = 100
    elif sa >= 1: ds = 80
    elif sa >= 0.5: ds = 60
    else: ds = 40

    # ---- 4. 板块地位分 (权重 35%) ----
    smax = extra.get("sector_max_board", bc)
    if bc >= smax and smax >= 3:
        sr = 100
    elif bc >= smax - 1:
        sr = 80
    elif bc >= smax - 2:
        sr = 60
    else:
        sr = 40

    # ---- 5. 抗跌性分 (权重 5%) ----
    # 看最近 5 日有没有大跌
    max_drawdown = 0
    for i in range(max(1, len(df) - 6), len(df)):
        dd = (float(df["close"].iloc[i]) - float(df["close"].iloc[i-1])) / float(df["close"].iloc[i-1]) * 100
        if dd < max_drawdown:
            max_drawdown = dd
    if max_drawdown > -3: rs = 80     # 抗跌
    elif max_drawdown > -7: rs = 60   # 一般
    else: rs = 40                     # 脆弱

    total = sr * 0.35 + cs * 0.25 + ts * 0.20 + ds * 0.15 + rs * 0.05
    grade = "总龙头" if total >= 80 else ("主线龙头" if total >= 60 else ("板块龙头" if total >= 40 else "补涨龙"))

    return _native({
        "code": code, "price": close, "pct_chg": round(pct_chg, 2),
        "leader_score": round(total), "grade": grade, "board_count": bc,
        "dimensions": {"sector_rank": sr, "consecutive": cs, "limit_time": ts,
                       "drive_effect": ds, "resistance": rs,
                       "block_time": bt, "sealed_amount": round(sa, 2)}
    })
