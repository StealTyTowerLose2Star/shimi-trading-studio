"""
拾米交易工作室 - 条件预警 API
Blueprint: /api/alert/*
"""
from flask import Blueprint, jsonify, request

from services.alert import (
    ALERT_TYPES, create_alert, list_alerts, get_alert,
    update_alert, delete_alert, check_alerts,
)

bp = Blueprint("alert", __name__, url_prefix="/api")


@bp.route("/alert/types")
def api_alert_types():
    """获取支持的预警类型"""
    return jsonify({"types": ALERT_TYPES})


@bp.route("/alert", methods=["GET"])
def api_list_alerts():
    """列出所有预警规则"""
    return jsonify({"alerts": list_alerts()})


@bp.route("/alert/<int:alert_id>", methods=["GET"])
def api_get_alert(alert_id):
    """获取单条预警规则"""
    alert = get_alert(alert_id)
    if not alert:
        return jsonify({"error": "not found"}), 404
    return jsonify(alert)


@bp.route("/alert", methods=["POST"])
def api_create_alert():
    """创建预警规则

    Body: {"type": "price_break", "params": {"code":"000001","threshold":15,"direction":"above"}, "enabled": true}
    """
    data = request.get_json(force=True, silent=True) or {}
    alert_type = data.get("type", "")
    params = data.get("params", {})
    enabled = data.get("enabled", True)

    if alert_type not in ALERT_TYPES:
        return jsonify({"error": f"unknown type: {alert_type}", "available": list(ALERT_TYPES.keys())}), 400

    result = create_alert(alert_type, params, enabled)
    if "error" in result:
        return jsonify(result), 400
    return jsonify(result), 201


@bp.route("/alert/<int:alert_id>", methods=["PUT"])
def api_update_alert(alert_id):
    """更新预警规则"""
    data = request.get_json(force=True, silent=True) or {}
    result = update_alert(alert_id, data)
    if not result:
        return jsonify({"error": "not found"}), 404
    return jsonify(result)


@bp.route("/alert/<int:alert_id>", methods=["DELETE"])
def api_delete_alert(alert_id):
    """删除预警规则"""
    if delete_alert(alert_id):
        return jsonify({"message": "deleted"})
    return jsonify({"error": "not found"}), 404


@bp.route("/alert/check", methods=["POST"])
def api_check_alerts():
    """手动触发预警检查 (也可由 cron 调用)"""
    force = request.args.get("force", "false").lower() == "true"
    triggered = check_alerts(force=force)
    return jsonify({
        "triggered": triggered,
        "count": len(triggered),
        "timestamp": request.args.get("t", ""),
    })
