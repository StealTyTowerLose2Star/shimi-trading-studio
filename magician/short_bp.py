"""
Magician 美股 — 做空API蓝图
路由前缀: /api/magician
"""

import logging
from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)
bp = Blueprint("magician_short", __name__, url_prefix="/api/magician")


@bp.route("/short/scan")
def api_short_scan():
    ts = request.args.get("tickers", "")
    if ts:
        tickers = [t.strip().upper() for t in ts.split(",") if t.strip()]
    else:
        from magician.config import DOUBLER_SEED_POOL
        tickers = DOUBLER_SEED_POOL
    from magician.short_finder import scan_short_candidates
    results = scan_short_candidates(tickers)
    return jsonify({"mode": "custom" if ts else "seed_pool", "count": len(results), "results": results})

@bp.route("/short/score/<ticker>")
def api_short_score(ticker: str):
    from magician.short_finder import find_short_opportunities
    results = find_short_opportunities([ticker.strip().upper()])
    return jsonify(results[0] if results else {"ticker": ticker, "error": "no data"})
