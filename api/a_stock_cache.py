"""
拾米交易工作室 - 缓存管理 API
Blueprint: /api/a-stock/cache/*
"""
import os
import json
import time
from flask import Blueprint, jsonify, request

from cache import cache_or_fetch, cache_delete, cache_set, cache_clear

bp = Blueprint("a_stock_cache", __name__, url_prefix="/api/a-stock")


@bp.route("/cache/summary")
def api_cache_summary():
    """缓存状态概览"""
    from data.fetcher import get_latest_date

    # 统计缓存key
    cache_keys = {
        "indices": "指数数据",
        "sectors": "行业板块",
        "sector_flow": "板块资金流",
        "sentiment": "市场情绪",
        "limit_up": "涨停板",
        "hot_stocks": "热门股",
        "strategy_trend": "趋势策略",
        "strategy_hybrid": "混合策略",
        "strategy_dragon": "龙头策略",
        "advice": "操作建议",
    }

    by_type = {}
    total = 0
    for key, label in cache_keys.items():
        # 尝试读取缓存状态
        cached = cache_or_fetch(key, None, 0)
        count = 1 if cached is not None else 0
        by_type[label] = count
        total += count

    return jsonify({
        "strategy": "内存+可选Redis",
        "total_records": total,
        "by_type": by_type,
        "db_size_mb": 0,
        "latest_trade_date": get_latest_date() or "未同步",
        "timestamp": time.strftime("%H:%M:%S"),
    })


@bp.route("/cache/refresh", methods=["POST"])
def api_cache_refresh():
    """全量刷新缓存"""
    from services.strategy import run_trend_scan, run_hybrid_scan, run_dragon_scan
    from services.advice import generate_advice
    from data.fetcher import (
        fetch_indices, fetch_sectors, fetch_sector_flow,
        fetch_sentiment, fetch_limit_up, fetch_hot_stocks,
    )

    refreshed = []
    failed = []

    tasks = [
        ("indices", fetch_indices, 30),
        ("sectors", fetch_sectors, 120),
        ("sector_flow", fetch_sector_flow, 60),
        ("sentiment", fetch_sentiment, 30),
        ("limit_up", fetch_limit_up, 60),
        ("hot_stocks", fetch_hot_stocks, 30),
        ("strategy_trend", run_trend_scan, 120),
        ("strategy_hybrid", run_hybrid_scan, 120),
        ("strategy_dragon", run_dragon_scan, 120),
        ("advice", generate_advice, 600),
    ]

    for key, fn, ttl in tasks:
        try:
            cache_delete(key)
            result = fn()
            cache_set(key, result, ttl)
            refreshed.append(key)
        except Exception as e:
            failed.append({"key": key, "error": str(e)[:100]})

    return jsonify({
        "result": f"刷新 {len(refreshed)}/{len(tasks)} 项",
        "refreshed": refreshed,
        "failed": failed,
        "timestamp": time.strftime("%H:%M:%S"),
    })


@bp.route("/cache/refresh-recent", methods=["POST"])
def api_cache_refresh_recent():
    """补刷近期数据 (最近N天)"""
    data = request.get_json(force=True, silent=True) or {}
    days_back = data.get("days_back", 5)

    # 只刷新高频数据
    from data.fetcher import fetch_indices, fetch_sectors, fetch_sentiment
    from services.strategy import run_trend_scan

    refreshed = []
    for key, fn, ttl in [
        ("indices", fetch_indices, 30),
        ("sectors", fetch_sectors, 120),
        ("sentiment", fetch_sentiment, 30),
        ("strategy_trend", run_trend_scan, 120),
    ]:
        try:
            cache_delete(key)
            result = fn()
            cache_set(key, result, ttl)
            refreshed.append(key)
        except Exception as e:
            pass

    return jsonify({
        "result": {"dates_attempted": days_back, "refreshed": len(refreshed)},
        "timestamp": time.strftime("%H:%M:%S"),
    })


@bp.route("/cache/invalidate", methods=["POST"])
def api_cache_invalidate():
    """失效所有A股缓存"""
    keys = [
        "indices", "sectors", "sector_flow", "sentiment", "limit_up",
        "hot_stocks", "strategy_trend", "strategy_hybrid", "strategy_dragon",
        "advice", "doubler_recommend", "doubler_history",
    ]
    deleted = 0
    for key in keys:
        if cache_delete(key):
            deleted += 1

    return jsonify({
        "deleted": deleted,
        "timestamp": time.strftime("%H:%M:%S"),
    })
