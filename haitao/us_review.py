"""
HiTao 美股 - 复盘系统
职责: 美股交易每日/每周复盘

架构: haitao/us_review.py → haitao/us_fetcher.py → yfinance/Finnhub
"""
from datetime import datetime, timedelta
from typing import Dict, List
from collections import defaultdict

from haitao.us_fetcher import get_quotes, get_history, calc_technical_indicators
from haitao.config import US_INDICES, HOT_US_STOCKS


def run_daily_review() -> Dict:
    """每日美股复盘

    检查维度:
      1. 三大指数表现 (S&P 500 / Nasdaq / Dow)
      2. 热门股涨跌
      3. 黄金扫描结果变化
      4. 持仓评估

    Returns:
        {"success": bool, "date": str, "summary": str, "details": dict}
    """
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")

    result = {
        "success": True,
        "date": today_str,
        "type": "daily",
        "summary": "",
        "details": {},
    }

    try:
        # 1. 指数表现
        indices = _review_indices()
        result["details"]["indices"] = indices

        # 2. 热门股扫描
        hot_stocks = _review_hot_stocks()
        result["details"]["hot_stocks"] = hot_stocks

        # 3. 生成摘要
        parts = []
        if indices:
            sp500 = next((i for i in indices if "S&P" in i.get("name", "")), None)
            if sp500:
                chg = sp500.get("change_pct", 0)
                direction = "📈" if chg > 0 else "📉" if chg < 0 else "➡️"
                parts.append(f"{direction} S&P 500 {chg:+.2f}%")

        if hot_stocks:
            gainers = [s for s in hot_stocks if s.get("change_pct", 0) > 0]
            losers = [s for s in hot_stocks if s.get("change_pct", 0) < 0]
            parts.append(f"热门: {len(gainers)}涨/{len(losers)}跌")

        result["summary"] = " | ".join(parts) if parts else "数据不足"

    except Exception as e:
        result["success"] = False
        result["error"] = str(e)[:200]

    return result


def run_weekly_review() -> Dict:
    """每周美股复盘

    检查维度:
      1. 本周指数走势
      2. 本周黄金扫描 Top 变化
      3. 行业板块轮动
      4. 交易盈亏统计

    Returns:
        {"success": bool, "week": str, "summary": str, "details": dict}
    """
    now = datetime.now()
    week_start = (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d")
    week_end = now.strftime("%Y-%m-%d")

    result = {
        "success": True,
        "week": f"{week_start} ~ {week_end}",
        "type": "weekly",
        "summary": "",
        "details": {},
    }

    try:
        # 1. 周度指数
        indices = _review_indices_weekly()
        result["details"]["indices"] = indices

        # 2. 本周黄金扫描
        gold_picks = _review_gold_picks()
        result["details"]["gold_picks"] = gold_picks

        # 3. 盈亏统计
        try:
            from haitao.us_pnl import calculate_pnl
            pnl = calculate_pnl(period="week")
            result["details"]["pnl"] = pnl
        except Exception:
            result["details"]["pnl"] = {"error": "盈亏数据不可用"}

        # 4. 摘要
        parts = []
        if indices:
            weekly_changes = [i.get("week_change_pct", 0) for i in indices if i.get("week_change_pct") is not None]
            if weekly_changes:
                avg = sum(weekly_changes) / len(weekly_changes)
                direction = "📈" if avg > 0 else "📉" if avg < 0 else "➡️"
                parts.append(f"{direction} 周均 {avg:+.2f}%")

        if gold_picks:
            parts.append(f"金矿: {len(gold_picks)}只")

        pnl_data = result["details"].get("pnl", {})
        if pnl_data and "summary" in pnl_data:
            total_pnl = pnl_data["summary"].get("total_pnl", 0)
            if total_pnl != 0:
                parts.append(f"盈亏: ${total_pnl:+.0f}")

        result["summary"] = " | ".join(parts) if parts else "数据不足"

    except Exception as e:
        result["success"] = False
        result["error"] = str(e)[:200]

    return result


def _review_indices() -> List[Dict]:
    """获取指数当日表现"""
    try:
        tickers = list(US_INDICES.keys())
        quotes = get_quotes(tickers)
        results = []
        for q in quotes:
            results.append({
                "name": q.get("name", q.get("symbol", "?")),
                "symbol": q.get("symbol", "?"),
                "price": q.get("price"),
                "change_pct": q.get("change_pct"),
                "volume": q.get("volume"),
            })
        return results
    except Exception:
        return []


def _review_indices_weekly() -> List[Dict]:
    """获取指数周度表现"""
    try:
        tickers = list(US_INDICES.keys())
        results = []
        for ticker in tickers:
            hist = get_history(ticker, 7)
            if hist is not None and len(hist) >= 2:
                week_start_price = float(hist["Close"].iloc[0])
                week_end_price = float(hist["Close"].iloc[-1])
                week_change = (week_end_price / week_start_price - 1) * 100
                results.append({
                    "symbol": ticker,
                    "week_start": round(week_start_price, 2),
                    "week_end": round(week_end_price, 2),
                    "week_change_pct": round(week_change, 2),
                })
        return results
    except Exception:
        return []


def _review_hot_stocks() -> List[Dict]:
    """热门股当日扫描"""
    try:
        quotes = get_quotes(HOT_US_STOCKS[:15])
        results = []
        for q in quotes:
            results.append({
                "symbol": q.get("symbol", "?"),
                "name": q.get("name", ""),
                "price": q.get("price"),
                "change_pct": q.get("change_pct"),
            })
        results.sort(key=lambda x: abs(x.get("change_pct", 0)), reverse=True)
        return results[:10]
    except Exception:
        return []


def _review_gold_picks() -> List[Dict]:
    """本周黄金扫描结果"""
    try:
        from haitao.us_gold_scanner import gold_pan_hot
        picks = gold_pan_hot()
        return [
            {"symbol": p.get("ticker", "?"), "score": p.get("score", 0), "rating": p.get("rating", "?")}
            for p in picks[:10]
        ]
    except Exception:
        return []
