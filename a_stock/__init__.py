"""
拾米交易工作室 - A股交易功能平台底座
统一入口: 所有 A 股组件的导出点

架构:
  a_stock/                 ← 平台底座 (本包)
     __init__.py           ← 统一重导出入口
     platform.py           ← AStockPlatform 生命周期管理
  services/                ← 业务逻辑服务层
  data/                    ← 数据获取层
  db/                      ← 数据库层
  api/                     ← HTTP API 蓝图层
  (根目录)                 ← 基础设施层

低耦合保障:
  - 不引入 haitao/ 的任何符号
  - 不创建 Flask 依赖
  - 纯 Python 导入，可独立测试
"""

# ============================================================
# 基础设施 - 核心模块 (根目录)
# ============================================================
from config import (
    TUSHARE_TOKEN, SERVER_HOST, SERVER_PORT, DEBUG,
    DB_TYPE, DB_PATH, ROOT_DIR, validate as validate_config,
)

from cache import (
    cache_get, cache_set, cache_delete, cache_clear, cache_or_fetch,
)

from logger import (
    get_logger, startup_log, request_log, dep_check_log,
    set_request_id, get_request_id,
)

from message_queue import enqueue, dequeue_all

# ============================================================
# 数据层 - data/
# ============================================================
from data.fetcher import (
    # 核心
    get_ts, fetch_latest_trade_date, fetch_all_stocks_basic,
    fetch_daily_data, fetch_daily_basic,
    get_latest_date, get_stock_basic, get_daily, get_daily_basic,
    # 指数/板块
    INDEX_MAP, fetch_indices, fetch_sectors, fetch_sector_flow,
    # 情绪/涨停板
    fetch_hot_stocks, fetch_sentiment,
    fetch_limit_up, fetch_limit_down, get_margin_detail,
)

# 实数
from data.realtime_provider import (
    fetch_realtime_prices, fetch_all_realtime, get_market_data,
)

# ============================================================
# 日级缓存版数据获取 (每日只调一次 Tushare)
# ============================================================
from data.fetcher_cached import (
    get_daily_cached,
    get_daily_basic_cached,
    get_stock_basic_cached,
    get_latest_trade_date_cached,
    refresh_daily,
    refresh_all_recent,
    get_cache_summary,
    invalidate_cache,
)

# ============================================================
# 策略评分引擎 - root-level modules
# ============================================================
from realtime_scorer import (
    get_kline, get_kline_batch,
    trend_detect, hybrid_score, dragon_leader_score,
    ma_convergence_score, macd_analysis,
)

from position_manager import (
    get_kline as pm_get_kline,
    evaluate_position, batch_evaluate,
    calc_atr,
)

from market_analysis import (
    fetch_sentiment as ma_fetch_sentiment,
    fetch_sectors as ma_fetch_sectors,
    fetch_sector_flow as ma_fetch_sector_flow,
    fetch_hot_stocks as ma_fetch_hot_stocks,
    fetch_limit_up as ma_fetch_limit_up,
)

# ============================================================
# 数据库层 - db/
# ============================================================
from db import (
    get_db, init_db,
    register_user, login_user, verify_token,
    get_trades, add_trade, update_trade, delete_trade, get_trade_summary,
    save_recommendations, get_recommendations, get_all_recommendations,
    save_review_report, get_latest_review, get_review_history,
)

# ============================================================
# 服务层 - services/
# ============================================================
from services import (
    # 策略扫描
    run_trend_scan, run_hybrid_scan, run_dragon_scan,
    # 操作建议
    generate_advice, calc_atr_based_levels,
    # 盈亏分析
    compute_pnl_report,
    # 持仓分析
    analyze_portfolio,
    # 复盘
    run_daily_review, run_weekly_review,
    # 预警
    create_alert, list_alerts, get_alert,
    update_alert, delete_alert, check_alerts,
)

# ============================================================
# 平台类
# ============================================================
from .platform import AStockPlatform

# ============================================================
# 版本信息
# ============================================================
__version__ = "2.0.0"
__author__ = "建筑师 · 拾米交易工作室"
__a_stock_role__ = "拾米A股"
__description__ = "A股交易功能平台底座 — 策略/交易/风控/复盘/预警 一体化"

# 导出清单
__all__ = [
    # 平台
    "AStockPlatform",
    # 配置
    "TUSHARE_TOKEN", "SERVER_HOST", "SERVER_PORT", "DEBUG",
    "DB_TYPE", "DB_PATH", "ROOT_DIR", "validate_config",
    # 缓存
    "cache_get", "cache_set", "cache_delete", "cache_clear", "cache_or_fetch",
    # 日志
    "get_logger", "startup_log", "request_log", "dep_check_log",
    "set_request_id", "get_request_id",
    # 消息队列
    "enqueue", "dequeue_all",
    # 数据
    "get_ts", "fetch_latest_trade_date", "fetch_all_stocks_basic",
    "fetch_daily_data", "fetch_daily_basic",
    "get_latest_date", "get_stock_basic", "get_daily", "get_daily_basic",
    "INDEX_MAP", "fetch_indices", "fetch_sectors", "fetch_sector_flow",
    "fetch_hot_stocks", "fetch_sentiment",
    "fetch_limit_up", "fetch_limit_down", "get_margin_detail",
    "fetch_realtime_prices", "fetch_all_realtime", "get_market_data",
    # 日级缓存版
    "get_daily_cached", "get_daily_basic_cached",
    "get_stock_basic_cached", "get_latest_trade_date_cached",
    "refresh_daily", "refresh_all_recent",
    "get_cache_summary", "invalidate_cache",
    # 策略评分
    "get_kline", "get_kline_batch",
    "trend_detect", "hybrid_score", "dragon_leader_score",
    "ma_convergence_score", "macd_analysis",
    "pm_get_kline", "evaluate_position", "batch_evaluate",
    "calc_atr",
    "ma_fetch_sentiment", "ma_fetch_sectors",
    "ma_fetch_sector_flow", "ma_fetch_hot_stocks", "ma_fetch_limit_up",
    # 数据库
    "get_db", "init_db",
    "register_user", "login_user", "verify_token",
    "get_trades", "add_trade", "update_trade", "delete_trade", "get_trade_summary",
    "save_recommendations", "get_recommendations", "get_all_recommendations",
    "save_review_report", "get_latest_review", "get_review_history",
    # 服务
    "run_trend_scan", "run_hybrid_scan", "run_dragon_scan",
    "generate_advice", "calc_atr_based_levels",
    "compute_pnl_report",
    "analyze_portfolio",
    "run_daily_review", "run_weekly_review",
    "create_alert", "list_alerts", "get_alert",
    "update_alert", "delete_alert", "check_alerts",
]
