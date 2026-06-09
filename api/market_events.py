"""
拾米交易工作室 - 市场事件 API 蓝图
url_prefix: /api
"""
from flask import Blueprint, jsonify, request

bp = Blueprint("market_events", __name__, url_prefix="/api")


@bp.route("/market/events")
def api_market_events():
    """获取当前市场事件及交易信号"""
    from services.market_events import scan_market_events
    return jsonify(scan_market_events())


@bp.route("/market/events/refresh", methods=["POST"])
def api_market_events_refresh():
    """强制刷新市场事件"""
    import os
    cache = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "market_events.json")
    if os.path.exists(cache):
        os.remove(cache)
    from services.market_events import scan_market_events
    return jsonify(scan_market_events())
