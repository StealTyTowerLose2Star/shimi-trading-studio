"""数据获取层 — 统一管理 tushare/东方财富 数据源"""
import time
import pandas as pd
from datetime import datetime, date, timedelta
from cache import cache_or_fetch, cache_delete
from config import TUSHARE_TOKEN


def get_ts():
    import tushare as ts
    return ts.pro_api(TUSHARE_TOKEN)


def is_trading_time():
    """判断是否在 A 股交易时段（周一至周五 9:30-15:00:00）"""
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    mins = now.hour * 60 + now.minute
    return 570 <= mins < 900


def fetch_daily_data_realtime():
    """盘中实时行情 — 东方财富推拉流"""
    from curl_cffi import requests
    rows, seen = [], set()
    for market, pn in [("0",1), ("0",2), ("1",1), ("1",2), ("80",1)]:
        try:
            url = f"http://80.push2.eastmoney.com/api/qt/clist/get?pn={pn}&pz=500&po=1&np=1&fields=f2,f3,f12,f14&fs=m:{market}+t:6"
            items = (requests.get(url, impersonate="chrome110", timeout=8).json().get("data",{}) or {}).get("diff", [])
            for item in items:
                c = item.get("f12","")
                if c and c not in seen:
                    seen.add(c)
                    ts = f"{c}.SZ" if c.startswith(("0","3")) else f"{c}.SH"
                    rows.append({"ts_code":ts, "close":item.get("f2",0)/100, "pct_chg":item.get("f3",0)/100})
        except:
            pass
    return pd.DataFrame(rows) if rows else None


def fetch_latest_trade_date():
    """获取最近交易日（交易时段返回当天日期）"""
    if is_trading_time():
        return date.today().strftime("%Y%m%d")
    try:
        df = get_ts().daily(trade_date="", limit=1)
        return df["trade_date"].iloc[0] if not df.empty else (date.today() - timedelta(days=1)).strftime("%Y%m%d")
    except:
        return (date.today() - timedelta(days=1)).strftime("%Y%m%d")


def fetch_all_stocks_basic():
    """全市场股票基础信息"""
    try:
        df = get_ts().stock_basic(exchange="", list_status="L", fields="ts_code,symbol,name,industry,market,area")
        return {row["ts_code"]: row.to_dict() for _, row in df.iterrows()} if not df.empty else {}
    except:
        return {}


def fetch_daily_data(trade_date):
    """获取某交易日全部股票行情"""
    try:
        df = get_ts().daily(trade_date=trade_date, fields="ts_code,open,high,low,close,pre_close,pct_chg,amount,vol")
        return df if not df.empty else None
    except:
        return None


def fetch_daily_basic(trade_date):
    """获取某交易日换手率等数据"""
    try:
        df = get_ts().daily_basic(trade_date=trade_date, fields="ts_code,turnover_rate,volume_ratio,total_mv,circ_mv")
        return df if not df.empty else None
    except:
        return None


# ─── 缓存层 ────────────────────────────────

def get_latest_date():
    return cache_or_fetch("latest_date", fetch_latest_trade_date, 300)


def get_stock_basic():
    return cache_or_fetch("stock_basic", fetch_all_stocks_basic, 3600)


def get_daily():
    """获取全市场行情（优先实时接口，盘后回退 tushare）"""
    if is_trading_time():
        try:
            df = fetch_daily_data_realtime()
            if df is not None and len(df) > 0:
                return df
        except:
            pass
    date = get_latest_date()
    if isinstance(date, dict) and "error" in date:
        return None
    df = cache_or_fetch(f"daily_{date}", lambda: fetch_daily_data(date), 600)
    if df is None or isinstance(df, str) or (isinstance(df, dict) and "error" in df):
        cache_delete(f"daily_{date}")
        # Not found → try previous day
        from datetime import datetime, timedelta
        d2 = datetime.strptime(str(date), "%Y%m%d") - timedelta(days=1)
        date = d2.strftime("%Y%m%d")
        df = cache_or_fetch(f"daily_{date}", lambda: fetch_daily_data(date), 600)
        if isinstance(df, dict) and "error" in df:
            cache_delete(f"daily_{date}")
            return None
    if df is None or isinstance(df, str) or (isinstance(df, dict) and "error" in df):
        cache_delete(f"daily_{date}")
        return None
    return df


def get_daily_basic():
    date = get_latest_date()
    if isinstance(date, dict) and "error" in date:
        return None
    df = cache_or_fetch(f"daily_basic_{date}", lambda: fetch_daily_basic(date), 300)
    if df is None or isinstance(df, str) or (isinstance(df, dict) and "error" in df):
        cache_delete(f"daily_basic_{date}")
        return None
    return df
