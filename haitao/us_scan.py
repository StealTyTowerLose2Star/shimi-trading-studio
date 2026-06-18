"""
HiTao 美股 - 扫描评分蓝图
路由前缀: /api/us
端点: scan, score, gold/*, screener/*, analyze/*
"""

import logging
from flask import Blueprint, jsonify, request

from haitao.services.scanner import (
    score_stock, scan_watchlist, scan_top_gainers, scan_adr_picks,
)

logger = logging.getLogger(__name__)
bp = Blueprint("haitao_scan", __name__, url_prefix="/api/us")


# ─── 扫描分析 ────────────────────────────────

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

@bp.route("/score/<ticker>")
def api_us_score(ticker: str):
    """个股评分"""
    return jsonify(score_stock(ticker.strip().upper()))


# ─── 掘金 API ──────────────────────────────────

from haitao.us_gold_scanner import (
    gold_score, gold_pan, gold_pan_hot, gold_pan_adr, gold_pan_top_gainers,
)

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


# ─── 全市场扫描 API ──────────────────────────

from haitao.us_screener import (
    full_scan, get_all_stocks, get_latest_results,
)

@bp.route("/screener/stats")
def api_screener_stats():
    """全市场统计"""
    stocks = get_all_stocks()
    latest = get_latest_results()
    return jsonify({
        "total_stocks": len(stocks),
        "latest_scan": latest.get("timestamp") if latest else None,
        "scan_progress": latest.get("progress", 0) if latest else 0,
        "total_picks": len(latest.get("results", [])) if latest else 0,
    })

@bp.route("/screener/run")
def api_screener_run():
    """执行一次扫描（300只/批）"""
    result = full_scan(max_batches=1)
    if result:
        picks = len(result.get("results", []))
        golds = len(result.get("gold_picks", []))
        return jsonify({"status": "ok", "total_picks": picks, "gold_picks": golds})
    return jsonify({"error": "Scan failed"}), 500

@bp.route("/screener/results")
def api_screener_results():
    """获取最新扫描结果"""
    latest = get_latest_results()
    if latest:
        return jsonify(latest)
    return jsonify({"error": "No scan results yet"}), 404

@bp.route("/screener/top")
def api_screener_top():
    """获取Top金矿推荐"""
    latest = get_latest_results()
    if not latest:
        return jsonify({"error": "No scan results"}), 404
    return jsonify({
        "timestamp": latest.get("timestamp"),
        "golds": latest.get("gold_picks", [])[:10],
        "silvers": latest.get("silver_picks", [])[:15],
        "total_scanned": latest.get("total_scanned", 0),
    })


# ─── 深度分析 API ──────────────────────────

from haitao.gold_analyzer import analyze_gold_pick, analyze_top_picks, generate_report

@bp.route("/analyze/report")
def api_analyze_report():
    """生成完整投资报告（评分细分 + 止盈止损 + 仓位建议）"""
    report = generate_report()
    return jsonify(report)

@bp.route("/analyze/<symbol>")
def api_analyze_symbol(symbol):
    """单只股票深度分析"""
    result = analyze_gold_pick(symbol.strip().upper())
    return jsonify(result)
