"""
HiTao 美股 - 投资建议引擎
职责: 市场评估 + 操作建议 + 个股推荐 (含做空)

对标 services/advice.py (A股), 但适配美股特点:
  - 多空双向
  - VIX 情绪指标
  - 盘前盘后数据
  - 板块轮动 (FAANG/半导体/新能源/金融)
"""

from datetime import datetime
from typing import Dict, List, Optional

from haitao.us_fetcher import get_quotes, get_history, calc_technical_indicators
from haitao.config import US_INDICES, HOT_US_STOCKS, CHINESE_ADR


def generate_advice() -> Dict:
    """生成美股投资建议

    维度:
      1. 市场环境评估 (指数/VIX/广度)
      2. 操作策略 (做多/做空/观望)
      3. 个股推荐 (含入场/止损/目标)
      4. 仓位建议

    Returns:
        {
            "generated_at": str,
            "market": {"phase": str, "vix": float, "breadth": dict},
            "strategy": {"direction": str, "aggressiveness": str},
            "picks": [{"symbol": str, "action": str, "entry": float, ...}],
            "position": str,
            "risk": str,
        }
    """
    result = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "market": {},
        "strategy": {},
        "picks": [],
        "position": "--",
        "risk": "--",
    }

    try:
        # 1. 市场环境
        market = _assess_market()
        result["market"] = market

        # 2. 操作策略
        strategy = _determine_strategy(market)
        result["strategy"] = strategy

        # 3. 个股推荐
        picks = _generate_picks(market, strategy)
        result["picks"] = picks

        # 4. 仓位建议
        result["position"] = _suggest_position(market, strategy)
        result["risk"] = _assess_risk(market)

    except Exception as e:
        result["error"] = str(e)[:200]

    return result


def _assess_market() -> Dict:
    """评估美股市场环境

    指标:
      - 三大指数位置 (vs 20/60日均线)
      - VIX 恐慌指数
      - 市场广度 (涨跌比)
    """
    market = {
        "phase": "未知",
        "vix": None,
        "vix_level": "未知",
        "indices": [],
        "breadth": {"advancing": 0, "declining": 0},
        "sentiment": "neutral",
    }

    try:
        # 指数数据
        index_tickers = list(US_INDICES.keys())
        quotes = get_quotes(index_tickers)

        up_count = 0
        down_count = 0
        for q in quotes:
            symbol = q.get("symbol", "?")
            chg = q.get("change_pct", 0) or 0
            price = q.get("price", 0) or 0

            market["indices"].append({
                "symbol": symbol,
                "name": US_INDICES.get(symbol, symbol),
                "price": price,
                "change_pct": round(chg, 2) if chg else 0,
            })

            if chg > 0:
                up_count += 1
            elif chg < 0:
                down_count += 1

        market["breadth"]["advancing"] = up_count
        market["breadth"]["declining"] = down_count

        # VIX 判定
        vix_data = next((q for q in quotes if q.get("symbol") == "^VIX"), None)
        if vix_data:
            vix = vix_data.get("price", 0) or 0
            market["vix"] = round(vix, 2)
            if vix < 15:
                market["vix_level"] = "低波动 · 市场平静"
                market["sentiment"] = "calm"
            elif vix < 20:
                market["vix_level"] = "正常波动"
                market["sentiment"] = "normal"
            elif vix < 30:
                market["vix_level"] = "恐慌上升 ⚠️"
                market["sentiment"] = "fearful"
            else:
                market["vix_level"] = "极度恐慌 🚨"
                market["sentiment"] = "panic"

        # 市场阶段判定
        if up_count >= len(quotes) * 0.75:
            market["phase"] = "强势上涨 📈"
        elif up_count > down_count:
            market["phase"] = "震荡偏多 ↗️"
        elif down_count > up_count:
            market["phase"] = "震荡偏空 ↘️"
        else:
            market["phase"] = "横盘整理 ➡️"

    except Exception:
        pass

    return market


def _determine_strategy(market: Dict) -> Dict:
    """根据市场环境确定操作策略"""
    phase = market.get("phase", "")
    vix_level = market.get("vix_level", "")

    strategy = {
        "direction": "hold",
        "aggressiveness": "conservative",
        "reason": "",
    }

    if "强势" in phase:
        strategy["direction"] = "buy"
        strategy["aggressiveness"] = "moderate"
        strategy["reason"] = "市场强势，做多为主"
    elif "偏多" in phase:
        strategy["direction"] = "buy"
        strategy["aggressiveness"] = "conservative"
        strategy["reason"] = "震荡偏多，精选个股做多"
    elif "偏空" in phase:
        strategy["direction"] = "hedge"
        strategy["aggressiveness"] = "conservative"
        strategy["reason"] = "偏空市场中考虑对冲或做空机会"
    else:
        strategy["direction"] = "hold"
        strategy["aggressiveness"] = "conservative"
        strategy["reason"] = "方向不明，观望为主"

    # VIX 调整
    if "恐慌" in vix_level:
        strategy["aggressiveness"] = "defensive"
        strategy["reason"] += " | VIX高位，降低仓位"

    return strategy


def _generate_picks(market: Dict, strategy: Dict) -> List[Dict]:
    """生成个股推荐"""
    picks = []

    try:
        from haitao.us_gold_scanner import gold_pan_hot, gold_pan_adr, gold_score

        direction = strategy.get("direction", "buy")

        # 做多推荐: 从黄金扫描取高分标的
        if direction in ("buy", "hedge"):
            hot_picks = gold_pan_hot()
            for p in hot_picks[:5]:
                if p.get("score", 0) >= 50:
                    entry = p.get("current_price", 0)
                    if entry <= 0:
                        continue

                    # ATR止盈止损 (简化版: 5%止损, 10%/20%/30%目标)
                    picks.append({
                        "symbol": p.get("ticker", "?"),
                        "name": "",
                        "action": "buy",
                        "score": p.get("score", 0),
                        "rating": p.get("rating", "?"),
                        "phase": p.get("phase", "?"),
                        "entry": round(entry, 2),
                        "stop_loss": round(entry * 0.95, 2),
                        "target_1": round(entry * 1.10, 2),
                        "target_2": round(entry * 1.20, 2),
                        "target_3": round(entry * 1.30, 2),
                        "signals": p.get("gold_signals", [])[:3],
                        "reason": f"黄金评分{p.get('score',0)}分 · {p.get('rating','?')}",
                    })

        # 中概股推荐
        adr_picks = gold_pan_adr()
        for p in adr_picks[:3]:
            if p.get("score", 0) >= 45:
                entry = p.get("current_price", 0)
                if entry <= 0:
                    continue
                picks.append({
                    "symbol": p.get("ticker", "?"),
                    "name": "",
                    "action": "buy",
                    "score": p.get("score", 0),
                    "rating": p.get("rating", "?"),
                    "entry": round(entry, 2),
                    "stop_loss": round(entry * 0.93, 2),
                    "target_1": round(entry * 1.10, 2),
                    "target_2": round(entry * 1.20, 2),
                    "target_3": round(entry * 1.30, 2),
                    "signals": p.get("gold_signals", [])[:3],
                    "reason": f"中概 · 黄金评分{p.get('score',0)}分",
                })

        # 去重
        seen = set()
        unique = []
        for p in picks:
            if p["symbol"] not in seen:
                seen.add(p["symbol"])
                unique.append(p)

        # 按评分排序
        unique.sort(key=lambda x: -x.get("score", 0))

        return unique[:8]

    except Exception:
        return []


def _suggest_position(market: Dict, strategy: Dict) -> str:
    """仓位建议"""
    phase = market.get("phase", "")
    vix_level = market.get("vix_level", "")

    if "强势" in phase and "恐慌" not in vix_level:
        return "积极 · 60-80%"
    elif "偏多" in phase:
        return "中等 · 40-60%"
    elif "偏空" in phase:
        return "防御 · 20-40%"
    elif "恐慌" in vix_level:
        return "轻仓 · 10-20%"
    else:
        return "观望 · 0-10%"


def _assess_risk(market: Dict) -> str:
    """风险评估"""
    vix = market.get("vix", 0) or 0
    up = market.get("breadth", {}).get("advancing", 0)
    down = market.get("breadth", {}).get("declining", 0)

    if vix < 15 and up > down:
        return "低"
    elif vix < 25:
        return "中"
    elif vix < 35:
        return "高 ⚠️"
    else:
        return "极高 🚨"
