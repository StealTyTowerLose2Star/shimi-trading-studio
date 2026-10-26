"""
拾米交易工作室 - 市场数据 API
"""
from flask import Blueprint, jsonify

from cache import cache_or_fetch, cache_get
from data.fetcher import (
    fetch_indices, fetch_sectors, fetch_sector_flow,
    fetch_hot_stocks, fetch_limit_up, fetch_sentiment,
    get_stock_basic, get_latest_date,
)

bp = Blueprint("market", __name__, url_prefix="/api")


def _is_error_payload(data):
    """cache_or_fetch 会把上游异常转成 {"error": ...} 普通返回体 — 识别之,
    不得把错误字典当正常行情/情绪数据下发"""
    return isinstance(data, dict) and "error" in data


def _market_json(key, fn, ttl):
    """只读行情端点统一错误契约: 上游失败 (cache_or_fetch 返回 error 字典,
    或缓存层自身直接抛异常) → 502 + 可读 error; 有效数据(含合法空列表) → 200 原样"""
    try:
        data = cache_or_fetch(key, fn, ttl)
    except Exception as e:
        return jsonify({"error": f"{key} 获取失败: {str(e)[:100]}"}), 502
    if _is_error_payload(data):
        return jsonify({"error": str(data["error"])[:120]}), 502
    return jsonify(data)


@bp.route("/health")
def health():
    # 存活检查不触发真实数据拉取，也不等价于数据源已接通。
    return jsonify({"status": "ok", "service_status": "ready",
                    "market_data_status": "not_checked", "studio": "拾米交易工作室",
                    "latest_trade_date": None,
                    "note": "仅服务存活，行情状态未知；不执行外部探测"})


@bp.route("/indices")
def api_indices():
    return _market_json("indices", fetch_indices, 30)


@bp.route("/sectors")
def api_sectors():
    return _market_json("sectors", fetch_sectors, 120)


@bp.route("/sector-flow")
def api_sector_flow():
    return _market_json("sector_flow", fetch_sector_flow, 60)


@bp.route("/hot-stocks")
def api_hot_stocks():
    return _market_json("hot_stocks", fetch_hot_stocks, 30)


@bp.route("/limit-up")
def api_limit_up():
    return _market_json("limit_up", fetch_limit_up, 60)


@bp.route("/sentiment")
def api_sentiment():
    return _market_json("sentiment", fetch_sentiment, 30)


@bp.route("/stock/lookup")
def api_stock_lookup():
    """股票代码搜索 → 返回代码+名称"""
    from flask import request
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify([])
    try:
        basic = get_stock_basic()
    except Exception as error:
        return jsonify(error=f"股票名录获取失败: {str(error)[:100]}"), 502
    if not isinstance(basic, dict) or "error" in basic or not basic:
        return jsonify(error="股票名录未取得，不能把失败当作无匹配"), 502
    results = []
    for code, info in basic.items():
        short = code.replace(".SZ", "").replace(".SH", "").replace(".BJ", "")
        name = info.get("name", "")
        if short.startswith(q) or (name and name.startswith(q)):
            results.append({"code": short, "name": name, "ts_code": code})
            if len(results) >= 10:
                break
    return jsonify(results)


@bp.route("/dashboard")
def api_dashboard():
    """聚合所有市场+策略数据 (带容错降级)"""
    import time
    start = time.time()

    result = {}
    errors = []

    # 每项独立容错: 单点故障不影响整体;
    # cache_or_fetch 会把异常转成 {"error": ...} 返回体, 必须显式识别,
    # 否则 _errors 永远为空、错误字典被当正常数据下发
    def safe_fetch(key, fn, ttl):
        try:
            data = cache_or_fetch(key, fn, ttl)
        except Exception as e:
            errors.append({"key": key, "error": str(e)[:80]})
            return None
        if _is_error_payload(data):
            errors.append({"key": key, "error": str(data["error"])[:80]})
            return None
        return data

    result["indices"] = safe_fetch("indices", fetch_indices, 30)
    result["sectors"] = safe_fetch("sectors", fetch_sectors, 120)
    result["sector_flow"] = safe_fetch("sector_flow", fetch_sector_flow, 60)
    result["limit_up"] = safe_fetch("limit_up", fetch_limit_up, 60)
    result["sentiment"] = safe_fetch("sentiment", fetch_sentiment, 30)

    # 策略扫描是全市场重计算 (2026-10-25起经 /api/strategy/<name>[/refresh]
    # 触发并按数据日缓存, 结果写穿 cache.py); dashboard 只读既有缓存不为聚合
    # 阻塞数分钟 — 缓存未生成时该项 None (前端显示"数据未取得", 不记错误)
    for _scan_key in ("strategy_trend", "strategy_hybrid", "strategy_dragon"):
        _scan_val = cache_get(_scan_key)
        result[_scan_key] = None if _is_error_payload(_scan_val) else _scan_val
    result["hot_stocks"] = safe_fetch("hot_stocks", fetch_hot_stocks, 30)

    total = round(time.time() - start, 1)
    if errors:
        print(f"[shimi] Dashboard degraded {total}s; failed keys: {[e['key'] for e in errors]}")
    else:
        print(f"[shimi] Dashboard {total}s")
    result["_errors"] = errors if errors else None
    resp = jsonify(result)
    if errors:
        resp.headers["X-Shimi-Degraded"] = "true"  # 显式降级标记 (聚合含成功项时仍200)
    return resp


# ─── 个股K线数据 ──────────────────────────────

@bp.route("/stock/<code>/kline")
def api_stock_kline(code):
    """个股日K线数据 (用于K线图表)

    Query: ?days=60 (默认60个交易日, 范围10~365)

    Returns:
        {"code": "000001", "name": "平安银行",
         "kline": [{"date":"2026-06-01","open":12.5,"high":13.0,"low":12.3,"close":12.8,"volume":1234567}, ...]}
    """
    from flask import request
    days = request.args.get("days", 60, type=int)
    days = min(max(days, 10), 365)

    try:
        from realtime_scorer import get_kline
        df = get_kline(code, days=days)
    except Exception as e:
        # 上游缺连接/权限/安全传输，不是应用内部错误或合法空数据。
        return jsonify({"error": str(e)[:120], "code": code,
                        "data_status": "unavailable"}), 502

    try:
        if df is None or len(df) == 0:
            return jsonify({"error": "无数据", "code": code}), 404

        kline_data = []
        for _, row in df.iterrows():
            kline_data.append({
                "date": str(row.get("date", row.get("trade_date", "")))[:10],
                "open": round(float(row["open"]), 2),
                "high": round(float(row["high"]), 2),
                "low": round(float(row["low"]), 2),
                "close": round(float(row["close"]), 2),
                "volume": int(row.get("volume", row.get("vol", 0))),
            })

        name = code
        try:
            from data.fetcher import get_stock_basic
            basic = get_stock_basic()
            if isinstance(basic, dict):
                ts_code = code + (".SZ" if code.startswith(("0","3")) else ".SH")
                if code.startswith("9"): ts_code = code + ".BJ"
                info = basic.get(ts_code, {})
                name = info.get("name", code)
        except Exception:
            pass

        return jsonify({"code": code, "name": name, "days": len(kline_data), "kline": kline_data})
    except Exception as e:
        return jsonify({"error": str(e)[:100], "code": code}), 500
