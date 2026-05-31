"""
拾米交易工作室 - 数据抓取模块
从 tushare 获取 A 股行情、板块、情绪等数据

从 backend.py 提取，保持向后兼容。
"""

import time
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, date
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional

import config
from cache import cache_or_fetch
from position_manager import get_kline, batch_evaluate, evaluate_position
from realtime_scorer import trend_detect, hybrid_score, dragon_leader_score, _count_consecutive_limit


# ============================================================
# tushare 初始化
# ============================================================
def get_ts():
    """获取 tushare pro API 连接实例"""
    import tushare as ts
    return ts.pro_api(config.TUSHARE_TOKEN)


# ============================================================
# 原始数据抓取（不含缓存）
# ============================================================

def fetch_latest_trade_date():
    """获取最近交易日

    Returns:
        str: 格式为 YYYYMMDD 的交易日字符串
    """
    try:
        pro = get_ts()
        df = pro.daily(trade_date="", limit=1)
        if df.empty:
            # fallback: try yesterday
            return (date.today() - timedelta(days=1)).strftime("%Y%m%d")
        return df["trade_date"].iloc[0]
    except:
        return (date.today() - timedelta(days=1)).strftime("%Y%m%d")


def fetch_all_stocks_basic():
    """获取全市场股票基础信息（含行业）

    Returns:
        dict: {ts_code: {ts_code, symbol, name, industry, market, area}, ...}
    """
    try:
        pro = get_ts()
        df = pro.stock_basic(exchange="", list_status="L",
                             fields="ts_code,symbol,name,industry,market,area")
        if df.empty:
            return {}
        return {row["ts_code"]: row.to_dict() for _, row in df.iterrows()}
    except:
        return {}


def fetch_daily_data(trade_date):
    """获取某交易日全部股票行情

    Args:
        trade_date: str, 格式 YYYYMMDD

    Returns:
        pd.DataFrame | None: 包含 ts_code,open,high,low,close,pre_close,pct_chg,amount,vol
    """
    pro = get_ts()
    df = pro.daily(trade_date=trade_date,
                   fields="ts_code,open,high,low,close,pre_close,pct_chg,amount,vol")
    if df.empty:
        return None
    return df


def fetch_daily_basic(trade_date):
    """获取某交易日换手率等数据

    Args:
        trade_date: str, 格式 YYYYMMDD

    Returns:
        pd.DataFrame | None: 包含 ts_code,turnover_rate,volume_ratio,total_mv,circ_mv
    """
    try:
        pro = get_ts()
        df = pro.daily_basic(trade_date=trade_date,
                             fields="ts_code,turnover_rate,volume_ratio,total_mv,circ_mv")
        if df.empty:
            return None
        return df
    except:
        return None


# ============================================================
# 数据缓存层
# ============================================================

def get_latest_date():
    """获取最近交易日（带缓存，TTL=300s）"""
    return cache_or_fetch("latest_date", fetch_latest_trade_date, 300)


def get_stock_basic():
    """获取全市场股票基础信息（带缓存，TTL=3600s）"""
    return cache_or_fetch("stock_basic", fetch_all_stocks_basic, 3600)


def get_daily():
    """获取最新交易日全部股票行情（带缓存，TTL=60s）"""
    date_val = get_latest_date()
    if isinstance(date_val, dict) and "error" in date_val:
        return None
    df = cache_or_fetch(f"daily_{date_val}", lambda: fetch_daily_data(date_val), 60)
    if isinstance(df, dict) and "error" in df:
        return None
    return df


def get_daily_basic():
    """获取最新交易日换手率等数据（带缓存，TTL=60s）"""
    date_val = get_latest_date()
    if isinstance(date_val, dict) and "error" in date_val:
        return None
    df = cache_or_fetch(f"daily_basic_{date_val}", lambda: fetch_daily_basic(date_val), 60)
    if isinstance(df, dict) and "error" in df:
        return None
    return df


# ============================================================
# 指数
# ============================================================

# 指数 ts_code 映射
INDEX_MAP = {
    "000001.SH": "上证指数",
    "399001.SZ": "深证成指",
    "399006.SZ": "创业板指",
    "000688.SH": "科创50",
}

def fetch_indices():
    """四大指数最新行情

    Returns:
        list[dict]: 每个指数含 name, code, price, change, high, low
    """
    pro = get_ts()
    today = get_latest_date()
    if isinstance(today, dict):
        today = ""
    result = []
    for code, name in INDEX_MAP.items():
        try:
            df = pro.index_daily(ts_code=code, start_date=today, end_date=today,
                                 fields="ts_code,trade_date,close,pct_chg,high,low,vol,amount")
            if df.empty:
                # fallback: last 5 days
                df = pro.index_daily(ts_code=code, limit=5,
                                     fields="ts_code,trade_date,close,pct_chg,high,low,vol,amount")
            if not df.empty:
                row = df.iloc[-1]
                result.append({
                    "name": name, "code": code,
                    "price": round(float(row["close"]), 2),
                    "change": round(float(row["pct_chg"]), 2),
                    "high": round(float(row["high"]), 2) if "high" in row else 0,
                    "low": round(float(row["low"]), 2) if "low" in row else 0,
                })
        except:
            pass
    return result


# ============================================================
# 板块排行
# ============================================================

def fetch_sectors():
    """行业板块排行 — 按涨跌幅排名 TOP 15

    Returns:
        list[dict]: 每个板块含 name, change, up_count, down_count
    """
    daily = get_daily()
    basic = get_stock_basic()
    if daily is None or isinstance(daily, dict):
        return []
    if isinstance(basic, dict) and "error" in basic:
        return []

    # Merge daily with industry
    merged = daily.copy()
    merged["industry"] = merged["ts_code"].map(
        lambda c: basic.get(c, {}).get("industry", "未知") if isinstance(basic, dict) else "未知"
    )

    # Group by industry, compute stats
    groups = merged[merged["industry"] != "未知"].groupby("industry")
    result = []
    for ind, grp in groups:
        avg_chg = grp["pct_chg"].mean()
        up = len(grp[grp["pct_chg"] > 0])
        total = len(grp)
        result.append({"name": ind, "change": round(avg_chg, 2), "up_count": up, "down_count": total - up})
    result.sort(key=lambda x: x["change"], reverse=True)
    return result[:15]


# ============================================================
# 板块资金流 + 情绪
# ============================================================

def fetch_sector_flow():
    """板块资金流分析：行业涨跌幅 + 上涨占比 + 强度评分

    强度评分 = 涨跌幅×0.6 + 上涨占比×0.04

    Returns:
        list[dict]: TOP 15 板块，含 name, change, up_ratio, stock_count, strength, hot
    """
    daily = get_daily()
    basic = get_stock_basic()
    if daily is None or isinstance(daily, dict):
        return []
    if isinstance(basic, dict) and "error" in basic:
        basic = {}

    # Build industry map
    def get_industry(code):
        info = basic.get(code, {}) if isinstance(basic, dict) else {}
        return info.get("industry", "未知")

    df = daily.copy()
    df["industry"] = df["ts_code"].map(get_industry)
    df = df[df["industry"] != "未知"]

    # Group by industry
    groups = df.groupby("industry")
    result = []
    for ind, grp in groups:
        avg_chg = grp["pct_chg"].mean()
        up_ratio = len(grp[grp["pct_chg"] > 0]) / max(len(grp), 1) * 100
        # 强度评分：涨跌幅×0.6 + 上涨占比×0.4
        strength = round(avg_chg * 0.6 + up_ratio * 0.04, 1)
        result.append({
            "name": ind,
            "change": round(avg_chg, 2),
            "up_ratio": round(up_ratio, 1),
            "stock_count": len(grp),
            "strength": strength,
        })

    result.sort(key=lambda x: x["strength"], reverse=True)

    # Mark "热点" sectors (strength > 2)
    for r in result:
        r["hot"] = r["strength"] > 2

    return result[:15]


def fetch_hot_stocks():
    """热门个股涨幅 TOP 20

    Returns:
        list[dict]: 每个股票含 code, name, price, change, volume, turnover
    """
    daily = get_daily()
    if daily is None or isinstance(daily, dict):
        return []
    basic = get_stock_basic()
    daily_basic = get_daily_basic()
    turnover_map = {}
    if daily_basic is not None and not isinstance(daily_basic, dict):
        turnover_map = daily_basic.set_index("ts_code")["turnover_rate"].to_dict()
    daily2 = daily.copy()
    daily2["name"] = daily2["ts_code"].map(
        lambda c: basic.get(c, {}).get("name", "") if isinstance(basic, dict) else ""
    )
    daily2["turnover"] = daily2["ts_code"].map(turnover_map).fillna(0)
    sorted_df = daily2.sort_values("pct_chg", ascending=False).head(20)
    return [
        {"code": row["ts_code"].replace(".SZ","").replace(".SH",""),
         "name": row["name"],
         "price": round(float(row["close"]), 2),
         "change": round(float(row["pct_chg"]), 2),
         "volume": round(float(row.get("amount", 0)) / 1e5, 1),
         "turnover": round(float(row.get("turnover", 0)), 1)}
        for _, row in sorted_df.iterrows()
    ]


def fetch_sentiment():
    """市场状态分析 — 4 维综合评分

    指数趋势40% + 指数位置20% + 情绪20% + 成交量20%
    输出 7 级市场状态 + 建议仓位

    Returns:
        dict: 包含 total_score, phase, description, position_ratio, 及各维度分
    """
    try:
        pro = get_ts()
        today = get_latest_date()
        if isinstance(today, dict):
            today = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=150)).strftime("%Y%m%d")

        # 1) 获取三大指数数据
        idx_codes = {"上证指数": "000001.SH", "深证成指": "399001.SZ", "创业板指": "399006.SZ"}
        idx_weights = {"上证指数": 0.40, "深证成指": 0.35, "创业板指": 0.25}
        index_data = {}
        for name, code in idx_codes.items():
            try:
                df = pro.index_daily(ts_code=code, start_date=start, end_date=today,
                                     fields="trade_date,close,vol,amount")
                if not df.empty:
                    df = df.sort_values("trade_date").reset_index(drop=True)
                    df["ma5"] = df["close"].rolling(5).mean()
                    df["ma10"] = df["close"].rolling(10).mean()
                    df["ma20"] = df["close"].rolling(20).mean()
                    df["ma60"] = df["close"].rolling(60).mean()
                    index_data[name] = df
            except:
                pass

        if not index_data:
            return {"error": "no index data"}

        # 2) 指数趋势评分 (40%) — MA排列+斜率+价格位置
        def calc_index_score(df):
            if df is None or len(df) < 60:
                return {"trend": 0, "position": 0}
            l = df.iloc[-1]
            ma5, ma10, ma20, ma60 = l["ma5"], l["ma10"], l["ma20"], l["ma60"]
            close = l["close"]

            # 均线排列 (50分)
            align = 0
            if ma5 > ma10: align += 12.5
            if ma10 > ma20: align += 12.5
            if ma20 > ma60: align += 12.5
            if ma5 > ma60: align += 12.5
            if align == 50: align = 50  # 满分配置

            # MA20斜率 (25分)
            if len(df) >= 25:
                slope = (ma20 - df["ma20"].iloc[-5]) / df["ma20"].iloc[-20] * 100
                slope_score = min(25, max(0, slope * 10))
            else:
                slope_score = 12.5

            # 价格位置 (25分)
            above_ma20 = close > ma20
            above_ma60 = close > ma60
            if above_ma20 and above_ma60:
                dist = (close - ma20) / ma20 * 100
                pos_score = 15 if dist > 15 else 25
            elif above_ma20:
                pos_score = 15
            elif above_ma60:
                pos_score = 10
            else:
                pos_score = 5 if (ma60 - close) / ma60 * 100 < 5 else 0

            return {"trend": round(align + slope_score + pos_score, 1), "position": pos_score,
                    "above_ma20": bool(above_ma20), "above_ma60": bool(above_ma60)}

        trend_total = 0
        pos_total = 0
        for name, w in idx_weights.items():
            if name in index_data:
                s = calc_index_score(index_data[name])
                trend_total += s["trend"] * w
                pos_total += s["position"] * w

        # 3) 情绪评分 (20%) — 涨停/跌停比分段映射
        daily = get_daily()
        limit_up_count = 0
        limit_down_count = 0
        if daily is not None and not isinstance(daily, dict):
            limit_up_count = len(daily[daily["pct_chg"] >= 9.5])
            limit_down_count = len(daily[daily["pct_chg"] <= -9.5])

        if limit_down_count == 0:
            ratio = 10 + limit_up_count
        else:
            ratio = limit_up_count / limit_down_count

        if ratio >= 5: sentiment_score = 90
        elif ratio >= 3: sentiment_score = 75
        elif ratio >= 1.5: sentiment_score = 60
        elif ratio >= 0.8: sentiment_score = 45
        elif ratio >= 0.3: sentiment_score = 25
        else: sentiment_score = 10

        # 4) 成交量评分 (20%) — 指数量 vs 20日均量
        vol_ratio = 0
        for name, w in idx_weights.items():
            df = index_data.get(name)
            if df is not None and len(df) >= 25:
                cur = df["amount"].iloc[-1]
                avg = df["amount"].tail(20).mean()
                if cur > 0 and avg > 0:
                    vol_ratio += (cur / avg) * w

        if vol_ratio >= 2.0: volume_score = 100
        elif vol_ratio >= 1.5: volume_score = 85
        elif vol_ratio >= 1.2: volume_score = 70
        elif vol_ratio >= 0.8: volume_score = 50
        elif vol_ratio >= 0.5: volume_score = 30
        else: volume_score = 10

        # 5) 综合评分
        total_score = trend_total * 0.40 + pos_total * 0.20 + sentiment_score * 0.20 + volume_score * 0.20

        # 6) 市场状态映射
        MARKET_STATES = [
            (85, "强势牛市🚀", "指数多头排列，量价齐升", (80, 100)),
            (65, "牛市📈", "指数趋势向上，市场活跃", (65, 80)),
            (45, "震荡偏多↗️", "指数震荡上行，精选个股", (45, 65)),
            (25, "震荡市➡️", "指数横盘整理，控制仓位", (25, 45)),
            (10, "震荡偏空↘️", "重心下移，轻仓防守", (10, 25)),
            (0,  "熊市📉", "空头排列，空仓为主", (0, 10)),
        ]
        state = "危机模式⚠️"
        state_desc = "市场恐慌，空仓观望"
        pos_range = (0, 5)
        for threshold, s_name, s_desc, s_range in MARKET_STATES:
            if total_score >= threshold:
                state = s_name
                state_desc = s_desc
                pos_range = s_range
                break

        # 建议仓位(取区间中值)
        suggested_pos = round((pos_range[0] + pos_range[1]) / 2, 1)

        # 涨跌统计
        up = limit_up_count
        down = limit_down_count
        total_stocks = 0
        if daily is not None and not isinstance(daily, dict):
            total_stocks = len(daily)
            up = len(daily[daily["pct_chg"] > 0])
            down = len(daily[daily["pct_chg"] < 0])

        return {
            "total_score": round(total_score, 1),
            "phase": state,
            "description": state_desc,
            "position_ratio": suggested_pos,
            "position_range": f"{pos_range[0]}%-{pos_range[1]}%",
            # 维度分
            "index_trend_score": round(trend_total, 1),
            "index_position_score": round(pos_total, 1),
            "sentiment_score": sentiment_score,
            "volume_score": volume_score,
            "volume_ratio": round(vol_ratio, 2),
            # 原始数据
            "total": total_stocks,
            "up": up,
            "down": down,
            "limit_up": limit_up_count,
            "limit_down": limit_down_count,
            "zt_ratio": round(ratio, 2),
            "source": "hybrid-strategy/MarketStateAnalyzer",
            "formula": "4维综合: 趋势40%+位置20%+情绪20%+量能20%",
        }
    except Exception as e:
        return {"error": str(e)}


# ============================================================
# 涨停板
# ============================================================

def fetch_limit_up():
    """涨停板 — pct_chg >= 9.5 的股票 TOP 20

    Returns:
        list[dict]: 每个股票含 code, name, price, change, board_count
    """
    daily = get_daily()
    if daily is None or isinstance(daily, dict):
        return []
    basic = get_stock_basic()

    df = daily[daily["pct_chg"] >= 9.5].sort_values("pct_chg", ascending=False)
    daily_basic = get_daily_basic()

    result = []
    for _, row in df.iterrows():
        code = row["ts_code"].replace(".SZ", "").replace(".SH", "")
        result.append({
            "code": code,
            "name": basic.get(row["ts_code"], {}).get("name", "") if isinstance(basic, dict) else "",
            "price": round(float(row["close"]), 2),
            "change": round(float(row["pct_chg"]), 2),
            "board_count": 1,  # simplified — full data would come from limit_list
        })
    return result[:20]
