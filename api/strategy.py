"""
拾米交易工作室 - 策略评分 API
"""
from flask import Blueprint, jsonify

from cache import cache_or_fetch, cache_delete, cache_set
from services.strategy import run_trend_scan, run_hybrid_scan, run_dragon_scan

bp = Blueprint("strategy", __name__, url_prefix="/api")


def _scan_json(result):
    """扫描结果错误契约 (与 api/market._market_json 一致):
    上游失败 (cache_or_fetch 返回 error 字典) → 502 显式失败, 不 200 包错误;
    合法空结果 {"picked": [], ...} → 200 (当日无入选不是错误)"""
    if isinstance(result, dict) and result.get("error"):
        return jsonify({"error": str(result["error"])[:120],
                        "data_status": "unavailable"}), 502
    return jsonify(result)


@bp.route("/strategy/<name>")
def api_strategy(name):
    if name not in ["trend", "hybrid", "dragon"]:
        return jsonify({"error": f"unknown strategy: {name}"}), 404
    fns = {"trend": run_trend_scan, "hybrid": run_hybrid_scan, "dragon": run_dragon_scan}
    return _scan_json(cache_or_fetch(f"strategy_{name}", fns[name], 120))


@bp.route("/strategy/<name>/refresh")
def api_strategy_refresh(name):
    if name not in ["trend", "hybrid", "dragon"]:
        return jsonify({"error": f"unknown strategy: {name}"}), 404
    cache_delete(f"strategy_{name}")
    fns = {"trend": run_trend_scan, "hybrid": run_hybrid_scan, "dragon": run_dragon_scan}
    try:
        result = fns[name]()
    except Exception as e:
        return jsonify({"error": str(e)[:120], "data_status": "unavailable"}), 502
    cache_set(f"strategy_{name}", result, 120)
    return _scan_json(result)
