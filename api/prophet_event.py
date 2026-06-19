"""
先知 · 事件信号 API 蓝图
url_prefix: /api/prophet

端点:
  GET  /api/prophet/event-signals       — 获取缓存信号 (由cron每30分钟刷新)
  GET  /api/prophet/event-signals/deep-dives — 仅定向深度分析
  POST /api/prophet/event-signals/scan  — 后台触发扫描, 立即返回 (非阻塞)
"""
from flask import Blueprint, jsonify, request
import threading
import os

bp = Blueprint("prophet_events", __name__, url_prefix="/api/prophet")


@bp.route("/event-signals")
def api_event_signals():
    """获取先知事件信号分析结果 (纯缓存, 秒级响应)

    cron_prophet_scan.sh 每30分钟自动刷新缓存
    """
    from ml.event_predictor import load_cached_signals

    cached = load_cached_signals(max_age_minutes=120)
    if cached:
        return jsonify({**cached, "source": "cache"})

    # 缓存不存在或过期超过2小时 → 返回空并标记
    return jsonify({
        "timestamp": "",
        "total_events": 0,
        "signals": [],
        "summary": {"total_signals": 0, "long_count": 0, "short_count": 0, "high_confidence": 0, "avg_score": 0},
        "deep_dives": [],
        "source": "empty",
        "message": "缓存未就绪, 请等待cron刷新或点击重新扫描",
    })


@bp.route("/event-signals/scan", methods=["POST"])
def api_event_signals_scan():
    """后台触发扫描, 立即返回 (非阻塞, ~30s后缓存更新)"""
    def _background_scan():
        try:
            from ml.event_predictor import scan_and_predict, save_signals
            result = scan_and_predict(pages=2)
            save_signals(result)
        except Exception:
            pass

    thread = threading.Thread(target=_background_scan, daemon=True)
    thread.start()

    # 检查是否已有缓存
    from ml.event_predictor import load_cached_signals
    cached = load_cached_signals(max_age_minutes=120)
    scan_time = cached.get("timestamp", "") if cached else ""

    return jsonify({
        "status": "started",
        "message": "后台扫描已触发, 约20-30秒后刷新页面查看结果",
        "last_scan": scan_time,
    })


@bp.route("/event-signals/deep-dives")
def api_deep_dives():
    """仅返回定向深度分析"""
    from ml.event_predictor import load_cached_signals

    cached = load_cached_signals(max_age_minutes=120)
    if not cached:
        return jsonify({"timestamp": "", "deep_dives": []})

    return jsonify({
        "timestamp": cached["timestamp"],
        "deep_dives": cached.get("deep_dives", []),
    })


@bp.route("/event-signals/status")
def api_status():
    """扫描状态 + 缓存信息"""
    from ml.event_predictor import load_cached_signals
    import os

    cache_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "data", "ml", "prophet_signals.json"
    )
    cache_exists = os.path.exists(cache_path)
    cache_age_min = 0
    if cache_exists:
        cache_age_min = int((os.path.getmtime(cache_path)))

    cached = load_cached_signals(max_age_minutes=120)
    return jsonify({
        "cache_exists": cache_exists,
        "cached_signals": cached["summary"]["total_signals"] if cached else 0,
        "last_scan": cached.get("timestamp", "") if cached else "",
    })
