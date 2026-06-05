"""海淘美股 - API 蓝图
对齐拾米 api/trade.py 风格
路由前缀: /api/us
"""
import logging
from flask import Blueprint, jsonify, request

from haitao.us_fetcher import (
    get_indices, get_hot_stocks, get_chinese_adr,
    get_us_market_status, get_pre_post_market,
    get_quotes, get_history, calc_technical_indicators,
    get_us_dashboard, clear_cache,
)
from haitao.us_scanner import (
    score_stock, scan_watchlist, scan_top_gainers, scan_adr_picks,
)
from haitao.us_position import (
    evaluate_us_position, batch_evaluate_us,
)
from haitao.us_trade_db import (
    add_us_trade, update_us_trade, delete_us_trade,
    get_us_trades, get_us_trade_summary,
)

logger = logging.getLogger(__name__)
bp = Blueprint("haitao", __name__, url_prefix="/api/us")


def _require_user():
    from api.auth import require_user
    return require_user()

def _unauthorized():
    from api.auth import unauthorized
    return unauthorized()


# ─── 市场数据 ──────────────────────────────────

@bp.route("/status")
def api_us_status():
    """美股市场状态"""
    return jsonify(get_us_market_status())

@bp.route("/indices")
def api_us_indices():
    """美股指数"""
    return jsonify(get_indices())

@bp.route("/hot")
def api_us_hot():
    """热门美股"""
    return jsonify(get_hot_stocks())

@bp.route("/adr")
def api_us_adr():
    """中概股"""
    return jsonify(get_chinese_adr())

@bp.route("/prepost")
def api_us_prepost():
    """盘前/盘后"""
    return jsonify(get_pre_post_market())

@bp.route("/quotes")
def api_us_quotes():
    """批量报价 ?tickers=AAPL,MSFT"""
    ts = request.args.get("tickers", "")
    if not ts:
        return jsonify({"error": "need ?tickers=..."}), 400
    tickers = [t.strip().upper() for t in ts.split(",") if t.strip()]
    return jsonify(get_quotes(tickers))

@bp.route("/quote/<ticker>")
def api_us_quote(ticker: str):
    """个股详情+技术指标+评分"""
    ticker = ticker.strip().upper()
    quotes = get_quotes([ticker])
    quote = quotes[0] if quotes else {"error": "no data"}
    tech = {}
    df = get_history(ticker)
    if df is not None:
        tech = calc_technical_indicators(df)
    score = score_stock(ticker)
    return jsonify({"quote": quote, "technicals": tech, "score": score})

@bp.route("/history/<ticker>")
def api_us_history(ticker: str):
    """历史K线 ?period=3mo&interval=1d"""
    ticker = ticker.strip().upper()
    period = request.args.get("period", "3mo")
    interval = request.args.get("interval", "1d")
    df = get_history(ticker, period=period, interval=interval)
    if df is None:
        return jsonify({"error": f"No data for {ticker}"}), 404
    records = []
    for _, row in df.iterrows():
        d = {}
        if hasattr(row, "name") and not isinstance(row.name, int):
            d["date"] = str(row.name)
        elif "Date" in df.columns:
            d["date"] = str(row.get("Date", ""))
        elif "Datetime" in df.columns:
            d["date"] = str(row.get("Datetime", ""))
        else:
            d["date"] = str(row.name)
        for col in ["Open", "High", "Low", "Close", "Volume"]:
            if col in df.columns:
                val = row[col]
                try:
                    d[col.lower()] = round(float(val), 2) if col != "Volume" else int(float(val))
                except (ValueError, TypeError):
                    d[col.lower()] = 0
        records.append(d)
    return jsonify({"ticker": ticker, "period": period, "data": records})

@bp.route("/dashboard")
def api_us_dashboard():
    """聚合首页"""
    return jsonify(get_us_dashboard())

# ─── 扫描分析 ──────────────────────────────────

@bp.route("/scan")
def api_us_scan():
    """扫描 ?mode=hot|adr|gainers"""
    mode = request.args.get("mode", "hot")
    if mode == "adr":
        results = scan_adr_picks()
    elif mode == "gainers":
        results = scan_top_gainers()
    else:
        from haitao.config import HOT_US_STOCKS
        results = scan_watchlist(HOT_US_STOCKS)
    return jsonify({"mode": mode, "count": len(results), "results": results})

# ─── 掘金 API ──────────────────────────────────

from haitao.us_gold_scanner import gold_score, gold_pan, gold_pan_hot, gold_pan_adr, gold_pan_top_gainers, gold_report

@bp.route("/gold/score/<ticker>")
def api_us_gold_score(ticker: str):
    """个股黄金评分"""
    return jsonify(gold_score(ticker.strip().upper()))

@bp.route("/gold/pan")
def api_us_gold_pan():
    """黄金挖掘 ?mode=hot|adr|gainers"""
    mode = request.args.get("mode", "hot")
    if mode == "adr":
        results = gold_pan_adr()
    elif mode == "gainers":
        results = gold_pan_top_gainers()
    else:
        from haitao.config import HOT_US_STOCKS
        results = gold_pan(HOT_US_STOCKS)
    return jsonify({"mode": mode, "count": len(results), "results": results})

@bp.route("/gold/report")
def api_us_gold_report():
    """掘金报告——最值得买的排行"""
    return jsonify(gold_report())

@bp.route("/score/<ticker>")
def api_us_score(ticker: str):
    """个股评分"""
    return jsonify(score_stock(ticker.strip().upper()))

# ─── 持仓评估 ──────────────────────────────────

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

# ─── 交易 CRUD ──────────────────────────────────

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

# ─── 搜索 ──────────────────────────────────────

@bp.route("/search")
def api_us_search():
    q = request.args.get("q", "").strip().lower()
    if len(q) < 1:
        return jsonify([])
    try:
        import yfinance as yf
        results = []
        common = ["AAPL","MSFT","GOOGL","AMZN","META","NVDA","TSLA",
                  "AMD","AVGO","PLTR","BABA","JD","PDD","NIO","SPY","QQQ",
                  "QQQM","VOO","VTI","IWM","SOXX","XLF","XLE","XLK",
                  "COIN","MSTR","SNAP","UBER","LYFT","DASH","HOOD"]
        for t in common:
            if q in t.lower():
                info = yf.Ticker(t).info
                results.append({
                    "ticker": t,
                    "name": info.get("shortName", info.get("longName", t)),
                    "sector": info.get("sector", ""),
                    "price": info.get("regularMarketPrice", 0),
                })
                if len(results) >= 10:
                    break
        return jsonify(results)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@bp.route("/cache/clear", methods=["POST"])
def api_us_clear_cache():
    clear_cache()
    return jsonify({"status": "ok", "message": "海淘缓存已清除"})
