"""
拾米交易工作室 - 市场数据 API
"""
from flask import Blueprint, jsonify

from cache import cache_or_fetch
from data.fetcher import (
    fetch_indices, fetch_sectors, fetch_sector_flow,
    fetch_hot_stocks, fetch_limit_up, fetch_sentiment,
    get_stock_basic, get_latest_date,
)

bp = Blueprint("market", __name__, url_prefix="/api")


@bp.route("/health")
def health():
    date = get_latest_date()
    return jsonify({"status": "ok", "studio": "拾米交易工作室", "latest_trade_date": date})


@bp.route("/indices")
def api_indices():
    return jsonify(cache_or_fetch("indices", fetch_indices, 30))


@bp.route("/sectors")
def api_sectors():
    return jsonify(cache_or_fetch("sectors", fetch_sectors, 120))


@bp.route("/sector-flow")
def api_sector_flow():
    return jsonify(cache_or_fetch("sector_flow", fetch_sector_flow, 60))


@bp.route("/hot-stocks")
def api_hot_stocks():
    return jsonify(cache_or_fetch("hot_stocks", fetch_hot_stocks, 30))


@bp.route("/limit-up")
def api_limit_up():
    return jsonify(cache_or_fetch("limit_up", fetch_limit_up, 60))


@bp.route("/sentiment")
def api_sentiment():
    return jsonify(cache_or_fetch("sentiment", fetch_sentiment, 30))


@bp.route("/stock/lookup")
def api_stock_lookup():
    """股票代码搜索 → 返回代码+名称"""
    from flask import request
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify([])
    basic = get_stock_basic()
    if not isinstance(basic, dict):
        return jsonify([])
    results = []
    for code, info in basic.items():
        short = code.replace(".SZ", "").replace(".SH", "").replace(".BJ", "")
        name = info.get("name", "")
        if short.startswith(q) or (name and name.startswith(q)):
            results.append({"code": short, "name": name, "ts_code": code})
            if len(results) >= 10:
                break
    return jsonify(results)


@bp.route("/dashboard")
def api_dashboard():
    """聚合所有市场+策略数据 (带容错降级)"""
    import time
    start = time.time()

    result = {}
    errors = []

    # 每项独立容错: 单点故障不影响整体
    def safe_fetch(key, fn, ttl):
        try:
            return cache_or_fetch(key, fn, ttl)
        except Exception as e:
            errors.append({"key": key, "error": str(e)[:80]})
            return None

    result["indices"] = safe_fetch("indices", fetch_indices, 30)
    result["sectors"] = safe_fetch("sectors", fetch_sectors, 120)
    result["sector_flow"] = safe_fetch("sector_flow", fetch_sector_flow, 60)
    result["limit_up"] = safe_fetch("limit_up", fetch_limit_up, 60)
    result["sentiment"] = safe_fetch("sentiment", fetch_sentiment, 30)

    from services.strategy import run_trend_scan, run_hybrid_scan, run_dragon_scan
    result["strategy_trend"] = safe_fetch("strategy_trend", run_trend_scan, 120)
    result["strategy_hybrid"] = safe_fetch("strategy_hybrid", run_hybrid_scan, 120)
    result["strategy_dragon"] = safe_fetch("strategy_dragon", run_dragon_scan, 120)
    result["hot_stocks"] = safe_fetch("hot_stocks", fetch_hot_stocks, 30)

    total = round(time.time() - start, 1)
    if errors:
        print(f"[拾米] ⚠️ Dashboard {total}s, {len(errors)} errors: {[e['key'] for e in errors]}")
    else:
        print(f"[拾米] ✅ Dashboard {total}s")
    result["_errors"] = errors if errors else None
    return jsonify(result)
