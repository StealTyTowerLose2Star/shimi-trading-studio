"""
HiTao 美股 - 市场数据蓝图
路由前缀: /api/us
端点: status, indices, hot, adr, prepost, quotes, history, dashboard, search, cache
"""

import logging
from flask import Blueprint, jsonify, request

from haitao.us_fetcher import (
    get_indices, get_hot_stocks, get_chinese_adr,
    get_us_market_status, get_pre_post_market,
    get_quotes, get_history, calc_technical_indicators,
    get_us_dashboard, clear_cache,
)

logger = logging.getLogger(__name__)
bp = Blueprint("haitao_market", __name__, url_prefix="/api/us")


# ─── 市场状态 ────────────────────────────────

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
    from haitao.us_scanner import score_stock
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

@bp.route("/search")
def api_us_search():
    """搜索美股"""
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
