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
    """获取当月翻倍潜力股推荐（缓存 ~5分钟）"""
    result = cache_or_fetch("doubler_recommend", recommend_current_month, 300)
    return jsonify(result)


@bp.route("/recommend/refresh")
def api_doubler_recommend_refresh():
    """强制刷新当月推荐"""
    cache_delete("doubler_recommend")
    result = recommend_current_month()
    cache_set("doubler_recommend", result, 300)
    return jsonify(result)


# ═══════════════════════════════════════════════════════════════
# 仓位方案
# ═══════════════════════════════════════════════════════════════
@bp.route("/plan/10k")
def api_doubler_plan_10k():
    """基于当月推荐池生成 1W 仓位方案"""
    recommend = cache_or_fetch("doubler_recommend", recommend_current_month, 300)
    if isinstance(recommend, dict) and "elite_picks" in recommend:
        plan = position_plan_10k(recommend["elite_picks"])
        return jsonify({"recommend_time": recommend.get("scan_time"), **plan})
    return jsonify({"error": "recommend data unavailable"})
