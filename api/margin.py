"""
拾米交易工作室 - 融资融券 API 蓝图
"""

from flask import Blueprint, jsonify, request
from datetime import date, timedelta

from data.fetcher import get_margin_detail

bp = Blueprint("margin", __name__, url_prefix="/api")


def _get_user_holdings():
    """获取当前用户持仓股票列表（模拟）

    从 db.py 中读取持仓，若失败返回空列表
    """
    try:
        from db import get_portfolio
        portfolio = get_portfolio()
        if isinstance(portfolio, list):
            # 持仓可能以 {ts_code: ..., ...} 或 {code: ...} 格式返回
            codes = []
            for item in portfolio:
                code = item.get("ts_code") or item.get("code") or ""
                if code:
                    codes.append(code)
            return codes
    except Exception:
        pass
    return []


@bp.route("/margin", methods=["GET"])
def margin_ranking():
    """获取融资融券排行

    Query params:
        code (str, optional): 查询单只股票，如 000001
        top (int, optional): 返回前 N 条，默认 20

    Returns:
        JSON: 融资融券排行列表
    """
    code = request.args.get("code", "").strip()
    top = request.args.get("top", 20, type=int)

    data = get_margin_detail()

    if isinstance(data, dict) and "error" in data:
        return jsonify({"error": data["error"]}), 500

    if not isinstance(data, list):
        return jsonify({"error": "数据格式异常"}), 500

    if code:
        # 查询单只股票 — 支持纯数字或带后缀
        query = code.upper()
        filtered = [item for item in data if query in item.get("ts_code", "")]
        if not filtered:
            return jsonify({"error": f"未找到股票 {code} 的融资融券数据"}), 404
        return jsonify({"code": query, "data": filtered[0]})

    # 返回排行
    result = data[:top]
    return jsonify({
        "total": len(data),
        "top": top,
        "data": result,
    })


@bp.route("/portfolio/margin", methods=["GET"])
def portfolio_margin():
    """获取持仓股票的融资融券数据

    需要登录态（读取用户持仓），返回持仓中每只股票的融资融券信息

    Returns:
        JSON: 持仓股票融资融券详情
    """
    holdings = _get_user_holdings()
    if not holdings:
        return jsonify({"error": "未获取到持仓数据或持仓为空"}), 404

    data = get_margin_detail()
    if isinstance(data, dict) and "error" in data:
        return jsonify({"error": data["error"]}), 500

    # 匹配持仓
    holdings_set = set()
    for code in holdings:
        # 标准化：去掉 .SZ/.SH 后缀
        c = code.replace(".SZ", "").replace(".SH", "").upper()
        holdings_set.add(c)

    matched = []
    for item in data:
        ts_code = item.get("ts_code", "")
        c = ts_code.replace(".SZ", "").replace(".SH", "").upper()
        if c in holdings_set:
            matched.append(item)
            holdings_set.discard(c)  # 避免重复

    return jsonify({
        "total": len(matched),
        "data": matched,
    })
