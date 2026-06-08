"""
拾米交易工作室 · 1W方案追踪 API
Blueprint: /api/plan-1w/*
"""
from flask import Blueprint, jsonify, request

bp = Blueprint("plan_1w", __name__, url_prefix="/api")


@bp.route("/plan-1w", methods=["GET"])
def api_list_plans():
    """列出方案 ?status=active&plan_date=2026-06-08"""
    from services.plan_1w import list_plans
    status = request.args.get("status")
    plan_date = request.args.get("plan_date")
    plans = list_plans(status=status, plan_date=plan_date)
    return jsonify({"plans": plans, "count": len(plans)})


@bp.route("/plan-1w/<int:plan_id>", methods=["GET"])
def api_get_plan(plan_id):
    """获取单个方案"""
    from services.plan_1w import get_plan
    plan = get_plan(plan_id)
    if not plan:
        return jsonify({"error": "not found"}), 404
    return jsonify(plan)


@bp.route("/plan-1w", methods=["POST"])
def api_create_plan():
    """手动创建方案 或 从魔法师扫描生成

    Body: {"plan_date": "2026-06-08", "picks": [...]}
    或 Body: {"generate": true}  ← 从 current_month_picks_v2.json 自动生成
    """
    from services.plan_1w import create_plan, generate_from_doubler

    data = request.get_json(force=True, silent=True) or {}

    if data.get("generate"):
        result = generate_from_doubler()
        if "error" in result:
            return jsonify(result), 400
        return jsonify(result), 201

    plan_date = data.get("plan_date", "")
    picks = data.get("picks", [])
    trade_date = data.get("trade_date", "")

    if not plan_date or not picks:
        return jsonify({"error": "plan_date and picks required"}), 400

    plans = create_plan(plan_date, picks, trade_date)
    return jsonify({"created": len(plans), "plans": plans}), 201


@bp.route("/plan-1w/<int:plan_id>/price", methods=["PUT"])
def api_update_price(plan_id):
    """更新价格 {"price": 12.50}"""
    from services.plan_1w import update_price
    data = request.get_json(force=True, silent=True) or {}
    price = data.get("price")
    if not price:
        return jsonify({"error": "price required"}), 400
    update_price(plan_id, float(price))
    return jsonify({"message": "updated"})


@bp.route("/plan-1w/<int:plan_id>/activate", methods=["POST"])
def api_activate_plan(plan_id):
    """确认买入 {"buy_price": 7.20, "shares": 500}"""
    from services.plan_1w import activate_plan
    data = request.get_json(force=True, silent=True) or {}
    buy_price = data.get("buy_price")
    shares = data.get("shares")
    result = activate_plan(plan_id,
        float(buy_price) if buy_price else None,
        int(shares) if shares else None)
    if "error" in (result or {}):
        return jsonify(result), 404
    return jsonify(result)


@bp.route("/plan-1w/<int:plan_id>/close", methods=["POST"])
def api_close_plan(plan_id):
    """平仓 {"close_price": 12.50, "reason": "止盈"}"""
    from services.plan_1w import close_plan
    data = request.get_json(force=True, silent=True) or {}
    close_price = data.get("close_price")
    reason = data.get("reason", "")
    close_plan(plan_id, close_price, reason)
    return jsonify({"message": "closed"})


@bp.route("/plan-1w/<int:plan_id>", methods=["DELETE"])
def api_delete_plan(plan_id):
    """删除方案"""
    from services.plan_1w import delete_plan
    delete_plan(plan_id)
    return jsonify({"message": "deleted"})


@bp.route("/plan-1w/summary", methods=["GET"])
def api_pnl_summary():
    """盈亏汇总"""
    from services.plan_1w import get_pnl_summary
    return jsonify(get_pnl_summary())


@bp.route("/plan-1w/refresh", methods=["POST"])
def api_refresh_prices():
    """批量更新所有活跃持仓价格"""
    from services.plan_1w import batch_update_prices
    result = batch_update_prices()
    return jsonify(result)


@bp.route("/plan-1w/generate", methods=["POST"])
def api_generate_from_scan():
    """从最新扫描结果生成1W方案"""
    from services.plan_1w import generate_from_doubler
    result = generate_from_doubler()
    if "error" in result:
        return jsonify(result), 400
    return jsonify(result), 201
