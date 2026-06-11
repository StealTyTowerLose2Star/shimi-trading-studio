"""
先知 · 事件信号 API 蓝图
url_prefix: /api/prophet

端点:
  GET  /api/prophet/event-signals       — 获取最新事件信号分析
  POST /api/prophet/event-signals/scan  — 触发即时扫描
"""
from flask import Blueprint, jsonify, request

bp = Blueprint("prophet_events", __name__, url_prefix="/api/prophet")


@bp.route("/event-signals")
def api_event_signals():
    """获取先知事件信号分析结果

    优先使用缓存 (30分钟内), 过期自动重新扫描
    """
    from ml.event_predictor import load_cached_signals, scan_and_predict, save_signals

    # 尝试缓存
    cached = load_cached_signals(max_age_minutes=30)
    if cached:
        return jsonify({**cached, "source": "cache"})

    # 重新扫描
    result = scan_and_predict(pages=3)
    save_signals(result)
    result["source"] = "fresh"
    return jsonify(result)


@bp.route("/event-signals/scan", methods=["POST"])
def api_event_signals_scan():
    """强制即时扫描 (清除缓存)"""
    from ml.event_predictor import scan_and_predict, save_signals

    pages = request.json.get("pages", 3) if request.is_json else 3
    result = scan_and_predict(pages=pages)
    save_signals(result)
    result["source"] = "fresh_forced"
    return jsonify(result)


@bp.route("/event-signals/deep-dives")
def api_deep_dives():
    """仅返回定向深度分析"""
    from ml.event_predictor import load_cached_signals, scan_and_predict, save_signals

    cached = load_cached_signals(max_age_minutes=60)
    if not cached:
        cached = scan_and_predict(pages=3)
        save_signals(cached)

    return jsonify({
        "timestamp": cached["timestamp"],
        "deep_dives": cached.get("deep_dives", []),
    })
