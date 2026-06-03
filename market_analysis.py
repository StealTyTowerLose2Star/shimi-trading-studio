"""市场分析层 — 市场状态、情绪、板块资金流"""
import pandas as pd
import time
from datetime import datetime, timedelta
from cache import cache_or_fetch
from data_fetcher import get_daily, get_stock_basic, get_latest_date, get_ts, get_daily_basic


# ═══════════════════════════════════════════
# 情绪分析
# ═══════════════════════════════════════════
def fetch_sentiment():
    """市场状态分析 — 4 维综合评分"""
    from datetime import datetime, timedelta
    try:
        pro = get_ts()
        today = get_latest_date()
        if isinstance(today, dict):
            today = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=150)).strftime("%Y%m%d")

        idx_codes = {"上证指数": "000001.SH", "深证成指": "399001.SZ", "创业板指": "399006.SZ"}
        idx_weights = {"上证指数": 0.40, "深证成指": 0.35, "创业板指": 0.25}
        index_data = {}
        for name, code in idx_codes.items():
            try:
                df = pro.index_daily(ts_code=code, start_date=start, end_date=today, fields="trade_date,close,vol,amount")
                if not df.empty:
                    df = df.sort_values("trade_date").reset_index(drop=True)
                    df["ma5"] = df["close"].rolling(5).mean()
                    df["ma10"] = df["close"].rolling(10).mean()
                    df["ma20"] = df["close"].rolling(20).mean()
                    df["ma60"] = df["close"].rolling(60).mean()
                    index_data[name] = df
            except:
                pass

        if not index_data:
            return {"error": "no index data"}

        def calc_index_score(df):
            if df is None or len(df) < 60:
                return {"trend": 0, "position": 0}
            l = df.iloc[-1]
            ma5, ma10, ma20, ma60 = l["ma5"], l["ma10"], l["ma20"], l["ma60"]
            close = l["close"]
            align = 0
            if ma5 > ma10: align += 12.5
            if ma10 > ma20: align += 12.5
            if ma20 > ma60: align += 12.5
            if ma5 > ma60: align += 12.5
            slope = (ma20 - df["ma20"].iloc[-5]) / df["ma20"].iloc[-20] * 100 if len(df) >= 25 else 0
            slope_score = min(25, max(0, slope * 10))
            above_ma20, above_ma60 = close > ma20, close > ma60
            if above_ma20 and above_ma60:
                dist = (close - ma20) / ma20 * 100
                pos_score = 15 if dist > 15 else 25
            elif above_ma20: pos_score = 15
            elif above_ma60: pos_score = 10
            else: pos_score = 5 if (ma60 - close) / ma60 * 100 < 5 else 0
            return {"trend": round(align + slope_score + pos_score, 1), "position": pos_score}

        trend_total = pos_total = 0
        for name, w in idx_weights.items():
            if name in index_data:
                s = calc_index_score(index_data[name])
                trend_total += s["trend"] * w
                pos_total += s["position"] * w

        daily = get_daily()
        limit_up_count = len(daily[daily["pct_chg"] >= 9.5]) if daily is not None and not isinstance(daily, dict) else 0
        limit_down_count = len(daily[daily["pct_chg"] <= -9.5]) if daily is not None and not isinstance(daily, dict) else 0
        ratio = limit_up_count / max(limit_down_count, 1)
        sentiment_score = 90 if ratio >= 5 else (75 if ratio >= 3 else (60 if ratio >= 1.5 else (45 if ratio >= 0.8 else (25 if ratio >= 0.3 else 10))))

        vol_ratio = 0
        for name, w in idx_weights.items():
            df = index_data.get(name)
            if df is not None and len(df) >= 25:
                cur, avg = df["amount"].iloc[-1], df["amount"].tail(20).mean()
                if cur > 0 and avg > 0:
                    vol_ratio += (cur / avg) * w
        volume_score = 100 if vol_ratio >= 2.0 else (85 if vol_ratio >= 1.5 else (70 if vol_ratio >= 1.2 else (50 if vol_ratio >= 0.8 else (30 if vol_ratio >= 0.5 else 10))))

        total_score = trend_total * 0.40 + pos_total * 0.20 + sentiment_score * 0.20 + volume_score * 0.20
        states = [(85,"强势牛市🚀","指数多头排列，量价齐升",(80,100)),(65,"牛市📈","指数趋势向上，活跃",(65,80)),
                  (45,"震荡偏多↗️","震荡上行，精选个股",(45,65)),(25,"震荡市➡️","横盘整理，控制仓位",(25,45)),
                  (10,"震荡偏空↘️","重心下移，轻仓防守",(10,25)),(0,"熊市📉","空头排列，空仓为主",(0,10))]
        state, state_desc, pos_range = "危机模式⚠️", "市场恐慌，空仓观望", (0,5)
        for threshold, s_name, s_desc, s_range in states:
            if total_score >= threshold:
                state, state_desc, pos_range = s_name, s_desc, s_range; break

        suggested_pos = round((pos_range[0] + pos_range[1]) / 2, 1)
        up = limit_up_count
        down = limit_down_count
        total_stocks = 0
        if daily is not None and not isinstance(daily, dict):
            total_stocks, up, down = len(daily), len(daily[daily["pct_chg"] > 0]), len(daily[daily["pct_chg"] < 0])

        return {
            "total_score": round(total_score, 1), "phase": state, "description": state_desc,
            "position_ratio": suggested_pos, "position_range": f"{pos_range[0]}%-{pos_range[1]}%",
            "index_trend_score": round(trend_total, 1), "index_position_score": round(pos_total, 1),
            "sentiment_score": sentiment_score, "volume_score": volume_score, "volume_ratio": round(vol_ratio, 2),
            "total": total_stocks, "up": up, "down": down, "limit_up": limit_up_count, "limit_down": limit_down_count,
            "zt_ratio": round(ratio, 2),
            "formula": "4维综合: 趋势40%+位置20%+情绪20%+量能20%",
        }
    except Exception as e:
        return {"error": str(e)}


# ═══════════════════════════════════════════
# 板块分析
# ═══════════════════════════════════════════
def fetch_sectors():
    """行业板块排行"""
    daily = get_daily()
    basic = get_stock_basic()
    if daily is None or isinstance(daily, dict):
        return []
    merged = daily.copy()
    merged["industry"] = merged["ts_code"].map(
        lambda c: basic.get(c, {}).get("industry", "未知") if isinstance(basic, dict) else "未知"
    )
    groups = merged[merged["industry"] != "未知"].groupby("industry")
    result = []
    for ind, grp in groups:
        avg_chg = grp["pct_chg"].mean()
        up = len(grp[grp["pct_chg"] > 0])
        result.append({"name": ind, "change": round(avg_chg, 2), "up_count": up, "down_count": len(grp) - up})
    result.sort(key=lambda x: x["change"], reverse=True)
    return result[:15]


def fetch_sector_flow():
    """板块资金流 — 强度评分"""
    daily = get_daily()
    basic = get_stock_basic()
    if daily is None or isinstance(daily, dict):
        return []
    basic = {} if (isinstance(basic, dict) and "error" in basic) else (basic or {})
    df = daily.copy()
    df["industry"] = df["ts_code"].map(lambda c: basic.get(c, {}).get("industry", "未知") if isinstance(basic, dict) else "未知")
    df = df[df["industry"] != "未知"]
    result = []
    for ind, grp in df.groupby("industry"):
        avg_chg = grp["pct_chg"].mean()
        up_ratio = len(grp[grp["pct_chg"] > 0]) / max(len(grp), 1) * 100
        strength = round(avg_chg * 0.6 + up_ratio * 0.04, 1)
        result.append({"name": ind, "change": round(avg_chg, 2), "up_ratio": round(up_ratio, 1),
                        "stock_count": len(grp), "strength": strength, "hot": strength > 2})
    result.sort(key=lambda x: x["strength"], reverse=True)
    return result[:15]


def fetch_hot_stocks():
    """热门个股涨幅 TOP 20"""
    daily = get_daily()
    if daily is None or isinstance(daily, dict):
        return []
    basic = get_stock_basic()
    daily_basic = get_daily_basic()
    turnover_map = {}
    if daily_basic is not None and not isinstance(daily_basic, dict):
        turnover_map = daily_basic.set_index("ts_code")["turnover_rate"].to_dict()
    daily2 = daily.copy()
    daily2["name"] = daily2["ts_code"].map(lambda c: basic.get(c, {}).get("name", "") if isinstance(basic, dict) else "")
    daily2["turnover"] = daily2["ts_code"].map(turnover_map).fillna(0)
    sorted_df = daily2.sort_values("pct_chg", ascending=False).head(20)
    return [{"code": r["ts_code"].replace(".SZ","").replace(".SH",""), "name": r["name"],
             "price": round(float(r["close"]),2), "change": round(float(r["pct_chg"]),2),
             "volume": round(float(r.get("amount",0))/1e5,1), "turnover": round(float(r.get("turnover",0)),1)}
            for _, r in sorted_df.iterrows()]


def fetch_limit_up():
    """涨停板"""
    daily = get_daily()
    if daily is None or isinstance(daily, dict):
        return []
    basic = get_stock_basic()
    df = daily[daily["pct_chg"] >= 9.5].sort_values("pct_chg", ascending=False)
    return [{"code": r["ts_code"].replace(".SZ","").replace(".SH",""),
             "name": basic.get(r["ts_code"], {}).get("name","") if isinstance(basic, dict) else "",
             "price": round(float(r["close"]),2), "change": round(float(r["pct_chg"]),2),
             "board_count": 1} for _, r in df.iterrows()][:20]
