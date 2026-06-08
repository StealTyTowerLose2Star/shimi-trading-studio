"""
拾米交易工作室 - 日级缓存数据层 (Cached Data Layer)
在原始 Tushare 抓取层之上加一层每日缓存
首次调用→Tushare→存本地；后续→读本地，零 API 消耗

使用方式:
  from data.fetcher_cached import get_daily_cached, refresh_all_daily

向下兼容:
  所有函数签名与原 fetcher_core 保持一致 (同入参同出参)
  返回 DataFrame 的函数仍返回 DataFrame
"""

from typing import Optional, Dict, Any
from datetime import datetime, timedelta

import pandas as pd

from data.daily_store import get_store, DailyStore
from data.fetcher_core import (
    get_ts,
    fetch_latest_trade_date,
    fetch_all_stocks_basic,
    fetch_daily_data,
    fetch_daily_basic,
)
from cache import cache_or_fetch, cache_get, cache_set


def _trade_date_today() -> str:
    """获取今日 (或最近交易日) 作为缓存基准"""
    from data.fetcher_core import fetch_latest_trade_date
    try:
        return fetch_latest_trade_date()
    except Exception:
        return (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")


# ============================================================
# 全量行情 - 日级缓存版
# ============================================================

def get_daily_cached(trade_date: Optional[str] = None) -> Optional[pd.DataFrame]:
    """日级缓存版 get_daily

    策略:
      1. 查 daily_store → 有 → 返回
      2. 无 → 调 Tushare → 存 daily_store → 返回
      3. Tushare 失败 → 尝试前一日数据

    Args:
        trade_date: "YYYYMMDD", None = 最新交易日

    Returns:
        pd.DataFrame | None
    """
    store = get_store()
    td = trade_date or _trade_date_today()

    # 1. 读本地
    raw = store.load_data("daily", td)
    if raw is not None:
        return DailyStore.to_dataframe(raw)

    # 2. 调 Tushare
    try:
        df = fetch_daily_data(td)
        if df is not None and not df.empty:
            store.save_data("daily", td, df)
            return df
    except Exception:
        pass

    # 3. 回退前一日
    prev = _prev_trade_date(td)
    if prev:
        raw_prev = store.load_data("daily", prev)
        if raw_prev is not None:
            return DailyStore.to_dataframe(raw_prev)

    return None


def get_daily_basic_cached(trade_date: Optional[str] = None) -> Optional[pd.DataFrame]:
    """日级缓存版 get_daily_basic"""
    store = get_store()
    td = trade_date or _trade_date_today()

    raw = store.load_data("daily_basic", td)
    if raw is not None:
        return DailyStore.to_dataframe(raw)

    try:
        df = fetch_daily_basic(td)
        if df is not None and not df.empty:
            store.save_data("daily_basic", td, df)
            return df
    except Exception:
        pass

    prev = _prev_trade_date(td)
    if prev:
        raw_prev = store.load_data("daily_basic", prev)
        if raw_prev is not None:
            return DailyStore.to_dataframe(raw_prev)
    return None


# ============================================================
# 股票基础信息 - 周级缓存
# ============================================================

def get_stock_basic_cached(force_refresh: bool = False) -> Dict[str, dict]:
    """周级缓存版 get_stock_basic (每周刷新一次)

    Args:
        force_refresh: 强制刷新 (忽略缓存)

    Returns:
        {ts_code: {...}} dict
    """
    store = get_store()

    if not force_refresh:
        raw = store.load_data("stock_basic", "latest")
        if raw is not None:
            return raw

    try:
        data = fetch_all_stocks_basic()
        if data:
            store.save_data("stock_basic", "latest", data, overwrite=True)
            # 记录刷新时间
            store.set_meta("stock_basic_refreshed", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        return data
    except Exception as e:
        # 回退到已有缓存
        fallback = store.load_data("stock_basic", "latest")
        if fallback is not None:
            return fallback
        return {}


# ============================================================
# 最新交易日 - 日级缓存
# ============================================================

def get_latest_trade_date_cached() -> str:
    """日级缓存版 get_latest_date

    交易日信息每天至多调一次 Tushare
    """
    store = get_store()
    cached = store.get_meta("latest_trade_date")
    if cached:
        return cached

    # 先用短时内存缓存兜着 (300s), 降低多并发时的重复 API 调用
    from data.fetcher_core import get_latest_date
    td = get_latest_date()
    if isinstance(td, str) and td.isdigit():
        store.set_meta("latest_trade_date", td)
    return td


# ============================================================
# 批量刷新 (一键补全)
# ============================================================

def refresh_daily(market: str = "all",
                  trade_date: Optional[str] = None) -> Dict[str, Any]:
    """一键刷新指定日缓存数据

    支持增量刷新:
      - 已缓存的数据跳过 (幂等)
      - 只填充缺失的数据

    Args:
        market: "all"=全部, "daily"=仅行情, "basic"=仅基础
        trade_date: "YYYYMMDD", None=最新交易日

    Returns:
        {type: True/False}  每个数据类型的刷新结果
    """
    store = get_store()
    td = trade_date or _trade_date_today()
    result: Dict[str, Any] = {}

    def _try_save(dt: str, fetch_fn, *args, **kwargs) -> bool:
        if store.has_data(dt, td if dt != "stock_basic" else "latest"):
            return False  # 已存在, 跳过
        try:
            data = fetch_fn(*args, **kwargs)
            if data is not None and not (isinstance(data, pd.DataFrame) and data.empty):
                date_key = td if dt != "stock_basic" else "latest"
                store.save_data(dt, date_key, data)
                # 周级类型记录刷新时间
                if dt in {"stock_basic", "indices", "sectors"}:
                    store.set_meta(f"{dt}_refreshed", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                return True
        except Exception as e:
            result[f"{dt}_error"] = str(e)
        return False

    if market in ("all", "daily"):
        result["daily"] = _try_save("daily", fetch_daily_data, td)
        result["daily_basic"] = _try_save("daily_basic", fetch_daily_basic, td)

    if market in ("all", "basic"):
        result["stock_basic"] = _try_save("stock_basic", fetch_all_stocks_basic)

    return result


def refresh_all_recent(days_back: int = 5) -> Dict[str, Any]:
    """批量补全最近 N 个交易日的缓存

    适用场景: 新部署/重建缓存后, 一次性补全历史数据
    """
    store = get_store()
    results = {}

    # 获取 最近 days_back 个交易日
    try:
        pro = get_ts()
        df_cal = pro.trade_cal(start_date=(
            datetime.now() - timedelta(days=days_back * 2)
        ).strftime("%Y%m%d"), end_date=datetime.now().strftime("%Y%m%d"))
        if df_cal.empty:
            return {"error": "no calendar data"}
        trade_dates = sorted(df_cal[df_cal["is_open"] == 1]["cal_date"].tolist(), reverse=True)
        trade_dates = trade_dates[:days_back]
    except Exception as e:
        return {"error": str(e)}

    for td in trade_dates:
        day_result = refresh_daily(trade_date=td)
        results[td] = day_result

    return {
        "dates_attempted": len(trade_dates),
        "dates": list(results.keys()),
        "details": results,
    }


# ============================================================
# 缓存概览
# ============================================================

def get_cache_summary() -> Dict:
    """返回本地缓存的全景概览"""
    store = get_store()
    base = store.summary()
    base["stock_basic_refreshed"] = store.get_meta("stock_basic_refreshed", "never")
    base["latest_trade_date"] = store.get_meta("latest_trade_date", "unknown")
    base["strategy"] = "写穿缓存: Tushare API 一次, 本地读取永久"
    return base


# ============================================================
# 工具
# ============================================================

def _prev_trade_date(trade_date: str) -> Optional[str]:
    """简单前推一个交易日 (不足则尝试两个)"""
    for days_back in [1, 2, 3, 5]:
        try:
            dt = datetime.strptime(trade_date, "%Y%m%d") - timedelta(days=days_back)
            return dt.strftime("%Y%m%d")
        except ValueError:
            pass
    return None


def invalidate_cache(data_type: Optional[str] = None) -> int:
    """主动失效缓存 (强制下次 API 调用)

    Args:
        data_type: None=全部, "daily"=某个类型

    Returns:
        删除的记录数
    """
    store = get_store()
    return store.clear(data_type)
