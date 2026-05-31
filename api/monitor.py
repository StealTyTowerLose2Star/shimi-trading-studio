"""
拾米交易工作室 - 服务器监控 API
"""
from flask import Blueprint, jsonify
from monitor import get_monitor_status

bp = Blueprint("monitor", __name__, url_prefix="/api")


@bp.route("/monitor")
def api_monitor():
    return jsonify(get_monitor_status())
