"""
拾米交易工作室 - 操作建议 API
"""
from flask import Blueprint, jsonify
import pandas as pd

from cache import cache_or_fetch
from services.advice import generate_advice
from data.fetcher import get_kline
from position_manager import evaluate_position

bp = Blueprint("advice", __name__, url_prefix="/api")


@bp.route("/advice")
def api_advice():
    return jsonify(cache_or_fetch("advice", generate_advice, 120))


@bp.route("/positions/evaluate", methods=["POST"])
def api_evaluate_positions():
    """批量评估持仓，返回动态止损/目标"""
    from flask import request
    import time
    data = request.get_json(force=True, silent=True) or {}
    positions = data.get("positions", [])
    if not positions:
        return jsonify({"error": "no positions", "results": []})
    from position_manager import batch_evaluate
    results = batch_evaluate(positions)
    return jsonify({"results": results, "timestamp": time.strftime("%H:%M:%S")})


@bp.route("/portfolio/advice")
def api_portfolio_advice():
    """持仓分析：对用户已持有的个股跑策略评分"""
    from db import get_trades
    from realtime_scorer import trend_detect, hybrid_score, dragon_leader_score
    from .auth import require_user, unauthorized

    user = require_user()
    if not user:
        return unauthorized()

    trades = get_trades(user_id=user["id"])
    open_trades = [t for t in trades if not t.get("exit_price")]

    if not open_trades:
        return jsonify({"positions": [], "summary": {"total": 0, "advice": "无持仓"}})

    positions = []
    total_invested = 0
    total_pnl = 0

    for t in open_trades:
        code = t["code"]
        entry = t["entry_price"]
        qty = t["qty"]
        invested = entry * qty
        total_invested += invested

        current_price = entry
        try:
            kline = get_kline(code, days=30)
            if kline is not None and len(kline) > 0:
                current_price = float(kline["close"].iloc[-1])
        except:
            pass

        pnl = (current_price - entry) * qty if t["direction"] == "buy" else (entry - current_price) * qty
        pnl_pct = (current_price - entry) / entry * 100 if t["direction"] == "buy" else (entry - current_price) / entry * 100
        total_pnl += pnl

        trend = None
        try:
            trend = trend_detect(code)
        except:
            pass
        hybrid = None
        try:
            hybrid = hybrid_score(code)
        except:
            pass
        dragon = None
        try:
            dragon = dragon_leader_score(code)
        except:
            pass

        tb = trend and trend["total_score"] >= 50
        hb = hybrid and hybrid["score"] >= 45
        db = dragon and dragon["leader_score"] >= 40
        sc = sum([tb, hb, db])
        safe = pnl_pct > 0

        if sc >= 2 and safe:
            advice = "持有 ✅"
            parts = []
            if tb and trend:
                parts.append(f"趋势{trend['total_score']}分 {trend['stage']}")
            if hb and hybrid:
                parts.append(f"混合{hybrid['score']}分 {hybrid['grade']}")
            if safe:
                parts.append(f"浮盈+{round(pnl_pct,1)}%")
            reason = " ".join(parts)
        elif sc >= 1 or safe:
            advice = "观望 ⏳"
            reason = f"信号偏弱，浮盈{round(pnl_pct,1)}%"
        else:
            advice = "减仓 ⚠️"
            reason = f"策略看空，浮盈{round(pnl_pct,1)}%"

        sl_label = "--"
        try:
            ev = evaluate_position(code, entry, t["direction"])
            if ev and "stop_loss_label" in ev:
                sl_label = ev["stop_loss_label"]
        except:
            pass

        positions.append({
            "code": code, "name": t.get("name", ""),
            "entry_price": round(entry, 2), "current_price": round(current_price, 2),
            "qty": qty, "invested": round(invested, 2),
            "pnl": round(pnl, 2), "pnl_pct": round(pnl_pct, 1),
            "stop_loss": sl_label, "advice": advice, "reason": reason,
            "trend_score": trend["total_score"] if trend else None,
            "trend_stage": trend["stage"] if trend else None,
            "hybrid_score": hybrid["score"] if hybrid else None,
            "hybrid_grade": hybrid["grade"] if hybrid else None,
            "dragon_score": dragon["leader_score"] if dragon else None,
            "dragon_grade": dragon["grade"] if dragon else None,
        })

    risk = "低"
    if total_pnl < -total_invested * 0.05:
        risk = "高⚠️"
    elif total_pnl < 0:
        risk = "中"

    return jsonify({
        "positions": positions,
        "summary": {
            "total_positions": len(open_trades),
            "total_invested": round(total_invested, 2),
            "total_pnl": round(total_pnl, 2),
            "total_pnl_pct": round(total_pnl / total_invested * 100, 1) if total_invested > 0 else 0,
            "risk": risk,
        }
    })
