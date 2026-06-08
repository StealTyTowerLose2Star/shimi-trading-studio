"""
HiTao 美股 - 杠杆ETF蓝图
路由前缀: /api/us
端点: leveraged/scan, leveraged/analyze/*, leveraged/list
"""

import logging
from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)
bp = Blueprint("haito_leveraged", __name__, url_prefix="/api/us")


@bp.route("/leveraged/scan")
def api_us_leveraged_scan():
    from haitao.us_leveraged_scanner import scan_leveraged_etfs
    direction = request.args.get("direction", "all")
    result = scan_leveraged_etfs(filters={"direction": direction})
    return jsonify(result)

@bp.route("/leveraged/analyze/<ticker>")
def api_us_leveraged_analyze(ticker: str):
    from haitao.us_leveraged_scanner import analyze_leveraged
    return jsonify(analyze_leveraged(ticker.strip().upper()))

@bp.route("/leveraged/list")
def api_us_leveraged_list():
    from haitao.us_leveraged_scanner import list_supported_etfs
    return jsonify(list_supported_etfs())
