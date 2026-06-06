"""
拾米交易工作室 - A股策略后台服务 (tushare 数据源)
Backend for ShiMi Trading Studio
"""
import sys, os, json, time, threading
sys.path.insert(0, os.path.dirname(__file__))

from flask import Flask, jsonify
from flask_cors import CORS
import pandas as pd
import numpy as np
from flask.json.provider import DefaultJSONProvider
import config

class NumpyJSONProvider(DefaultJSONProvider):
    def default(self, obj):
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, (np.bool_,)):
            return bool(obj)
        if isinstance(obj, (np.ndarray,)):
            return obj.tolist()
        return super().default(obj)

from realtime_scorer import (
    trend_detect, hybrid_score, dragon_leader_score,
    get_kline, get_kline_batch, _count_consecutive_limit,
)
from position_manager import batch_evaluate, evaluate_position
from db import (login_user, verify_token, register_user, list_users,
                add_trade, update_trade, delete_trade, get_trades, get_trade_summary)

app = Flask(__name__)
app.json = NumpyJSONProvider(app)
app.static_folder = os.path.dirname(os.path.abspath(__file__))
app.static_url_path = ""
CORS(app)

# 注册海淘美股蓝图
try:
    from haitao.api import bp as haitao_bp
    app.register_blueprint(haitao_bp)
    print("✅ 海淘美股模块已加载")
except Exception as e:
    print(f"⚠️ 海淘模块加载失败: {e}")


from services.strategy import run_trend_scan, run_hybrid_scan, run_dragon_scan
from services.advice import generate_advice, calc_atr_based_levels
from data.fetcher import (
    fetch_indices, fetch_sectors, fetch_sector_flow,
    fetch_hot_stocks, fetch_sentiment, fetch_limit_up,
    get_latest_date, get_stock_basic, get_daily, get_daily_basic, get_ts,
)
from cache import cache_or_fetch, cache_delete, cache_clear
def api_advice():
    return jsonify(cache_or_fetch("advice", generate_advice, 600))


@app.route("/api/positions/evaluate", methods=["POST"])
def api_evaluate_positions():
    """批量评估持仓，返回动态止损/目标"""
    from flask import request
    data = request.get_json(force=True, silent=True) or {}
    positions = data.get("positions", [])
    if not positions:
        return jsonify({"error": "no positions", "results": []})
    results = batch_evaluate(positions)
    return jsonify({"results": results, "timestamp": time.strftime("%H:%M:%S")})


def _get_margin_info(code):
    """获取个股融资融券数据"""
    try:
        from realtime_scorer import get_ts
        pro = get_ts()
        ts_code = code + (".SZ" if code.startswith(("0","3")) else ".SH")
        df = pro.margin_detail(ts_code=ts_code, limit=2)
        if df is not None and not df.empty:
            row = df.iloc[-1]
            return {"rzye": float(row.get("rzye",0))/1e8, "rqye": float(row.get("rqye",0))/1e8,
                    "date": str(row.get("trade_date",""))}
    except:
        pass
    return None


@app.route("/api/positions/realtime", methods=["POST"])
def api_positions_realtime():
    """获取持仓实时行情（东方财富盘中数据）"""
    from flask import request
    data = request.get_json(force=True, silent=True) or {}
    codes = data.get("codes", [])
    if not codes:
        return jsonify({"error": "no codes", "prices": {}})
    
    from curl_cffi import requests
    # 东方财富实时报价：一次查询多只股票
    secids = []
    for code in codes:
        c = code.strip()
        if c.startswith(("0","3")): secids.append(f"0.{c}")
        elif c.startswith("6"): secids.append(f"1.{c}")
    
    if not secids:
        return jsonify({"prices": {}})
    
    url = "http://80.push2.eastmoney.com/api/qt/ulist.np/get?fields=f2,f3,f12,f14&secids=" + ",".join(secids)
    try:
        r = requests.get(url, impersonate="chrome110", timeout=10)
        items = r.json().get("data",{}).get("diff",[])
        prices = {}
        for item in items:
            code = item.get("f12","")
            prices[code] = {"price": round(item.get("f2",0)/100,2), "change": round(item.get("f3",0)/100,2)}
        return jsonify({"prices": prices})
    except Exception as e:
        return jsonify({"error": str(e), "prices": {}})


@app.route("/api/portfolio/advice")
@app.route("/api/portfolio/advice")
def api_portfolio_advice():
    user = _require_user()
    if not user:
        return _unauthorized()
    from services.portfolio import analyze_portfolio
    result = analyze_portfolio(user["id"])
    return jsonify(result)


# ─── 账户与交易 API ─────────────────────────────────

def _require_user():
    """从请求头获取当前用户"""
    from flask import request
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
        user = verify_token(token)
        if user:
            return user
    return None


def _unauthorized():
    return jsonify({"error": "未登录或登录已过期"}), 401


@app.route("/api/auth/login", methods=["POST"])
def api_login():
    from flask import request
    data = request.get_json(force=True, silent=True) or {}
    username = data.get("username", "")
    password = data.get("password", "")
    result = login_user(username, password)
    if "error" in result:
        return jsonify(result), 401
    return jsonify(result)


@app.route("/api/auth/register", methods=["POST"])
def api_register():
    from flask import request
    data = request.get_json(force=True, silent=True) or {}
    result = register_user(
        data.get("username", ""),
        data.get("password", ""),
        data.get("display_name")
    )
    if "error" in result:
        return jsonify(result), 400
    return jsonify(result)


@app.route("/api/auth/me")
def api_me():
    user = _require_user()
    if not user:
        return _unauthorized()
    return jsonify({"user": user})


@app.route("/api/users")
def api_users():
    return jsonify(list_users())


@app.route("/api/trades", methods=["GET"])
def api_get_trades():
    user = _require_user()
    if not user:
        return _unauthorized()
    trades = get_trades(user_id=user["id"])
    return jsonify({"trades": trades, "summary": get_trade_summary(user["id"])})


@app.route("/api/trades/pnl-report")
def api_pnl_report():
    from flask import request
    user = _require_user()
    if not user:
        return _unauthorized()
    period = request.args.get("period", "month")
    from services.pnl import compute_pnl_report
    result = compute_pnl_report(user_id=user["id"])
    return jsonify({"period": period, "report": result})


@app.route("/api/trades", methods=["POST"])
def api_add_trade():
    user = _require_user()
    if not user:
        return _unauthorized()
    from flask import request
    data = request.get_json(force=True, silent=True) or {}
    result = add_trade(user["id"], data)
    return jsonify(result)


@app.route("/api/trades/<int:trade_id>", methods=["PUT"])
def api_update_trade(trade_id):
    user = _require_user()
    if not user:
        return _unauthorized()
    from flask import request
    data = request.get_json(force=True, silent=True) or {}
    result = update_trade(trade_id, user["id"], data)
    if "error" in result:
        return jsonify(result), 404
    return jsonify(result)


@app.route("/api/trades/<int:trade_id>", methods=["DELETE"])
def api_delete_trade(trade_id):
    user = _require_user()
    if not user:
        return _unauthorized()
    result = delete_trade(trade_id, user["id"])
    if "error" in result:
        return jsonify(result), 404
    return jsonify(result)


@app.route("/api/health")
def health():
    date = get_latest_date()
    return jsonify({"status": "ok", "studio": "拾米交易工作室", "latest_trade_date": date})


@app.route("/api/indices")
def api_indices():
    return jsonify(cache_or_fetch("indices", fetch_indices, 30))


@app.route("/api/sectors")
def api_sectors():
    return jsonify(cache_or_fetch("sectors", fetch_sectors, 120))


@app.route("/api/sector-flow")
def api_sector_flow():
    return jsonify(cache_or_fetch("sector_flow", fetch_sector_flow, 60))


@app.route("/api/hot-stocks")
def api_hot_stocks():
    return jsonify(cache_or_fetch("hot_stocks", fetch_hot_stocks, 30))


@app.route("/api/stock/lookup")
def api_stock_lookup():
    """股票代码搜索 → 返回代码+名称"""
    from flask import request
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify([])
    basic = get_stock_basic()
    if not isinstance(basic, dict):
        return jsonify([])
    results = []
    for code, info in basic.items():
        short = code.replace(".SZ","").replace(".SH","").replace(".BJ","")
        name = info.get("name", "")
        # 匹配代码或名称前缀
        if short.startswith(q) or (name and name.startswith(q)):
            results.append({"code": short, "name": name, "ts_code": code})
            if len(results) >= 10:
                break
    return jsonify(results)


@app.route("/api/limit-up")
def api_limit_up():
    return jsonify(cache_or_fetch("limit_up", fetch_limit_up, 60))


@app.route("/api/sentiment")
def api_sentiment():
    return jsonify(cache_or_fetch("sentiment", fetch_sentiment, 30))


@app.route("/api/strategy/<name>")
def api_strategy(name):
    if name not in ["trend", "hybrid", "dragon"]:
        return jsonify({"error": f"unknown strategy: {name}"}), 404
    fns = {"trend": run_trend_scan, "hybrid": run_hybrid_scan, "dragon": run_dragon_scan}
    return jsonify(cache_or_fetch(f"strategy_{name}", fns[name], 120))


@app.route("/api/strategy/<name>/refresh")
def api_strategy_refresh(name):
    if name not in ["trend", "hybrid", "dragon"]:
        return jsonify({"error": f"unknown strategy: {name}"}), 404
    cache_delete(f"strategy_{name}")
    fns = {"trend": run_trend_scan, "hybrid": run_hybrid_scan, "dragon": run_dragon_scan}
    result = fns[name]()
    from cache import cache_set
    cache_set(f"strategy_{name}", result, 120)
    return jsonify(result)


@app.route("/api/dashboard")
def api_dashboard():
    start = time.time()
    logs = []

    def log_step(name):
        elapsed = round(time.time() - start, 1)
        logs.append(f"{name} ({elapsed}s)")
        print(f"[拾米] {name} ({elapsed}s)")

    result = {
        "indices": cache_or_fetch("indices", fetch_indices, 30),
    }
    log_step("指数")

    result["sectors"] = cache_or_fetch("sectors", fetch_sectors, 120)
    log_step("板块")

    result["sector_flow"] = cache_or_fetch("sector_flow", fetch_sector_flow, 60)
    log_step("资金流")

    result["limit_up"] = cache_or_fetch("limit_up", fetch_limit_up, 60)
    log_step("涨停板")

    result["sentiment"] = cache_or_fetch("sentiment", fetch_sentiment, 30)
    log_step("市场状态")

    result["strategy_trend"] = cache_or_fetch("strategy_trend", run_trend_scan, 120)
    log_step("趋势策略")

    result["strategy_hybrid"] = cache_or_fetch("strategy_hybrid", run_hybrid_scan, 120)
    log_step("混合策略")

    result["strategy_dragon"] = cache_or_fetch("strategy_dragon", run_dragon_scan, 120)
    log_step("龙头策略")

    result["hot_stocks"] = cache_or_fetch("hot_stocks", fetch_hot_stocks, 30)
    log_step("热门个股")

    total = round(time.time() - start, 1)
    print(f"[拾米] ✅ Dashboard 总耗时 {total}s")
    return jsonify(result)


@app.route("/")
def index():
    return app.send_static_file("index.html")


@app.route("/api/monitor")
def api_monitor():
    """服务器资源监控"""
    from monitor import get_monitor_status
    return jsonify(get_monitor_status())


# ═══════════════════════════════════════════════
# 翻倍股扫描 API (services/doubler_scanner)
# ═══════════════════════════════════════════════
@app.route("/api/doubler/history")
def api_doubler_history():
    import json as _json
    fpath = os.path.join(os.path.dirname(__file__), "monthly_doublers.json")
    if os.path.exists(fpath):
        with open(fpath) as f:
            return jsonify(_json.load(f))
    return jsonify({"error": "no data, call /api/doubler/history/refresh first"})


@app.route("/api/doubler/history/refresh")
def api_doubler_history_refresh():
    from services.doubler_scanner import scan_monthly_doublers
    cache_delete("doubler_history")
    result = scan_monthly_doublers()
    return jsonify(result)


@app.route("/api/doubler/recommend")
def api_doubler_recommend():
    import json as _json, os as _os
    fpath = _os.path.join(_os.path.dirname(__file__), "doubler_prediction_cache.json")
    if _os.path.exists(fpath):
        with open(fpath) as f:
            return jsonify(_json.load(f))
    # fallback
    from services.doubler_scanner import recommend_current_month
    return jsonify(recommend_current_month())


@app.route("/api/doubler/recommend/refresh")
def api_doubler_recommend_refresh():
    cache_delete("doubler_recommend")
    try:
        from services.doubler_predictor import predict_monthly_doublers
        result = predict_monthly_doublers()
    except Exception as e:
        print(f"[doubler] predict failed, fallback: {e}")
        from services.doubler_scanner import recommend_current_month
        result = recommend_current_month()
        result["model"] = "fallback (tushare unavailable)"
    cache_set("doubler_recommend", result, 300)
    return jsonify(result)


@app.route("/api/doubler/plan/10k")
def api_doubler_plan_10k():
    from services.doubler_scanner import recommend_current_month, position_plan_10k
    recommend = cache_or_fetch("doubler_recommend", recommend_current_month, 300)
    if isinstance(recommend, dict) and "elite_picks" in recommend:
        plan = position_plan_10k(recommend["elite_picks"])
        return jsonify({"recommend_time": recommend.get("scan_time"), **plan})
    return jsonify({"error": "recommend data unavailable"})


# ═══════════════ 闭环跟踪 ═══════════════
@app.route("/api/doubler/track/start")
def api_track_start():
    from services.doubler_tracker import start_tracking
    return jsonify(start_tracking())

@app.route("/api/doubler/track/status")
def api_track_status():
    from services.doubler_tracker import get_tracking_status
    return jsonify(get_tracking_status())

@app.route("/api/doubler/track/update")
def api_track_update():
    from services.doubler_tracker import update_progress
    return jsonify(update_progress())

@app.route("/api/doubler/track/verify")
def api_track_verify():
    from services.doubler_tracker import verify_month
    return jsonify(verify_month())

@app.route("/api/doubler/track/effectiveness")
def api_track_effectiveness():
    from services.doubler_tracker import get_catalyst_effectiveness
    return jsonify(get_catalyst_effectiveness())


if __name__ == "__main__":
    print("🚀 拾米交易工作室 Backend (tushare) 启动中...")
    print(f"   Dashboard: http://localhost:7890")
    print(f"   API:       http://localhost:7890/api/dashboard")
    app.run(host="127.0.0.1", port=config.SERVER_PORT, debug=config.DEBUG)
