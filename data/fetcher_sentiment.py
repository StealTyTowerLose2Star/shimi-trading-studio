"""
拾米交易工作室 - 数据层：市场情绪 & 涨停板
"""
from datetime import datetime, timedelta

from data.fetcher_core import get_ts, get_daily, get_stock_basic, get_daily_basic, get_latest_date


# ============================================================
# 热门个股
# ============================================================

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
    daily2["name"] = daily2["ts_code"].map(
        lambda c: basic.get(c, {}).get("name", "") if isinstance(basic, dict) else ""
    )
    daily2["turnover"] = daily2["ts_code"].map(turnover_map).fillna(0)
    sorted_df = daily2.sort_values("pct_chg", ascending=False).head(20)
    return [
        {"code": row["ts_code"].replace(".SZ","").replace(".SH",""),
         "name": row["name"],
         "price": round(float(row["close"]), 2),
         "change": round(float(row["pct_chg"]), 2),
         "volume": round(float(row.get("amount", 0)) / 1e5, 1),
         "turnover": round(float(row.get("turnover", 0)), 1)}
        for _, row in sorted_df.iterrows()
    ]


# ============================================================
# 市场情绪分析
# ============================================================

def fetch_sentiment():
    """市场状态分析 — 4 维综合评分"""
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
                df = pro.index_daily(ts_code=code, start_date=start, end_date=today,
                                     fields="trade_date,close,vol,amount")
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

            slope_score = 12.5
            if len(df) >= 25:
                slope = (ma20 - df["ma20"].iloc[-5]) / df["ma20"].iloc[-20] * 100
                slope_score = min(25, max(0, slope * 10))

            above_ma20 = close > ma20
            above_ma60 = close > ma60
            if above_ma20 and above_ma60:
                dist = (close - ma20) / ma20 * 100
                pos_score = 15 if dist > 15 else 25
            elif above_ma20:
                pos_score = 15
            elif above_ma60:
                pos_score = 10
            else:
                pos_score = 5 if (ma60 - close) / ma60 * 100 < 5 else 0

            return {"trend": round(align + slope_score + pos_score, 1), "position": pos_score,
                    "above_ma20": bool(above_ma20), "above_ma60": bool(above_ma60)}

        trend_total = 0
        pos_total = 0
        for name, w in idx_weights.items():
            if name in index_data:
                s = calc_index_score(index_data[name])
                trend_total += s["trend"] * w
                pos_total += s["position"] * w

        daily = get_daily()
        limit_up_count = 0
        limit_down_count = 0
        if daily is not None and not isinstance(daily, dict):
            limit_up_count = len(daily[daily["pct_chg"] >= 9.5])
            limit_down_count = len(daily[daily["pct_chg"] <= -9.5])

        if limit_down_count == 0:
            ratio = 10 + limit_up_count
        else:
            ratio = limit_up_count / limit_down_count

        if ratio >= 5: sentiment_score = 90
        elif ratio >= 3: sentiment_score = 75
        elif ratio >= 1.5: sentiment_score = 60
        elif ratio >= 0.8: sentiment_score = 45
        elif ratio >= 0.3: sentiment_score = 25
        else: sentiment_score = 10

        vol_ratio = 0
        for name, w in idx_weights.items():
            df = index_data.get(name)
            if df is not None and len(df) >= 25:
                cur = df["amount"].iloc[-1]
                avg = df["amount"].tail(20).mean()
                if cur > 0 and avg > 0:
                    vol_ratio += (cur / avg) * w

        if vol_ratio >= 2.0: volume_score = 100
        elif vol_ratio >= 1.5: volume_score = 85
        elif vol_ratio >= 1.2: volume_score = 70
        elif vol_ratio >= 0.8: volume_score = 50
        elif vol_ratio >= 0.5: volume_score = 30
        else: volume_score = 10

        total_score = trend_total * 0.40 + pos_total * 0.20 + sentiment_score * 0.20 + volume_score * 0.20

        MARKET_STATES = [
            (85, "强势牛市🚀", "指数多头排列，量价齐升", (80, 100)),
            (65, "牛市📈", "指数趋势向上，市场活跃", (65, 80)),
            (45, "震荡偏多↗️", "指数震荡上行，精选个股", (45, 65)),
            (25, "震荡市➡️", "指数横盘整理，控制仓位", (25, 45)),
            (10, "震荡偏空↘️", "重心下移，轻仓防守", (10, 25)),
            (0,  "熊市📉", "空头排列，空仓为主", (0, 10)),
        ]
        state = "危机模式⚠️"
        state_desc = "市场恐慌，空仓观望"
        pos_range = (0, 5)
        for threshold, s_name, s_desc, s_range in MARKET_STATES:
            if total_score >= threshold:
                state = s_name
                state_desc = s_desc
                pos_range = s_range
                break

        suggested_pos = round((pos_range[0] + pos_range[1]) / 2, 1)

        up = limit_up_count
        down = limit_down_count
        total_stocks = 0
        if daily is not None and not isinstance(daily, dict):
            total_stocks = len(daily)
            up = len(daily[daily["pct_chg"] > 0])
            down = len(daily[daily["pct_chg"] < 0])

        return {
            "total_score": round(total_score, 1),
            "phase": state, "description": state_desc,
            "position_ratio": suggested_pos,
            "position_range": f"{pos_range[0]}%-{pos_range[1]}%",
            "index_trend_score": round(trend_total, 1),
            "index_position_score": round(pos_total, 1),
            "sentiment_score": sentiment_score,
            "volume_score": volume_score,
            "volume_ratio": round(vol_ratio, 2),
            "total": total_stocks, "up": up, "down": down,
            "limit_up": limit_up_count, "limit_down": limit_down_count,
            "zt_ratio": round(ratio, 2),
            "source": "hybrid-strategy/MarketStateAnalyzer",
            "formula": "4维综合: 趋势40%+位置20%+情绪20%+量能20%",
        }
    except Exception as e:
        return {"error": str(e)}


# ============================================================
# 涨停板
# ============================================================

def fetch_limit_up():
    """涨停板 — pct_chg >= 9.5 的股票 TOP 20"""
    daily = get_daily()
    if daily is None or isinstance(daily, dict):
        return []
    basic = get_stock_basic()
    df = daily[daily["pct_chg"] >= 9.5].copy()
    df["name"] = df["ts_code"].map(
        lambda c: basic.get(c, {}).get("name", "") if isinstance(basic, dict) else ""
    )
    # Count consecutive limit-up days
    def board_count(code):
        try:
            from position_manager import get_kline
            kline = get_kline(code, days=20)
            if kline is not None and not kline.empty:
                up_days = (kline["pct_chg"] >= 9.5).astype(int)
                count = 0
                for v in reversed(up_days.values):
                    if v: count += 1
                    else: break
                return count
        except:
            pass
        return 1

    df["board_count"] = df["ts_code"].apply(board_count)
    result = []
    for _, row in df.head(20).iterrows():
        result.append({
            "code": row["ts_code"].replace(".SZ","").replace(".SH",""),
            "name": row["name"],
            "price": round(float(row["close"]), 2),
            "change": round(float(row["pct_chg"]), 2),
            "board_count": int(row["board_count"]),
        })
    return result


def fetch_limit_down():
    """跌停板 — pct_chg <= -9.5 的股票"""
    daily = get_daily()
    if daily is None or isinstance(daily, dict):
        return []
    basic = get_stock_basic()
    df = daily[daily["pct_chg"] <= -9.5].copy()
    df["name"] = df["ts_code"].map(
        lambda c: basic.get(c, {}).get("name", "") if isinstance(basic, dict) else ""
    )
    result = []
    for _, row in df.head(20).iterrows():
        result.append({
            "code": row["ts_code"].replace(".SZ","").replace(".SH",""),
            "name": row["name"],
            "price": round(float(row["close"]), 2),
            "change": round(float(row["pct_chg"]), 2),
        })
    return result


# ============================================================
# 融资融券
# ============================================================

def get_margin_detail(ts_code: str, trade_date: str = None) -> dict:
    """获取个股融资融券数据"""
    try:
        pro = get_ts()
        if not trade_date:
            trade_date = get_latest_date()
            if isinstance(trade_date, dict):
                return {"error": "no trade date"}
        df = pro.margin_detail(ts_code=ts_code, trade_date=trade_date)
        if df is not None and not df.empty:
            row = df.iloc[-1]
            return {
                "rzye": float(row.get("rzye", 0)),
                "rzmre": float(row.get("rzmre", 0)),
                "rqyl": float(row.get("rqyl", 0)),
                "rqmcl": float(row.get("rqmcl", 0)),
            }
        # 回退到前一日
        from datetime import datetime, timedelta
        prev = (datetime.strptime(trade_date, "%Y%m%d") - timedelta(days=3)).strftime("%Y%m%d")
        df2 = pro.margin_detail(ts_code=ts_code, trade_date=prev)
        if df2 is not None and not df2.empty:
            row = df2.iloc[-1]
            return {
                "rzye": float(row.get("rzye", 0)),
                "rzmre": float(row.get("rzmre", 0)),
                "rqyl": float(row.get("rqyl", 0)),
                "rqmcl": float(row.get("rqmcl", 0)),
            }
        return {"error": "no margin data"}
    except Exception as e:
        return {"error": str(e)}
