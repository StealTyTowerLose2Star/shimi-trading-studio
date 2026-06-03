"""
拾米交易工作室 - 盘中实时数据源（零外部依赖）
使用 urllib 请求东方财富 API 获取实时行情
"""

import json
import urllib.request
import urllib.error
from datetime import datetime, time, date, timedelta
from typing import List, Dict, Optional

import pandas as pd

import config
from cache import cache_or_fetch


# ============================================================
# 交易时间判断
# ============================================================

def is_trading_time() -> bool:
    """判断当前是否为 A 股盘中交易时间

    周一至周五 9:30-15:00（含午休）返回 True
    """
    now = datetime.now()
    if now.weekday() >= 5:  # 周六日
        return False
    current = now.time()
    open_time = time(9, 30)
    close_time = time(15, 0)
    return open_time <= current <= close_time


def is_weekday() -> bool:
    """判断今天是否为工作日（周一至周五）"""
    return datetime.now().weekday() < 5


# ============================================================
# 东方财富 API 辅助
# ============================================================

def _build_secid(code: str) -> str:
    """将股票代码转换为东方财富 secid 格式

    Args:
        code: 股票代码，如 '000001' 或 '000001.SZ'

    Returns:
        str: secid 格式，如 '0.000001' 或 '1.600519'
    """
    code = code.strip().upper()
    if code.endswith('.SZ'):
        return f"0.{code.replace('.SZ', '')}"
    elif code.endswith('.SH'):
        return f"1.{code.replace('.SH', '')}"
    else:
        # 根据代码前缀推断
        if code.startswith('6') or code.startswith('9'):
            return f"1.{code}"
        else:
            return f"0.{code}"


def _parse_code_from_secid(secid: str) -> str:
    """从 secid 还原为纯数字代码"""
    return secid.split('.')[1]


# ============================================================
# 实时行情查询（单次请求，多只股票）
# ============================================================

def fetch_realtime_prices(codes: List[str]) -> Dict[str, dict]:
    """获取多只股票的实时行情

    Args:
        codes: 股票代码列表，支持带后缀(000001.SZ)或不带后缀(000001)

    Returns:
        dict: {code: {price, change, name, ...}}
              失败时返回 {error: message}
    """
    if not codes:
        return {}

    secids = [_build_secid(c) for c in codes]
    secids_str = ','.join(secids)
    url = (
        f"http://push2.eastmoney.com/api/qt/ulist.np/get"
        f"?fields=f2,f3,f12,f14&secids={secids_str}"
    )

    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://quote.eastmoney.com/",
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8")
            data = json.loads(raw)

        result = {}
        if data.get("data") and data["data"].get("diff"):
            for item in data["data"]["diff"]:
                code = _parse_code_from_secid(str(item.get("f12", "")))
                price = item.get("f2")
                change = item.get("f3")
                name = item.get("f14", "")
                # f2=-1 表示停牌或暂无数据
                if price is not None and price not in ("-", None) and price != -1:
                    result[code] = {
                        "price": round(float(price), 2),
                        "change": round(float(change), 2) if change not in (None, "-") else 0.0,
                        "name": str(name) if name else "",
                    }
        return result

    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError,
            ConnectionError, TimeoutError, OSError) as e:
        return {"error": f"东方财富行情请求失败: {e}"}


# ============================================================
# 全市场实时行情（分市场分页）
# ============================================================

# 市场前缀: 0=深市, 1=沪市, 80=创业板
_MARKETS = [
    {"market": 0, "name": "深市"},
    {"market": 1, "name": "沪市"},
    {"market": 80, "name": "创业板"},
]


def _fetch_market_page(market: int, page: int, page_size: int = 500) -> List[dict]:
    """请求单个市场单页数据"""
    url = (
        f"http://80.push2.eastmoney.com/api/qt/clist/get"
        f"?pn={page}&pz={page_size}&po=1&np=1"
        f"&fields=f2,f3,f12,f14&fs=m:{market}+t:6"
    )
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://quote.eastmoney.com/",
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
            data = json.loads(raw)

        items = []
        if data.get("data") and data["data"].get("diff"):
            for item in data["data"]["diff"]:
                code = str(item.get("f12", ""))
                price = item.get("f2")
                change = item.get("f3")
                name = item.get("f14", "")
                if price is not None and price not in ("-", None) and price != -1:
                    # 构建 ts_code 格式
                    if market == 1:
                        ts_code = f"{code}.SH"
                    else:
                        ts_code = f"{code}.SZ"
                    items.append({
                        "ts_code": ts_code,
                        "close": round(float(price), 2),
                        "pct_chg": round(float(change), 2) if change not in (None, "-") else 0.0,
                        "name": str(name) if name else "",
                    })
        return items
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError,
            ConnectionError, TimeoutError, OSError):
        return []


def fetch_all_realtime() -> pd.DataFrame:
    """获取全市场实时行情（分市场分页）

    每个市场取 2 页（每页 500 只），去重后返回 DataFrame

    Returns:
        pd.DataFrame: 包含 ts_code, close, pct_chg 列
    """
    all_items = []
    seen_codes = set()

    for mkt in _MARKETS:
        for page_num in range(1, 3):  # 每市场取 2 页
            items = _fetch_market_page(mkt["market"], page_num)
            for item in items:
                code = item["ts_code"]
                if code not in seen_codes:
                    seen_codes.add(code)
                    all_items.append(item)

    if not all_items:
        return pd.DataFrame(columns=["ts_code", "close", "pct_chg"])

    df = pd.DataFrame(all_items)
    # 确保必要列存在
    for col in ["ts_code", "close", "pct_chg"]:
        if col not in df.columns:
            df[col] = 0.0 if col != "ts_code" else ""
    return df


# ============================================================
# 统一入口
# ============================================================

def get_market_data() -> Optional[pd.DataFrame]:
    """获取行情数据的统一入口

    盘中: 调用东方财富实时接口，失败时静默回退到 tushare
    盘后: 调用 tushare get_daily()
    结果缓存 60 秒

    Returns:
        pd.DataFrame | None: 包含 ts_code, close, pct_chg 等列
    """
    now = datetime.now()
    use_eastmoney = is_trading_time()

    if use_eastmoney:
        # 盘中：优先东方财富实时数据
        df = cache_or_fetch("realtime_market", fetch_all_realtime, 60)
        if isinstance(df, dict) and "error" in df:
            df = None
        if df is not None and not df.empty:
            return df
        # 静默回退到 tushare
        from data.fetcher import get_daily
        df = get_daily()
        return df
    else:
        # 盘后：使用 tushare
        from data.fetcher import get_daily
        return get_daily()
