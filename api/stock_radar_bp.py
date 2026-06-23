"""
拾米交易工作室 - 个股雷达 API
归属: 🍚 拾米 A股

端点:
  GET /api/stock/<code>/radar          — 全维度分析
  GET /api/stock/<code>/radar/summary  — 精简摘要 (hover/列表卡片)
"""

from flask import Blueprint, jsonify
from cache import cache_or_fetch
from services.stock_radar import analyze_stock

bp = Blueprint("stock_radar", __name__, url_prefix="/api")


@bp.route("/stock/<code>/radar")
def api_stock_radar(code: str):
    """个股全维度雷达分析

    返回五大分析器结果 + 雷达评分 + 风险提示 + 综合结论。
    见 services/stock_radar.py 各分析器文档获取字段说明。

    缓存: 60秒 (行情级实时性)
    """
    result = cache_or_fetch(
        f"stock_radar:{code}",
        lambda: analyze_stock(code),
        ttl=60,
    )
    return jsonify(result)


@bp.route("/stock/<code>/radar/summary")
def api_stock_radar_summary(code: str):
    """个股雷达精简摘要 — 用于列表 hover / 卡片展示

    返回: 五维评分 + 综合结论 + 核心风险 (不含完整分析细节)
    """
    full = cache_or_fetch(
        f"stock_radar:{code}",
        lambda: analyze_stock(code),
        ttl=60,
    )
    return jsonify({
        "code": full.get("code"),
        "name": full.get("name"),
        "price": full.get("price"),
        "radar": full.get("radar", {}),
        "conclusion": full.get("conclusion", {}),
        "risk": full.get("risk", []),
    })
