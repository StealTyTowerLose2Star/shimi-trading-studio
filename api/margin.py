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
    """获取个股融资融券数据

    Query params:
        code (str, required): 股票代码，如 000001 或 000001.SZ
        top (int, optional): 忽略 (已废弃)

    Returns:
        JSON: 该股票融资融券详情
    """
    code = request.args.get("code", "").strip()

    if not code:
        return jsonify({
            "data": [],
            "hint": "使用 ?code=000001 查询个股融资融券",
        })

    # 支持纯数字或带后缀
    query_code = code.upper()
    if not query_code.endswith(".SZ") and not query_code.endswith(".SH"):
        # 自动补全后缀
        if query_code.startswith("6") or query_code.startswith("9"):
            query_code += ".SH"
        elif query_code.startswith("0") or query_code.startswith("3"):
            query_code += ".SZ"
        else:
            query_code += ".SH"

    data = get_margin_detail(ts_code=query_code)

    if isinstance(data, dict) and "error" in data:
        return jsonify({"error": data["error"], "data": []}), 404

    return jsonify({
        "code": query_code,
        "data": [data] if isinstance(data, dict) else data,
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

    # 逐只查询持仓的融资融券
    matched = []
    for code in holdings:
        # 标准化代码
        c = code.replace(".SZ", "").replace(".SH", "").upper()
        if c.startswith("6") or c.startswith("9"):
            query = c + ".SH"
        elif c.startswith("0") or c.startswith("3"):
            query = c + ".SZ"
        else:
            query = c + ".SH"

        data = get_margin_detail(ts_code=query)
        if isinstance(data, dict) and "error" not in data:
            data["code"] = code
            matched.append(data)

    return jsonify({
        "total": len(matched),
        "data": matched,
    })
