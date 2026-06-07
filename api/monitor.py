"""
拾米交易工作室 - 服务器监控 API
建筑师基础设施: 系统资源 + 外部依赖健康检查
"""
from flask import Blueprint, jsonify
from monitor import get_monitor_status, check_external_deps

bp = Blueprint("monitor", __name__, url_prefix="/api")


@bp.route("/monitor")
def api_monitor():
    """服务器资源监控 (CPU/内存/磁盘/网络/负载)"""
    return jsonify(get_monitor_status())


@bp.route("/health/deps")
def api_health_deps():
    """外部依赖健康检查 (tushare / Finnhub / yfinance)

    Returns:
        {
            "tushare":   {"reachable": true, "latency_ms": 234, "message": "正常"},
            "finnhub":   {"reachable": true, "latency_ms": 156, "message": "AAPL $212.5"},
            "yfinance":  {"reachable": true, "latency_ms": 890, "message": "正常"},
            "overall":   "healthy",
            "healthy_count": 3,
            "total_count": 3,
            "timestamp": "2026-06-07 19:30:00"
        }
    """
    return jsonify(check_external_deps())
