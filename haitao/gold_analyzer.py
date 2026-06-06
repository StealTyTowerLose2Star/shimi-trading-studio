"""海淘掘金 — 黄金股深度分析引擎

对筛选出的金矿/银矿股票生成:
1. 评分细分（为什么高分的解释）
2. 投资建议（入场/止盈/止损价格）
3. 仓位建议（基于 10,000 HKD 本金）
"""
import os, json, time, logging
from datetime import datetime
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

FINNHUB_KEY = os.environ.get("FINNHUB_KEY", "")
FH_BASE = "https://finnhub.io/api/v1"

# 资金配置常数
CAPITAL_HKD = 10000.0          # 本金 10,000 HKD
HKD_TO_USD = 7.83              # 汇率
CAPITAL_USD = CAPITAL_HKD / HKD_TO_USD  # ≈ $1,277
RISK_PER_TRADE_PCT = 0.02      # 每笔风险 2%
MAX_POSITIONS = 5              # 最多同时 5 个持仓
RISK_REWARD_RATIO = 2.0        # 止盈风险比


def analyze_gold_pick(symbol: str) -> dict:
    """对单只黄金股进行深度分析"""
    # 获取行情数据
    quote = _get_quote(symbol)
    if not quote:
        return {"symbol": symbol, "error": "quote_failed"}

    price = quote.get("price", 0)
    if price <= 0:
        return {"symbol": symbol, "error": "no_price"}

    # 计算评分细分
    score_detail = _calculate_score_detail(quote)

    # 计算止盈止损
    risk_mgmt = _calculate_risk_management(quote)

    # 计算仓位
    position = _calculate_position(price, risk_mgmt["stop_loss"])

    return {
        "symbol": symbol,
        "price": price,
        "change_pct": quote.get("change_pct", 0),
        "volume": quote.get("volume", 0),

        # 评分细分
        "score": score_detail["total"],
        "score_breakdown": score_detail["breakdown"],
        "signals": score_detail["signals"],
        "grade": _grade(score_detail["total"]),

        # 投资建议
        "entry": {
            "price": round(price, 2),
            "type": "市价" if abs(quote.get("change_pct", 0) or 0) < 3 else "限价",
            "suggestion": f"建议现价 \${price:.2f} 入场" if abs(quote.get("change_pct", 0) or 0) < 3 else f"等回调至 \${risk_mgmt['entry_zone']} 区间入场",
        },
        "stop_loss": {
            "price": risk_mgmt["stop_loss"],
            "pct": risk_mgmt["stop_loss_pct"],
            "reason": f"2倍日内波动保护 (昨日波幅 {risk_mgmt['day_range_pct']}%)",
        },
        "take_profit": {
            "tp1": risk_mgmt["tp1"],
            "tp1_pct": risk_mgmt["tp1_pct"],
            "tp2": risk_mgmt["tp2"],
            "tp2_pct": risk_mgmt["tp2_pct"],
            "strategy": f"分批止盈: TP1({risk_mgmt['tp1_pct']}%+)减半, TP2({risk_mgmt['tp2_pct']}%+)清仓",
        },

        # 仓位建议
        "position": {
            "capital_hkd": CAPITAL_HKD,
            "capital_usd": round(CAPITAL_USD, 0),
            "risk_per_trade_pct": RISK_PER_TRADE_PCT * 100,
            "risk_per_trade_usd": round(position["risk_usd"], 2),
            "shares": position["shares"],
            "position_value_usd": round(position["value_usd"], 2),
            "position_value_hkd": round(position["value_hkd"], 2),
            "pct_of_capital": round(position["pct_of_capital"] * 100, 1),
            "suggestion": position["suggestion"],
        },
        "analyzed_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


def _get_quote(symbol: str) -> dict:
    """获取行情"""
    url = f"{FH_BASE}/quote?symbol={symbol}&token={FINNHUB_KEY}"
    try:
        r = requests.get(url, timeout=10)
        q = r.json()
        if isinstance(q, dict) and q.get("c"):
            return {
                "price": float(q["c"]),
                "change_pct": q.get("dp") or 0,
                "volume": q.get("v") or 0,
                "high": float(q.get("h", q.get("c", 0))),
                "low": float(q.get("l", q.get("c", 0))),
                "open": float(q.get("o", q.get("c", 0))),
                "prev_close": float(q.get("pc", q.get("c", 0))),
            }
    except:
        pass
    return {}


def _calculate_score_detail(quote: dict) -> dict:
    """计算评分细分"""
    price = quote["price"]
    chg = quote.get("change_pct") or 0
    vol = quote.get("volume") or 0
    hi = quote.get("high", price)
    lo = quote.get("low", price)
    prev = quote.get("prev_close", price)
    dr = (hi - lo) / price * 100 if price > 0 else 0

    breakdown = {}
    signals = []

    # 涨跌幅 (25分)
    if 1 <= chg <= 5:
        breakdown["涨跌幅"] = {"score": 25, "detail": f"{chg:+.1f}% 温和上涨", "max": 25}
        signals.append("温和上涨")
    elif 5 < chg <= 15:
        breakdown["涨跌幅"] = {"score": 20, "detail": f"{chg:+.1f}% 强势启动", "max": 25}
        signals.append("强势启动")
    elif chg > 15:
        breakdown["涨跌幅"] = {"score": 10, "detail": f"{chg:+.1f}% 暴涨(注意追高风险)", "max": 25}
        signals.append("暴涨(谨慎)")
    elif 0 <= chg < 1:
        breakdown["涨跌幅"] = {"score": 15, "detail": f"{chg:+.1f}% 平稳", "max": 25}
        signals.append("平稳运行")
    elif -3 <= chg < 0:
        breakdown["涨跌幅"] = {"score": 12, "detail": f"{chg:.1f}% 微调(关注反弹)", "max": 25}
        signals.append("微调中")
    elif -10 <= chg < -3:
        breakdown["涨跌幅"] = {"score": 8, "detail": f"{chg:.1f}% 回调(关注反弹)", "max": 25}
        signals.append("回调中")
    else:
        breakdown["涨跌幅"] = {"score": 3, "detail": f"{chg:.1f}% 深度下跌", "max": 25}
        signals.append("深度下跌")

    # 价格区间 (25分)
    if 5 <= price <= 20:
        breakdown["价格区间"] = {"score": 25, "detail": f"\${price:.2f} 低价小盘，上涨空间大", "max": 25}
        signals.append("低价小盘")
    elif 20 < price <= 50:
        breakdown["价格区间"] = {"score": 20, "detail": f"\${price:.2f} 小盘成长区间", "max": 25}
        signals.append("小盘成长")
    elif 50 < price <= 150:
        breakdown["价格区间"] = {"score": 15, "detail": f"\${price:.2f} 中盘稳健", "max": 25}
        signals.append("中盘稳健")
    elif 150 < price <= 300:
        breakdown["价格区间"] = {"score": 8, "detail": f"\${price:.2f} 大盘蓝筹", "max": 25}
    else:
        breakdown["价格区间"] = {"score": 3, "detail": f"\${price:.2f} 价格过高/过低", "max": 25}

    # 流动性 (15分)
    if vol > 5000000:
        breakdown["流动性"] = {"score": 15, "detail": f"成交量 {vol/1e6:.1f}M，超高流动性", "max": 15}
    elif vol > 1000000:
        breakdown["流动性"] = {"score": 12, "detail": f"成交量 {vol/1e6:.1f}M，高流动性", "max": 15}
    elif vol > 300000:
        breakdown["流动性"] = {"score": 8, "detail": f"成交量 {vol/1e3:.0f}K，适中", "max": 15}
    else:
        breakdown["流动性"] = {"score": 5, "detail": "流动性偏低", "max": 15}

    # 日内波动 (10分)
    if 0 < dr <= 3:
        breakdown["波动率"] = {"score": 10, "detail": f"波幅 {dr:.1f}%，窄幅蓄力有利爆发", "max": 10}
        signals.append("窄幅蓄力")
    elif 3 < dr <= 6:
        breakdown["波动率"] = {"score": 5, "detail": f"波幅 {dr:.1f}%，正常波动", "max": 10}
    elif dr > 10:
        breakdown["波动率"] = {"score": -3, "detail": f"波幅 {dr:.1f}%，剧烈波动风险高", "max": 10}
    else:
        breakdown["波动率"] = {"score": 3, "detail": "波动极低", "max": 10}

    # 趋势 (5分)
    if price > prev:
        breakdown["趋势"] = {"score": 5, "detail": "价格高于前收盘，延续上行", "max": 5}
    else:
        breakdown["趋势"] = {"score": 0, "detail": "低于前收盘", "max": 5}

    total = sum(v["score"] for v in breakdown.values())
    return {"total": total, "breakdown": breakdown, "signals": signals}


def _calculate_risk_management(quote: dict) -> dict:
    """计算止盈止损价位"""
    price = quote["price"]
    hi = quote.get("high", price)
    lo = quote.get("low", price)
    prev = quote.get("prev_close", price)

    # 使用日内波动作为风险度量
    daily_range = max(hi - lo, price * 0.02)  # 至少 2%
    dr = (hi - lo) / price * 100 if price > 0 else 2.0

    # 止损设 2 倍日内波幅
    stop_loss = round(price - daily_range * 2, 2)
    sl_pct = round((stop_loss / price - 1) * 100, 1)

    # 止盈: 风险比 2:1 和 4:1
    tp1 = round(price + daily_range * 2 * RISK_REWARD_RATIO, 2)
    tp2 = round(price + daily_range * 4 * RISK_REWARD_RATIO, 2)
    tp1_pct = round((tp1 / price - 1) * 100, 1)
    tp2_pct = round((tp2 / price - 1) * 100, 1)

    return {
        "day_range_pct": round(dr, 1),
        "stop_loss": stop_loss,
        "stop_loss_pct": sl_pct,
        "tp1": tp1,
        "tp1_pct": tp1_pct,
        "tp2": tp2,
        "tp2_pct": tp2_pct,
        "entry_zone": round(price * 0.98, 2),
    }


def _calculate_position(price: float, stop_loss: float) -> dict:
    """计算仓位大小"""
    risk_per_trade = CAPITAL_USD * RISK_PER_TRADE_PCT

    # 每股价差风险
    risk_per_share = abs(price - stop_loss)
    if risk_per_share < 0.01:
        risk_per_share = price * 0.02

    # 最大买入股数 (基于风险限额)
    max_shares = int(risk_per_trade / risk_per_share)
    if max_shares < 1:
        max_shares = 1

    value_usd = max_shares * price
    pct = value_usd / CAPITAL_USD

    # 单只个股仓位上限 = 100% / MAX_POSITIONS (多只平均分配)
    max_pct = 1.0 / MAX_POSITIONS
    if pct > max_pct:
        max_shares = int(CAPITAL_USD * max_pct / price)
        value_usd = max_shares * price
        pct = value_usd / CAPITAL_USD

    value_hkd = value_usd * HKD_TO_USD

    if max_shares >= 100:
        suggestion = f"买 {max_shares} 股(HK${value_hkd:.0f}, {pct*100:.0f}%仓位)，等回调加仓"
    elif max_shares >= 10:
        suggestion = f"买入 {max_shares} 股(HK${value_hkd:.0f})，占本金 {pct*100:.0f}%"
    else:
        suggestion = f"小额买入 {max_shares} 股(HK${value_hkd:.0f})"

    return {
        "risk_usd": round(risk_per_trade, 2),
        "risk_hkd": round(risk_per_trade * HKD_TO_USD, 0),
        "shares": max_shares,
        "value_usd": round(value_usd, 2),
        "value_hkd": round(value_hkd, 0),
        "pct_of_capital": round(pct, 3),
        "suggestion": suggestion,
    }


def _grade(score: int) -> str:
    if score >= 70: return "🥇金矿"
    elif score >= 50: return "🥈银矿"
    elif score >= 30: return "🥉铜矿"
    return "🪨石矿"


def analyze_top_picks(top_n: int = 10) -> List[dict]:
    """分析 Top N 黄金股"""
    # 从缓存加载金矿列表
    cache_path = "/root/shi-mi-dashboard/haitao/cache/gold_picks.json"
    if not os.path.exists(cache_path):
        return []

    with open(cache_path) as f:
        data = json.load(f)

    golds = data.get("gold_picks", [])[:top_n]
    results = []
    for g in golds:
        sym = g["symbol"]
        analysis = analyze_gold_pick(sym)
        if "error" not in analysis:
            # 合并原始分数
            analysis["name"] = g.get("name", sym)
            analysis["exchange"] = g.get("exchange", "")
            results.append(analysis)
        time.sleep(0.3)

    return results


def generate_report() -> dict:
    """生成完整投资报告（基于已缓存的扫描数据，不额外消耗API）"""
    cache_path = "/root/shi-mi-dashboard/haitao/cache/gold_picks.json"
    if not os.path.exists(cache_path):
        return {"error": "No scan cache yet", "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M")}
    
    with open(cache_path) as f:
        data = json.load(f)
    
    golds = data.get("gold_picks", [])
    silvers = data.get("silver_picks", [])
    all_picks = golds + silvers[:5]  # 前5银矿
    
    valid = []
    for g in all_picks[:8]:
        price = g.get("price", 0)
        if price <= 0: continue
        
        # 模拟一个 quote dict 供分析函数使用
        quote = {
            "price": price,
            "change_pct": g.get("change_pct", 0),
            "volume": g.get("volume", 0),
            "high": price * (1 + abs(g.get("change_pct", 0))/200),
            "low": price * (1 - abs(g.get("change_pct", 0))/200),
            "prev_close": price / (1 + g.get("change_pct", 0)/100) if g.get("change_pct") else price,
        }
        
        score_detail = _calculate_score_detail(quote)
        risk_mgmt = _calculate_risk_management(quote)
        position = _calculate_position(price, risk_mgmt["stop_loss"])
        
        valid.append({
            "symbol": g["symbol"],
            "name": g.get("name", g["symbol"]),
            "price": price,
            "change_pct": g.get("change_pct", 0),
            "volume": g.get("volume", 0),
            "score": score_detail["total"],
            "grade": _grade(score_detail["total"]),
            "score_breakdown": score_detail["breakdown"],
            "signals": score_detail["signals"],
            "entry": {
                "price": round(price, 2),
                "type": "市价",
                "suggestion": f"建议现价 \${price:.2f} 入场" if abs(g.get("change_pct", 0) or 0) < 3 else f"等回调至 \${risk_mgmt['entry_zone']} 区间入场",
            },
            "stop_loss": {
                "price": risk_mgmt["stop_loss"],
                "pct": risk_mgmt["stop_loss_pct"],
                "reason": f"2倍日内波动保护",
            },
            "take_profit": {
                "tp1": risk_mgmt["tp1"],
                "tp1_pct": risk_mgmt["tp1_pct"],
                "tp2": risk_mgmt["tp2"],
                "tp2_pct": risk_mgmt["tp2_pct"],
                "strategy": f"分批止盈: TP1({risk_mgmt['tp1_pct']}%+)减半, TP2({risk_mgmt['tp2_pct']}%+)清仓",
            },
            "position": {
                "risk_per_trade_usd": round(position["risk_usd"], 2),
                "shares": position["shares"],
                "value_usd": round(position["value_usd"], 2),
                "value_hkd": round(position["value_hkd"], 0),
                "pct_of_capital": round(position["pct_of_capital"] * 100, 1),
                "position_value_hkd": round(position["value_hkd"], 0),
                "suggestion": position["suggestion"],
            },
            "analyzed_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        })
    
    total_cap = CAPITAL_USD
    final = [p for p in valid if p["position"]["pct_of_capital"] <= 30][:MAX_POSITIONS]
    allocated = sum(p["position"]["value_usd"] for p in final)
    
    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "capital": {
            "hkd": CAPITAL_HKD, "usd": round(CAPITAL_USD, 0),
            "risk_per_trade_pct": RISK_PER_TRADE_PCT * 100,
            "max_positions": MAX_POSITIONS,
        },
        "picks": final,
        "summary": {
            "total_picks": len(final),
            "allocated_usd": round(allocated, 2),
            "allocated_hkd": round(allocated * HKD_TO_USD, 0),
            "remaining_usd": round(total_cap - allocated, 2),
            "remaining_hkd": round((total_cap - allocated) * HKD_TO_USD, 0),
        },
    }
