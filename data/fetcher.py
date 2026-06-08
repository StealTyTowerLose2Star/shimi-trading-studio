"""
拾米交易工作室 - 数据抓取统一入口
从拆分后的子模块重新导出，保持所有 from data.fetcher import X 向后兼容
"""
from data.fetcher_core import (
    get_ts, fetch_latest_trade_date, fetch_all_stocks_basic,
    fetch_daily_data, fetch_daily_basic,
    get_latest_date, get_stock_basic, get_daily, get_daily_basic,
)
from data.fetcher_indices import (
    INDEX_MAP, fetch_indices, fetch_sectors, fetch_sector_flow,
)
from data.fetcher_sentiment import (
    fetch_hot_stocks, fetch_sentiment,
    fetch_limit_up, fetch_limit_down,
    get_margin_detail,
)

# Re-export from position_manager (maintained for backward compatibility)
from position_manager import get_kline, batch_evaluate, evaluate_position

# Re-export from realtime_scorer (maintained for backward compatibility)
from realtime_scorer import trend_detect, hybrid_score, dragon_leader_score

# ============================================================
# 日级缓存版数据获取 (带本地持久)
# ============================================================
from data.fetcher_cached import (
    # 行情 - 每日只调一次 Tushare
    get_daily_cached,
    get_daily_basic_cached,
    get_stock_basic_cached,
    get_latest_trade_date_cached,
    # 批量刷新
    refresh_daily,
    refresh_all_recent,
    get_cache_summary,
    invalidate_cache,
)
