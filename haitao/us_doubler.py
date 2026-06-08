"""
HiTao 美股 - 翻倍股猎手蓝图
路由前缀: /api/us
端点: doubler/scan, doubler/recommend, doubler/score/*, doubler/predict, doubler/track/*
"""

import logging
from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)
bp = Blueprint("haito_doubler", __name__, url_prefix="/api/us")


@bp.route("/doubler/scan")
def api_us_doubler_scan():
    ts = request.args.get("tickers", "")
    if ts:
        tickers = [t.strip().upper() for t in ts.split(",") if t.strip()]
    else:
        from haitao.config import DOUBLER_SEED_POOL
        tickers = DOUBLER_SEED_POOL
    from haitao.us_doubler_scanner import scan_doublers
    results = scan_doublers(tickers)
    return jsonify({"mode": "custom" if ts else "seed_pool", "count": len(results), "results": results})

@bp.route("/doubler/recommend")
def api_us_doubler_recommend():
    from haitao.us_doubler_scanner import recommend_doublers
    return jsonify(recommend_doublers())

@bp.route("/doubler/score/<ticker>")
def api_us_doubler_score(ticker: str):
    from haitao.us_doubler_scanner import score_doubler
    return jsonify(score_doubler(ticker.strip().upper()))

@bp.route("/doubler/predict")
def api_us_doubler_predict():
    ts = request.args.get("tickers", "")
    if ts:
        tickers = [t.strip().upper() for t in ts.split(",") if t.strip()]
    else:
        from haitao.config import DOUBLER_SEED_POOL
        tickers = DOUBLER_SEED_POOL
    from haitao.us_doubler_predictor import predict_batch
    results = predict_batch(tickers)
    return jsonify({"count": len(results), "results": results})

@bp.route("/doubler/track/start", methods=["POST"])
def api_us_doubler_track_start():
    from haitao.us_doubler_scanner import recommend_doublers
    from haitao.us_doubler_tracker import save_recommendation
    recommend = recommend_doublers()
    result = save_recommendation(recommend)
    return jsonify(result)

@bp.route("/doubler/track/status")
def api_us_doubler_track_status():
    from haitao.us_doubler_tracker import get_tracking_status
    return jsonify(get_tracking_status())

@bp.route("/doubler/track/update", methods=["POST"])
def api_us_doubler_track_update():
    from haitao.us_doubler_tracker import update_prices
    result = update_prices()
    return jsonify(result)

@bp.route("/doubler/track/report")
def api_us_doubler_track_report():
    month = request.args.get("month", "")
    from haitao.us_doubler_tracker import get_monthly_report
    return jsonify(get_monthly_report(month or None))

@bp.route("/doubler/track/verify", methods=["POST"])
def api_us_doubler_track_verify():
    month = request.args.get("month", "")
    from haitao.us_doubler_tracker import verify_month
    return jsonify(verify_month(month or None))
