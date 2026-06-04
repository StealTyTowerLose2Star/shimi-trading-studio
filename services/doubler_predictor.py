"""
拾米交易工作室 - 翻倍预测引擎
公式: 翻倍潜力 = 催化剂爆发力 × 剩余上涨空间 × 小盘弹性

与 recommend_current_month 的区别:
  recommend 奖励「已经涨起来的」(动量 + 量比 + 换手)
  predict   奖励「将要涨的」(催化剂 × 空间 × 弹性)
"""
import sys, os
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _get_month_start_prices(pro):
    """批量获取本月首日全市场收盘价 → {ts_code: close}"""
    now = datetime.now()
    # 找到本月第一个交易日
    cal = pro.trade_cal(start_date=now.strftime("%Y%m") + "01",
                        end_date=now.strftime("%Y%m%d"), is_open="1")
    if cal is None or len(cal) == 0:
        return {}
    cal = cal.sort_values("cal_date")
    first_day = cal.iloc[0]["cal_date"]
    
    # 一次性获取全市场收盘价
    df = pro.daily(trade_date=first_day, fields="ts_code,close")
    if df is None or len(df) == 0:
        return {}
    return {r["ts_code"]: float(r["close"]) for _, r in df.iterrows()}


def predict_monthly_doublers():
    """
    翻倍预测: 从催化剂池中找出"还有翻倍空间"的个股

    公式: potential = d7 × room × size_boost
      d7: 催化剂强度 (4-20)
      room: 剩余空间 (本月已涨越少, room越大)
      size_boost: 小盘加成 (<10亿=1.5x, <20亿=1.3x, ...)
    """
    from data.fetcher import get_daily, get_stock_basic, get_latest_date
    from services.doubler_scanner import _get_pro

    pro = _get_pro()
    today = get_latest_date()
    daily = get_daily()

    if daily is None or isinstance(daily, (dict, str)) or len(daily) == 0:
        return {"error": "daily data unavailable", "trade_date": today}

    basic = get_stock_basic()
    info_map = basic if isinstance(basic, dict) else {}

    # 量能(仅用市值)
    basic_df = None
    try:
        basic_df = pro.daily_basic(trade_date=today,
                                   fields="ts_code,total_mv,circ_mv,turnover_rate")
    except:
        pass
    basic_map = {}
    if basic_df is not None and len(basic_df) > 0:
        for _, r in basic_df.iterrows():
            basic_map[r["ts_code"]] = r

    # 催化剂
    catalyst_scores = {}
    try:
        from services.catalyst_engine import scan_all_catalysts
        ym = today[:6]
        cat_result = scan_all_catalysts(ym, trade_date=f"{today[:4]}-{today[4:6]}-{today[6:]}")
        catalyst_scores = cat_result.get("stock_scores", {})
    except:
        pass

    # 预加载本月首日全市场价 (1次API调用替代N次)
    month_start_prices = _get_month_start_prices(pro)

    candidates = []
    for _, row in daily.iterrows():
        code = row["ts_code"]
        close = float(row["close"])
        pct_chg = float(row["pct_chg"])

        if "ST" in code or close <= 0:
            continue

        info = info_map.get(code, {})
        short = str(code).replace(".SZ","").replace(".SH","").replace(".BJ","")
        b_row = basic_map.get(code, None)

        circ_mv = float(b_row["circ_mv"]) / 1e4 if b_row is not None and b_row["circ_mv"] is not None else 0
        circ_mv = 0 if (circ_mv != circ_mv) else circ_mv

        cat = catalyst_scores.get(short, {})
        d7 = cat.get("d7", 0)
        if d7 < 8:
            continue

        # 本月至今涨幅 (用批量获取的月初价)
        month_start_price = month_start_prices.get(code)
        mtm_gain = 0
        if month_start_price and month_start_price > 0:
            mtm_gain = (close / month_start_price - 1) * 100
        room = max(100 - mtm_gain, 10) / 100

        # 小盘弹性
        if circ_mv < 10:
            size_boost = 1.5
        elif circ_mv < 20:
            size_boost = 1.3
        elif circ_mv < 50:
            size_boost = 1.1
        elif circ_mv < 100:
            size_boost = 1.0
        else:
            size_boost = 0.7

        potential = d7 * room * size_boost

        # 动量微奖励: 在趋势中的股票有惯性 (<30%涨幅时给予≤15%加成)
        if 5 <= mtm_gain <= 30:
            potential *= 1 + min(mtm_gain / 200, 0.15)
        # 涨幅过大时不给额外惩罚, room因子已自动降低潜力
        # 例: mtm_gain=44% → room=0.56 → 自然降低, 但不排除

        candidates.append({
            "code": short, "ts_code": code,
            "name": info.get("name", "?"),
            "industry": info.get("industry", "未知"),
            "close": round(close, 2),
            "pct_chg": round(pct_chg, 2),
            "circ_mv_yi": round(circ_mv, 2),
            "potential": round(potential, 1),
            "catalyst_d7": d7,
            "catalyst_type": cat.get("top_event_type", ""),
            "mtm_gain": round(mtm_gain, 1),
            "room_pct": round(max(100 - mtm_gain, 10), 0),
            "size_boost": size_boost,
        })

    candidates.sort(key=lambda x: -x["potential"])
    clean = [c for c in candidates[:30] if "ST" not in c["name"]]

    return {
        "trade_date": today,
        "top30": candidates[:30],
        "elite_picks": clean[:12],
        "model": "doubling_potential (d7 × room × size)",
        "scan_time": datetime.now().isoformat(),
    }
