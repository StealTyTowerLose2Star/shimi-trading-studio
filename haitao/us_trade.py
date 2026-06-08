"""
HiTao 美股 - 交易与持仓蓝图
路由前缀: /api/us
端点: trades/*, position/*, pnl-report
"""

import logging
from flask import Blueprint, jsonify, request

from haitao.services.portfolio import evaluate_us_position, batch_evaluate_us
from haitao.us_trade_db import (
    add_us_trade, update_us_trade, delete_us_trade,
    get_us_trades, get_us_trade_summary,
)
from haitao.services.pnl import calculate_pnl

logger = logging.getLogger(__name__)
bp = Blueprint("haitao_trade", __name__, url_prefix="/api/us")


def _require_user():
    from api.auth import require_user
    return require_user()

def _unauthorized():
    from api.auth import unauthorized
    return unauthorized()


# ─── 持仓评估 ────────────────────────────────

@bp.route("/position/evaluate", methods=["POST"])
def api_us_evaluate_position():
    data = request.get_json(force=True, silent=True) or {}
    ticker = data.get("ticker", "").strip().upper()
    if not ticker:
        return jsonify({"error": "ticker required"}), 400
    return jsonify(evaluate_us_position(
        ticker=ticker,
        entry_price=float(data.get("entry_price", 0)),
        direction=data.get("direction", "buy"),
        qty=int(data.get("qty", 100)),
        entry_date=data.get("entry_date"),
    ))

@bp.route("/positions/evaluate", methods=["POST"])
def api_us_batch_evaluate():
    data = request.get_json(force=True, silent=True) or {}
    positions = data.get("positions", [])
    if not positions:
        return jsonify({"error": "positions required"}), 400
    return jsonify({"positions": batch_evaluate_us(positions)})


# ─── 交易 CRUD ───────────────────────────────

@bp.route("/trades", methods=["GET"])
def api_us_get_trades():
    user = _require_user()
    if not user:
        return _unauthorized()
    trades = get_us_trades(user_id=user["id"])
    return jsonify({"trades": trades, "summary": get_us_trade_summary(user["id"])})

@bp.route("/trades", methods=["POST"])
def api_us_add_trade():
    user = _require_user()
    if not user:
        return _unauthorized()
    data = request.get_json(force=True, silent=True) or {}
    return jsonify(add_us_trade(user["id"], data))

@bp.route("/trades/<int:trade_id>", methods=["PUT"])
def api_us_update_trade(trade_id: int):
    user = _require_user()
    if not user:
        return _unauthorized()
    data = request.get_json(force=True, silent=True) or {}
    result = update_us_trade(trade_id, user["id"], data)
    if "error" in result:
        return jsonify(result), 404
    return jsonify(result)

@bp.route("/trades/<int:trade_id>", methods=["DELETE"])
def api_us_delete_trade(trade_id: int):
    user = _require_user()
    if not user:
        return _unauthorized()
    result = delete_us_trade(trade_id, user["id"])
    if "error" in result:
        return jsonify(result), 404
    return jsonify(result)


# ─── 盈亏追踪 ────────────────────────────────

@bp.route("/trades/pnl-report")
def api_us_pnl_report():
    """美股交易盈亏统计"""
    period = request.args.get("period", "month")
    return jsonify(calculate_pnl(period=period))
