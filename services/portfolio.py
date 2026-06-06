"""
拾米交易工作室 — 持仓分析服务模块
从 backend.py api_portfolio_advice 提取
"""
from db import get_trades
from realtime_scorer import get_kline, trend_detect, hybrid_score, dragon_leader_score
from position_manager import evaluate_position


def analyze_portfolio(user_id: int):
    """分析用户持仓，返回每条持仓的策略评分及建议"""
    trades = get_trades(user_id=user_id)
    open_trades = [t for t in trades if not t.get("exit_price")]

    if not open_trades:
        return {"positions": [], "summary": {"total": 0, "advice": "无持仓"}}

    positions = []
    total_invested = 0
    total_pnl = 0
    bullish_count = 0

    for t in open_trades:
        code = t["code"]
        entry = t["entry_price"]
        qty = t["qty"]
        invested = entry * qty
        total_invested += invested

        current_price = entry
        kline = None
        try:
            kline = get_kline(code, days=30)
            if kline is not None and len(kline) > 0:
                current_price = float(kline["close"].iloc[-1])
        except:
            pass

        pnl = (current_price - entry) * qty if t["direction"] == "buy" else (entry - current_price) * qty
        pnl_pct = (current_price - entry) / entry * 100 if t["direction"] == "buy" else (entry - current_price) / entry * 100
        total_pnl += pnl

        recent_trend = "平稳"
        recent_alert = ""
        if kline is not None and len(kline) >= 5:
            closes = kline["close"].tolist()[-5:]
            pcts = kline["pct_chg"].tolist()[-5:]
            latest_pct = float(pcts[-1]) if pcts[-1] is not None else 0
            if latest_pct < -5:
                recent_trend, recent_alert = "大跌⚠️", "日跌幅超5%"
            elif latest_pct < -3:
                recent_trend, recent_alert = "下跌📉", "日跌幅超3%"
            elif latest_pct < -1:
                recent_trend = "微跌"
            elif latest_pct > 5:
                recent_trend, recent_alert = "大涨📈", "强势上涨"
            down_days = sum(1 for p in pcts[-3:] if (p or 0) < -1)
            if down_days >= 2 and latest_pct < 0:
                recent_trend = "连续下跌⚠️"
                recent_alert = (recent_alert or "") + " 连续3日2次下跌"
            vols = kline["volume"].tolist()[-5:]
            if len(vols) >= 3 and latest_pct < -3:
                avg_vol = sum(vols[-4:-1]) / 3
                if vols[-1] > avg_vol * 2.5:
                    recent_alert += " 放量下跌⚠️"

        trend = None
        try: trend = trend_detect(code)
        except: pass
        hybrid = None
        try: hybrid = hybrid_score(code)
        except: pass
        dragon = None
        try: dragon = dragon_leader_score(code)
        except: pass

        tb = trend and trend["total_score"] >= 50
        hb = hybrid and hybrid["score"] >= 45
        db = dragon and dragon["leader_score"] >= 40
        sc = sum([tb, hb, db])
        safe = pnl_pct > 0
        trend_stage = trend["stage"] if trend else "未知"

        if trend_stage == "鱼尾期":
            advice = "减仓 ⚠️"; action = "减仓至30%以下，设好止损"
            parts = ["鱼尾期，风险收益比差"]
            if "大跌" in recent_trend: parts.append(recent_alert)
            if not safe: parts.append(f"浮亏{round(abs(pnl_pct),1)}%")
            reason = " | ".join(parts)
        elif trend_stage == "鱼身末期":
            if recent_trend in ("大跌⚠️", "连续下跌⚠️"):
                advice = "减仓 ⚠️"; action = "减仓至50%，锁定利润"
                reason = f"鱼身末期+{recent_alert}"
            elif safe and sc >= 2:
                advice = "谨慎持有 ⏳"; action = "持有观望，止损上移至成本"
                reason = "鱼身末期，浮盈保护中"
            else:
                advice = "观察 ⏳"; action = "暂不加仓，观察2-3天"
                reason = "鱼身末期，建议轻仓"
        elif recent_trend in ("大跌⚠️", "连续下跌⚠️"):
            advice = "减仓 ⚠️"; action = "减仓至30%，控制风险"
            parts = [recent_alert]
            if not safe: parts.append(f"浮亏{round(abs(pnl_pct),1)}%")
            if trend_stage == "鱼头期": parts.append("鱼头期波动大")
            reason = " | ".join(parts)
        elif not safe and sc <= 1:
            advice = "减仓 ⚠️"; action = "减仓至50%以下"
            reason = f"策略偏弱+浮亏{round(abs(pnl_pct),1)}%"
        elif sc >= 2 and safe:
            advice = "持有 ✅"; action = "继续持有，止损上移保护利润"
            parts = [f"趋势{trend['total_score']}分 {trend_stage}"]
            if hb and hybrid: parts.append(f"混合{hybrid['score']}分 {hybrid['grade']}")
            parts.append(f"浮盈+{round(pnl_pct,1)}%")
            reason = " ".join(parts)
            bullish_count += 1
        else:
            advice = "观望 ⏳"; action = "等待明确信号再操作"
            reason = "信号偏弱"

        sl_label = "--"
        distance_to_sl = "--"
        scenario = ""
        try:
            ev = evaluate_position(code, entry, t["direction"])
            if ev and isinstance(ev, dict):
                sl_label = ev.get("stop_loss_label", "--")
                distance = ev.get("distance_to_sl_pct")
                if distance is not None:
                    distance_to_sl = f"{distance}%"
                    if distance < 3: scenario = "⚠️ 距止损很近，密切关注"
                    elif distance < 8: scenario = "正常持仓区间"
                    else: scenario = "安全距离充足"
                trail = ev.get("trailing_level", 0)
                if trail >= 1:
                    scenario += "·浮动止盈已激活 ✅" if trail == 1 else "·已锁定利润 ✅"
        except:
            pass

        positions.append({
            "code": code, "name": t.get("name", ""),
            "entry_price": round(entry, 2), "current_price": round(current_price, 2),
            "qty": qty, "invested": round(invested, 2),
            "pnl": round(pnl, 2), "pnl_pct": round(pnl_pct, 1),
            "stop_loss": sl_label, "distance_to_sl": distance_to_sl,
            "advice": advice, "action": action, "reason": reason, "scenario": scenario,
            "trend_score": trend["total_score"] if trend else None,
            "trend_stage": trend["stage"] if trend else None,
            "hybrid_score": hybrid["score"] if hybrid else None,
            "hybrid_grade": hybrid["grade"] if hybrid else None,
            "dragon_score": dragon["leader_score"] if dragon else None,
            "dragon_grade": dragon["grade"] if dragon else None,
            "margin": {},
        })

    risk = "低"
    if total_pnl < -total_invested * 0.05: risk = "高⚠️"
    elif total_pnl < 0: risk = "中"

    return {
        "positions": positions,
        "summary": {
            "total_positions": len(open_trades),
            "total_invested": round(total_invested, 2),
            "total_pnl": round(total_pnl, 2),
            "total_pnl_pct": round(total_pnl / total_invested * 100, 1) if total_invested > 0 else 0,
            "bullish_count": bullish_count,
            "risk": risk,
        }
    }
