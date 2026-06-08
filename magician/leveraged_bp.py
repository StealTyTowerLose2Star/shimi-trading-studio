"""
Magician 美股 — 杠杆ETF蓝图
路由前缀: /api/magician
"""

import logging
from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)
bp = Blueprint("magician_leveraged", __name__, url_prefix="/api/magician")


@bp.route("/leveraged/scan")
def api_leveraged_scan():
    from magician.leveraged_scanner import scan_leveraged_etfs
    direction = request.args.get("direction", "all")
    result = scan_leveraged_etfs(filters={"direction": direction})
    return jsonify(result)

@bp.route("/leveraged/analyze/<ticker>")
def api_leveraged_analyze(ticker: str):
    from magician.leveraged_scanner import analyze_leveraged
    return jsonify(analyze_leveraged(ticker.strip().upper()))

@bp.route("/leveraged/list")
def api_leveraged_list():
    from magician.leveraged_scanner import list_supported_etfs
    return jsonify(list_supported_etfs())
