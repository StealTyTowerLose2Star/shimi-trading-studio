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


def _radar_unavailable(result):
    """K线/行情全部不可取得时雷达是空壳 (各分析器均为"数据不足",
    综合0分+回避结论会伪装成真实判断) — 识别之并显式失败 (real-only)"""
    trend = result.get("trend", {}) if isinstance(result, dict) else {}
    return (isinstance(result, dict)
            and result.get("price") is None
            and result.get("price_position", {}).get("zone") == "数据不足"
            and trend.get("daily", {}).get("direction") == "数据不足")


def _radar_or_502(code):
    result = cache_or_fetch(
        f"stock_radar:{code}",
        lambda: analyze_stock(code),
        ttl=60,
    )
    if _radar_unavailable(result) or (isinstance(result, dict) and result.get("error")):
        return None, (jsonify({"error": "雷达数据源不可取得(K线/行情失败), 不以空壳评分冒充",
                               "code": code, "data_status": "unavailable"}), 502)
    return result, None


@bp.route("/stock/<code>/radar")
def api_stock_radar(code: str):
    """个股全维度雷达分析

    返回五大分析器结果 + 雷达评分 + 风险提示 + 综合结论。
    见 services/stock_radar.py 各分析器文档获取字段说明。

    缓存: 60秒 (行情级实时性); 数据源全失败 → 502 显式失败
    """
    result, error = _radar_or_502(code)
    if error:
        return error
    return jsonify(result)


@bp.route("/stock/<code>/radar/summary")
def api_stock_radar_summary(code: str):
    """个股雷达精简摘要 — 用于列表 hover / 卡片展示

    返回: 五维评分 + 综合结论 + 核心风险 (不含完整分析细节)
    """
    full, error = _radar_or_502(code)
    if error:
        return error
    return jsonify({
        "code": full.get("code"),
        "name": full.get("name"),
        "price": full.get("price"),
        "radar": full.get("radar", {}),
        "conclusion": full.get("conclusion", {}),
        "risk": full.get("risk", []),
    })
