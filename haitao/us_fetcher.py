"""海淘掘金 - 数据层入口（重定向到新数据提供商）
切换到 Finnhub / Alpha Vantage / Stooq 三源架构
"""
from haitao.us_data import (
    get_quotes, get_indices, get_hot, get_adr,
    get_prepost as get_pre_post_market, get_us_dashboard,
    get_history, get_market_status as get_us_market_status,
    calc_technical_indicators,
    _set_cache as _set_cache, _cached as _cached,
    clear_cache, FINNHUB_KEY,
)

__all__ = [
    "get_quotes", "get_indices", "get_hot_stocks",
    "get_chinese_adr", "get_pre_post_market",
    "get_us_dashboard", "get_history",
    "get_us_market_status", "clear_cache",
    "FINNHUB_KEY",
]

# Alias for backward compatibility
get_hot_stocks = get_hot
get_chinese_adr = get_adr
