"""
HiTao 美股 - 复盘蓝图
路由前缀: /api/us
端点: review/daily, review/weekly
"""

import logging
from flask import Blueprint, jsonify

from haitao.services.review import run_daily_review, run_weekly_review

logger = logging.getLogger(__name__)
bp = Blueprint("haito_review", __name__, url_prefix="/api/us")


@bp.route("/review/daily", methods=["GET", "POST"])
def api_us_review_daily():
    """美股每日复盘"""
    return jsonify(run_daily_review())

@bp.route("/review/weekly", methods=["GET", "POST"])
def api_us_review_weekly():
    """美股每周复盘"""
    return jsonify(run_weekly_review())
