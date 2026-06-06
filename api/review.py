"""
拾米交易工作室 - 复盘系统 API
Flask Blueprint, url_prefix="/api"
"""
from flask import Blueprint, jsonify, request
import time
import json

from db import get_latest_review, get_review_history
from services.review import run_daily_review
from services.review_weekly import run_weekly_review

bp = Blueprint("review", __name__, url_prefix="/api")


# ─── 每日复盘 ──────────────────────────────────────────

@bp.route("/review/daily", methods=["POST"])
def trigger_daily_review():
    """触发每日复盘"""
    try:
        result = run_daily_review()
        if "error" in result:
            return jsonify({
                "success": False,
                "error": result["error"],
                "data": None,
            }), 200
        return jsonify({
            "success": True,
            "message": "每日复盘完成",
            "data": result,
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "data": None,
        }), 500


@bp.route("/review/daily", methods=["GET"])
def get_daily_review():
    """获取最新每日复盘报告"""
    report = get_latest_review("daily")
    if not report:
        return jsonify({
            "success": False,
            "error": "暂无每日复盘报告",
            "data": None,
        }), 200
    return jsonify({
        "success": True,
        "data": report,
    })


# ─── 每周复盘 ──────────────────────────────────────────

@bp.route("/review/weekly", methods=["POST"])
def trigger_weekly_review():
    """触发每周复盘"""
    try:
        result = run_weekly_review()
        if "error" in result:
            return jsonify({
                "success": False,
                "error": result["error"],
                "data": None,
            }), 200
        return jsonify({
            "success": True,
            "message": "每周复盘完成",
            "data": result,
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "data": None,
        }), 500


@bp.route("/review/weekly", methods=["GET"])
def get_weekly_review():
    """获取最新每周复盘报告"""
    report = get_latest_review("weekly")
    if not report:
        return jsonify({
            "success": False,
            "error": "暂无每周复盘报告",
            "data": None,
        }), 200
    return jsonify({
        "success": True,
        "data": report,
    })


# ─── 复盘历史 ──────────────────────────────────────────

@bp.route("/review/history", methods=["GET"])
def get_review_history_api():
    """获取复盘历史列表"""
    review_type = request.args.get("type", None)
    limit = request.args.get("limit", 10, type=int)

    if review_type and review_type not in ("daily", "weekly"):
        return jsonify({
            "success": False,
            "error": "参数 type 必须是 daily 或 weekly",
            "data": None,
        }), 400

    if review_type:
        history = get_review_history(review_type, limit)
    else:
        daily = get_review_history("daily", limit)
        weekly = get_review_history("weekly", limit)
        # Merge and sort by id desc
        history = sorted(
            daily + weekly,
            key=lambda x: x.get("id", 0),
            reverse=True,
        )[:limit]

    return jsonify({
        "success": True,
        "data": history,
    })
