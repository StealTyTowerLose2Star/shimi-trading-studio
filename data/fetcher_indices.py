"""
拾米交易工作室 - 数据层：指数 & 板块排行
"""
import pandas as pd

from data.fetcher_core import get_ts, get_daily, get_stock_basic, get_latest_date
from cache import cache_or_fetch


# ============================================================
# 指数
# ============================================================

INDEX_MAP = {
    "000001.SH": "上证指数",
    "399001.SZ": "深证成指",
    "399006.SZ": "创业板指",
    "000688.SH": "科创50",
}

def fetch_indices():
    """四大指数最新行情"""
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
    """行业板块排行 — 按涨跌幅排名 TOP 15"""
    daily = get_daily()
    basic = get_stock_basic()
    if daily is None or isinstance(daily, dict):
        return []
    if isinstance(basic, dict) and "error" in basic:
        return []

    merged = daily.copy()
    merged["industry"] = merged["ts_code"].map(
        lambda c: basic.get(c, {}).get("industry", "未知") if isinstance(basic, dict) else "未知"
    )

    groups = merged[merged["industry"] != "未知"].groupby("industry")
    result = []
    for ind, grp in groups:
        avg_chg = grp["pct_chg"].mean()
        up = len(grp[grp["pct_chg"] > 0])
        total = len(grp)
        result.append({"name": ind, "change": round(avg_chg, 2), "up_count": up, "down_count": total - up})
    result.sort(key=lambda x: x["change"], reverse=True)
    return result[:15]


def fetch_sector_flow():
    """板块资金流分析：行业涨跌幅 + 上涨占比 + 强度评分"""
    daily = get_daily()
    basic = get_stock_basic()
    if daily is None or isinstance(daily, dict):
        return []
    if isinstance(basic, dict) and "error" in basic:
        basic = {}

    def get_industry(code):
        info = basic.get(code, {}) if isinstance(basic, dict) else {}
        return info.get("industry", "未知")

    df = daily.copy()
    df["industry"] = df["ts_code"].map(get_industry)
    df = df[df["industry"] != "未知"]

    groups = df.groupby("industry")
    result = []
    for ind, grp in groups:
        avg_chg = grp["pct_chg"].mean()
        up_ratio = len(grp[grp["pct_chg"] > 0]) / max(len(grp), 1) * 100
        strength = round(avg_chg * 0.6 + up_ratio * 0.04, 1)
        result.append({
            "name": ind, "change": round(avg_chg, 2),
            "up_ratio": round(up_ratio, 1), "stock_count": len(grp),
            "strength": strength, "hot": strength > 2,
        })

    result.sort(key=lambda x: x["strength"], reverse=True)
    return result[:15]
