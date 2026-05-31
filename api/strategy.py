"""
拾米交易工作室 - 策略评分 API
"""
from flask import Blueprint, jsonify

from cache import cache_or_fetch, cache_delete, cache_set
from services.strategy import run_trend_scan, run_hybrid_scan, run_dragon_scan

bp = Blueprint("strategy", __name__, url_prefix="/api")


@bp.route("/strategy/<name>")
def api_strategy(name):
    if name not in ["trend", "hybrid", "dragon"]:
        return jsonify({"error": f"unknown strategy: {name}"}), 404
    fns = {"trend": run_trend_scan, "hybrid": run_hybrid_scan, "dragon": run_dragon_scan}
    return jsonify(cache_or_fetch(f"strategy_{name}", fns[name], 120))


@bp.route("/strategy/<name>/refresh")
def api_strategy_refresh(name):
    if name not in ["trend", "hybrid", "dragon"]:
        return jsonify({"error": f"unknown strategy: {name}"}), 404
    cache_delete(f"strategy_{name}")
    fns = {"trend": run_trend_scan, "hybrid": run_hybrid_scan, "dragon": run_dragon_scan}
    result = fns[name]()
    cache_set(f"strategy_{name}", result, 120)
    return jsonify(result)
