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
    user = require_user()
    if not user:
        return unauthorized()
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

    from collections import defaultdict
    buckets = defaultdict(lambda: {"trades": 0, "won": 0, "pnl": 0.0})
    seen_dates = set()

    # 已平仓：按 exit 日期计入盈亏
    for t in trades:
        if not t.get("exit_price"):
            continue
        exit_date = t.get("date", "")
        if not exit_date:
            continue
        if period == "month":
            key = exit_date[:7]
        elif period == "year":
            key = exit_date[:4]
        else:
            key = exit_date

        entry_p = t.get("entry_price") or 0
        qty = t.get("qty") or 0
        exit_p = t.get("exit_price") or 0
        pnl = (exit_p - entry_p) * qty if t["direction"] == "buy" \
              else (entry_p - exit_p) * qty
        buckets[key]["trades"] += 1
        if pnl > 0:
            buckets[key]["won"] += 1
        buckets[key]["pnl"] += pnl
        seen_dates.add(key)

    # 持仓（未平仓）：按入场日期计入浮动盈亏
    # 尝试获取最新价计算浮动盈亏
    try:
        from position_manager import get_kline as _pm_get_kline
        _price_cache = {}
        for t in trades:
            if t.get("exit_price"):
                continue
            code = t["code"]
            if code not in _price_cache:
                try:
                    df = _pm_get_kline(code, days=10)
                    if df is not None and len(df) > 0:
                        _price_cache[code] = float(df["close"].iloc[-1])
                    else:
                        _price_cache[code] = None
                except Exception:
                    _price_cache[code] = None
    except Exception:
        _price_cache = {}

    for t in trades:
        if t.get("exit_price"):
            continue
        entry_date = t.get("date", "")
        if not entry_date:
            continue
        if period == "month":
            key = entry_date[:7]
        elif period == "year":
            key = entry_date[:4]
        else:
            key = entry_date

        entry_p = t.get("entry_price") or 0
        qty = t.get("qty") or 0
        cp = _price_cache.get(t["code"])
        if cp is not None:
            u_pnl = (cp - entry_p) * qty if t["direction"] == "buy" \
                   else (entry_p - cp) * qty
        else:
            u_pnl = 0

        if key not in seen_dates:
            buckets[key] = {"trades": 0, "won": 0, "pnl": 0.0}

        buckets[key]["pnl"] += u_pnl
        seen_dates.add(key)

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


# ─── 实时行情 ─────────────────────────────────

@bp.route("/positions/realtime", methods=["POST"])
def api_positions_realtime():
    """获取持仓实时行情（东方财富盘中数据）"""
    data = request.get_json(force=True, silent=True) or {}
    codes = data.get("codes", [])
    if not codes:
        return jsonify({"error": "no codes", "prices": {}})

    secids = []
    for code in codes:
        c = code.strip()
        if c.startswith(("0", "3")):
            secids.append(f"0.{c}")
        elif c.startswith("6"):
            secids.append(f"1.{c}")

    if not secids:
        return jsonify({"prices": {}})

    import time as _time
    try:
        from curl_cffi import requests as cffi_requests
        url = "http://80.push2.eastmoney.com/api/qt/ulist.np/get?fields=f2,f3,f12,f14&secids=" + ",".join(secids)
        r = cffi_requests.get(url, timeout=5)
        if r.status_code != 200:
            return jsonify({"prices": {}, "error": f"HTTP {r.status_code}"})
        raw = r.json()
    except ImportError:
        import requests as std_requests
        url = "http://80.push2.eastmoney.com/api/qt/ulist.np/get?fields=f2,f3,f12,f14&secids=" + ",".join(secids)
        r = std_requests.get(url, timeout=5)
        if r.status_code != 200:
            return jsonify({"prices": {}, "error": f"HTTP {r.status_code}"})
        raw = r.json()

    prices = {}
    for item in raw.get("data", {}).get("diff", []):
        raw_price = item.get("f2")
        raw_pct = item.get("f3")
        prices[item["f12"]] = {
            "price": round(raw_price / 100, 2) if raw_price else None,
            "change_pct": round(raw_pct / 100, 2) if raw_pct else None,
            "name": item.get("f14", ""),
        }

    return jsonify({"prices": prices, "timestamp": _time.strftime("%H:%M:%S")})
