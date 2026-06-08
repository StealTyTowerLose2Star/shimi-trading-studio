"""
拾米交易工作室 - 翻倍股扫描 API
Blueprint: /api/doubler/*
"""
from flask import Blueprint, jsonify
from cache import cache_or_fetch, cache_delete, cache_set
from services.doubler_scanner import (
    scan_monthly_doublers,
    recommend_current_month,
    position_plan_10k,
)

bp = Blueprint("doubler", __name__, url_prefix="/api/doubler")


# ═══════════════════════════════════════════════════════════════
# 历史翻倍股
# ═══════════════════════════════════════════════════════════════
@bp.route("/history")
def api_doubler_history():
    """获取历史月度翻倍股扫描结果"""
    import json, os
    fpath = os.path.join(os.path.dirname(os.path.dirname(__file__)), "monthly_doublers.json")
    if os.path.exists(fpath):
        with open(fpath) as f:
            data = json.load(f)
        return jsonify(data)
    return jsonify({"error": "no data, call /history/refresh first"})


@bp.route("/history/refresh")
def api_doubler_history_refresh():
    """强制重新扫描历史翻倍股（耗时 ~2-3分钟）"""
    cache_delete("doubler_history")
    result = scan_monthly_doublers()
    cache_set("doubler_history", result, 3600)
    return jsonify(result)


# ═══════════════════════════════════════════════════════════════
# 当月推荐
# ═══════════════════════════════════════════════════════════════
@bp.route("/recommend")
def api_doubler_recommend():
    """获取当月翻倍潜力股 (10维评分 + 启动前期检测 + 催化剂)"""
    result = cache_or_fetch("doubler_recommend_v4", recommend_current_month, 300)
    return jsonify(result)


@bp.route("/recommend/refresh")
def api_doubler_recommend_refresh():
    """强制刷新当月推荐"""
    from services.doubler_predictor import predict_monthly_doublers
    cache_delete("doubler_recommend")
    result = predict_monthly_doublers()
    cache_set("doubler_recommend", result, 300)
    return jsonify(result)


# ═══════════════════════════════════════════════════════════════
# 仓位方案
# ═══════════════════════════════════════════════════════════════
@bp.route("/plan/10k")
def api_doubler_plan_10k():
    """基于当月推荐池生成 1W 仓位方案"""
    recommend = cache_or_fetch("doubler_recommend_v4", recommend_current_month, 300)
    if isinstance(recommend, dict) and "elite_picks" in recommend:
        plan = position_plan_10k(recommend["elite_picks"])
        return jsonify({"recommend_time": recommend.get("scan_time"), **plan})
    return jsonify({"error": "recommend data unavailable"})


# ═══════════════════════════════════════════════════════════════
# 闭环跟踪 (月内跟踪 + 月末验证)
# ═══════════════════════════════════════════════════════════════
@bp.route("/track/start")
def api_track_start():
    """月初启动跟踪: 保存当前推荐到跟踪表"""
    from services.doubler_tracker import start_tracking
    return jsonify(start_tracking())


@bp.route("/track/status")
def api_track_status():
    """查看本月跟踪进度"""
    from services.doubler_tracker import get_tracking_status
    return jsonify(get_tracking_status())


@bp.route("/track/update")
def api_track_update():
    """手动更新当日价格"""
    from services.doubler_tracker import update_progress
    return jsonify(update_progress())


@bp.route("/track/verify")
def api_track_verify():
    """月末验证: 计算实际涨跌幅"""
    from services.doubler_tracker import verify_month
    return jsonify(verify_month())


@bp.route("/track/effectiveness")
def api_track_effectiveness():
    """查看各催化剂类型历史命中率"""
    from services.doubler_tracker import get_catalyst_effectiveness
    return jsonify(get_catalyst_effectiveness())
