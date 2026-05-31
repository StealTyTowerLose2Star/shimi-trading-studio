"""
拾米交易工作室 - 策略扫描引擎
从 backend.py 提取，保持向后兼容。

包含三大策略扫描函数：
1. run_trend_scan()  — 趋势策略扫描
2. run_hybrid_scan() — 混合策略扫描
3. run_dragon_scan() — 龙头战法扫描
"""

import pandas as pd
from data.fetcher import get_daily, get_stock_basic
from realtime_scorer import (
    trend_detect, hybrid_score, dragon_leader_score,
    get_kline_batch,
)


# ============================================================
# 策略扫描
# ============================================================
def run_trend_scan():
    """趋势策略扫描 — 使用 realtime_scorer.trend_detect 进行真实 TrendDetector 评分

    从当日股票池中筛选涨幅前 10 的候选股，并行预取 K 线数据后逐个调用
    trend_detect() 评估趋势强度，返回评分 >= 30 的股票。

    Returns:
        dict: {
            "picked": list[dict] — TOP 15 股票，每项含 code, name, price, change,
                      trend_score, stage, strength, trend_formed,
            "total_scanned": int,
            "engine": str
        }
    """
    daily = get_daily()
    basic = get_stock_basic()
    if daily is None or isinstance(daily, dict):
        return {"picked": [], "total_scanned": 0}

    # Build quick lookup for name and pct_chg
    name_map = {}
    chg_map = {}
    if isinstance(basic, dict):
        for code, info in basic.items():
            short = code.replace(".SZ","").replace(".SH","").replace(".BJ","")
            name_map[short] = info.get("name", "")
    if isinstance(daily, pd.DataFrame) and not isinstance(daily, dict):
        for _, row in daily.iterrows():
            short = row["ts_code"].replace(".SZ","").replace(".SH","").replace(".BJ","")
            chg_map[short] = float(row["pct_chg"])

    # Pre-filter: top 20 by pct_chg
    candidates = daily.sort_values("pct_chg", ascending=False).head(10)
    stock_codes = [c.replace(".SZ","").replace(".SH","").replace(".BJ","")
                   for c in candidates["ts_code"]]

    # 并行预取 K 线数据
    get_kline_batch(stock_codes, days=120)

    results = []
    for code in stock_codes:
        try:
            r = trend_detect(code)
            if r and r["total_score"] >= 30:
                results.append({
                    "code": code,
                    "name": name_map.get(code, ""),
                    "price": r["price"],
                    "change": round(chg_map.get(code, 0), 2),
                    "trend_score": r["total_score"],
                    "stage": r["stage"],
                    "strength": r["strength"],
                    "trend_formed": bool(r["trend_formed"]),
                })
        except:
            continue

    results.sort(key=lambda x: x["trend_score"], reverse=True)
    return {"picked": results[:15], "total_scanned": len(stock_codes),
            "engine": "TrendDetector (real)"}


def run_hybrid_scan():
    """混合策略扫描 — 使用 realtime_scorer.hybrid_score 进行真实 MergedScorer 7维评分

    从当日股票池中筛选涨幅前 10 的候选股，并行预取 K 线数据后逐个调用
    hybrid_score() 评估混合策略评分，返回 score >= 15 的股票。

    Returns:
        dict: {
            "picked": list[dict] — TOP 15 股票，每项含 code, name, price, change,
                      score, grade, position_pct, 及各维度评分,
            "total_scanned": int,
            "engine": str
        }
    """
    daily = get_daily()
    basic = get_stock_basic()
    if daily is None or isinstance(daily, dict):
        return {"picked": [], "total_scanned": 0}

    candidates = daily.sort_values("pct_chg", ascending=False).head(10)
    stock_codes = [c.replace(".SZ","").replace(".SH","").replace(".BJ","")
                   for c in candidates["ts_code"]]

    # Build sector map
    sector_map = {}
    if isinstance(basic, dict):
        sector_map = {k: v.get("industry", "") for k, v in basic.items()}

    # Build name/chg maps
    name_map = {}
    chg_map = {}
    if isinstance(basic, dict):
        for c, info in basic.items():
            short = c.replace(".SZ","").replace(".SH","").replace(".BJ","")
            name_map[short] = info.get("name", "")
    if isinstance(daily, pd.DataFrame):
        for _, row in daily.iterrows():
            short = row["ts_code"].replace(".SZ","").replace(".SH","").replace(".BJ","")
            chg_map[short] = float(row["pct_chg"])

    # 并行预取 K 线数据
    get_kline_batch(stock_codes, days=120)

    results = []
    for code in stock_codes:
        try:
            sector = sector_map.get(code, "")
            r = hybrid_score(code, industry=sector)
            if r and r["score"] >= 15:
                dims = r.get("dimensions", {})
                results.append({
                    "code": code,
                    "name": name_map.get(code, ""),
                    "price": r["price"],
                    "change": chg_map.get(code, r["pct_chg"]),
                    "score": r["score"],
                    "grade": r["grade"],
                    "position_pct": r["position_pct"],
                    "d_trend": dims.get("d1_trend", 0),
                    "d_momentum": dims.get("d2_momentum", 0),
                    "d_volume": dims.get("d5_volume", 0),
                    "d_safety": dims.get("d6_safety", 0),
                    "burst": dims.get("burst_bonus", 0),
                })
        except:
            pass

    results.sort(key=lambda x: x["score"], reverse=True)
    return {"picked": results[:15], "total_scanned": len(stock_codes),
            "engine": "MergedScorer 7D (real)"}


def run_dragon_scan():
    """龙头战法扫描 — 使用 realtime_scorer.dragon_leader_score 进行真实 LeaderScorer 5维评分

    从涨停板 (pct_chg >= 9.5) 或涨幅前 10 候选股中筛选，并行预取 K 线数据
    后逐个调用 dragon_leader_score() 评估龙头评分。

    Returns:
        dict: {
            "picked": list[dict] — TOP 15 股票，每项含 code, name, price, change,
                      leader_score, grade, board_count, 及各维度评分,
            "total_scanned": int,
            "engine": str
        }
    """
    daily = get_daily()
    basic = get_stock_basic()
    if daily is None or isinstance(daily, dict):
        return {"picked": [], "total_scanned": 0}

    # Build maps
    name_map = {}
    chg_map = {}
    if isinstance(basic, dict):
        for c, info in basic.items():
            short = c.replace(".SZ","").replace(".SH","").replace(".BJ","")
            name_map[short] = info.get("name", "")
    if isinstance(daily, pd.DataFrame):
        for _, row in daily.iterrows():
            short = row["ts_code"].replace(".SZ","").replace(".SH","").replace(".BJ","")
            chg_map[short] = float(row["pct_chg"])

    # Get limit-up candidates
    limits = daily[daily["pct_chg"] >= 9.5]
    if limits.empty:
        limits = daily.sort_values("pct_chg", ascending=False).head(10)
    else:
        limits = limits.sort_values("pct_chg", ascending=False).head(10)

    stock_codes = [c.replace(".SZ","").replace(".SH","").replace(".BJ","")
                   for c in limits["ts_code"]]

    # 并行预取 K 线数据
    get_kline_batch(stock_codes, days=60)

    results = []
    for code in stock_codes:
        try:
            r = dragon_leader_score(code)
            if r:
                dims = r.get("dimensions", {})
                results.append({
                    "code": code,
                    "name": name_map.get(code, ""),
                    "price": r["price"],
                    "change": chg_map.get(code, r["pct_chg"]),
                    "leader_score": r["leader_score"],
                    "grade": r["grade"],
                    "board_count": r["board_count"],
                    "d_sector": dims.get("sector_rank", 0),
                    "d_consecutive": dims.get("consecutive", 0),
                    "d_time": dims.get("limit_time", 0),
                    "d_drive": dims.get("drive_effect", 0),
                })
        except:
            pass

    results.sort(key=lambda x: x["leader_score"], reverse=True)
    return {"picked": results[:15], "total_scanned": len(stock_codes),
            "engine": "LeaderScorer 5D (real)"}
