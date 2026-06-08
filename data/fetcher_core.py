"""
拾米交易工作室 - 数据层：核心抓取模块
tushare 原始数据抓取 + 缓存访问器
"""
import time
from datetime import datetime, timedelta, date

import config
from cache import cache_or_fetch


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
    """获取最近交易日"""
    try:
        pro = get_ts()
        df = pro.daily(trade_date="", limit=1)
        if df.empty:
            return (date.today() - timedelta(days=1)).strftime("%Y%m%d")
        return df["trade_date"].iloc[0]
    except Exception:
        return (date.today() - timedelta(days=1)).strftime("%Y%m%d")


def fetch_all_stocks_basic():
    """获取全市场股票基础信息（含行业）"""
    try:
        pro = get_ts()
        df = pro.stock_basic(exchange="", list_status="L",
                             fields="ts_code,symbol,name,industry,market,area")
        if df.empty:
            return {}
        return {row["ts_code"]: row.to_dict() for _, row in df.iterrows()}
    except Exception:
        return {}


def fetch_daily_data(trade_date):
    """获取某交易日全部股票行情"""
    pro = get_ts()
    df = pro.daily(trade_date=trade_date,
                   fields="ts_code,open,high,low,close,pre_close,pct_chg,amount,vol")
    if df.empty:
        return None
    return df


def fetch_daily_basic(trade_date):
    """获取某交易日换手率等数据"""
    try:
        pro = get_ts()
        df = pro.daily_basic(trade_date=trade_date,
                             fields="ts_code,turnover_rate,volume_ratio,total_mv,circ_mv")
        if df.empty:
            return None
        return df
    except Exception:
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
