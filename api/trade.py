"""
拾米交易工作室 - 账户与交易 API
"""
from flask import Blueprint, jsonify, request

from db import (
    login_user, verify_token, register_user, list_users,
    add_trade, update_trade, delete_trade, get_trades, get_trade_summary,
)
from .auth import require_user, unauthorized

bp = Blueprint("trade", __name__, url_prefix="/api")


# ─── 认证 ──────────────────────────────────────

@bp.route("/auth/login", methods=["POST"])
def api_login():
    data = request.get_json(force=True, silent=True) or {}
    result = login_user(data.get("username", ""), data.get("password", ""))
    if "error" in result:
        return jsonify(result), 401
    return jsonify(result)


@bp.route("/auth/register", methods=["POST"])
def api_register():
    data = request.get_json(force=True, silent=True) or {}
    result = register_user(
        data.get("username", ""),
        data.get("password", ""),
        data.get("display_name"),
    )
    if "error" in result:
        return jsonify(result), 400
    return jsonify(result)


@bp.route("/auth/me")
def api_me():
    user = require_user()
    if not user:
        return unauthorized()
    return jsonify({"user": user})


@bp.route("/users")
def api_users():
    return jsonify(list_users())


# ─── 交易 CRUD ─────────────────────────────────

@bp.route("/trades", methods=["GET"])
def api_get_trades():
    user = require_user()
    if not user:
        return unauthorized()
    trades = get_trades(user_id=user["id"])
    return jsonify({"trades": trades, "summary": get_trade_summary(user["id"])})


@bp.route("/trades/pnl-report")
def api_pnl_report():
    """盈亏统计（按日/月/年）"""
    user = require_user()
    if not user:
        return unauthorized()
    period = request.args.get("period", "month")
    trades = get_trades(user_id=user["id"])
    closed = [t for t in trades if t.get("exit_price")]

    from collections import defaultdict
    buckets = defaultdict(lambda: {"trades": 0, "won": 0, "pnl": 0.0})

    for t in closed:
        exit_date = t.get("date", "")
        if not exit_date:
            continue
        if period == "month":
            key = exit_date[:7]
        elif period == "year":
            key = exit_date[:4]
        else:
            key = exit_date

        pnl = (t["exit_price"] - t["entry_price"]) * t["qty"] if t["direction"] == "buy" \
              else (t["entry_price"] - t["exit_price"]) * t["qty"]
        buckets[key]["trades"] += 1
        if pnl > 0:
            buckets[key]["won"] += 1
        buckets[key]["pnl"] += pnl

    result = []
    for k in sorted(buckets, reverse=True):
        b = buckets[k]
        result.append({
            "period": k,
            "trades": b["trades"],
            "won": b["won"],
            "win_rate": round(b["won"] / b["trades"] * 100, 1) if b["trades"] > 0 else 0,
            "pnl": round(b["pnl"], 2),
        })

    return jsonify({"period": period, "report": result})


@bp.route("/trades", methods=["POST"])
def api_add_trade():
    user = require_user()
    if not user:
        return unauthorized()
    data = request.get_json(force=True, silent=True) or {}
    result = add_trade(user["id"], data)
    return jsonify(result)


@bp.route("/trades/<int:trade_id>", methods=["PUT"])
def api_update_trade(trade_id):
    user = require_user()
    if not user:
        return unauthorized()
    data = request.get_json(force=True, silent=True) or {}
    result = update_trade(trade_id, user["id"], data)
    if "error" in result:
        return jsonify(result), 404
    return jsonify(result)


@bp.route("/trades/<int:trade_id>", methods=["DELETE"])
def api_delete_trade(trade_id):
    user = require_user()
    if not user:
        return unauthorized()
    result = delete_trade(trade_id, user["id"])
    if "error" in result:
        return jsonify(result), 404
    return jsonify(result)
