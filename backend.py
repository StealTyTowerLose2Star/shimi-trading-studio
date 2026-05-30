"""
拾米交易工作室 - A股策略后台服务 (tushare 数据源)
Backend for ShiMi Trading Studio
"""
import sys, os, json, time, threading
sys.path.insert(0, os.path.dirname(__file__))

from flask import Flask, jsonify
from flask_cors import CORS
import pandas as pd
import numpy as np
from flask.json.provider import DefaultJSONProvider

class NumpyJSONProvider(DefaultJSONProvider):
    def default(self, obj):
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, (np.bool_,)):
            return bool(obj)
        if isinstance(obj, (np.ndarray,)):
            return obj.tolist()
        return super().default(obj)

from realtime_scorer import (
    trend_detect, hybrid_score, dragon_leader_score,
    get_kline, _count_consecutive_limit, get_ts,
)
from position_manager import batch_evaluate, evaluate_position
from db import (login_user, verify_token, register_user, list_users,
                add_trade, update_trade, delete_trade, get_trades, get_trade_summary)

app = Flask(__name__)
app.json = NumpyJSONProvider(app)
CORS(app)

# ============================================================
# tushare 初始化
# ============================================================
TUSHARE_TOKEN = "b5e768c112082f5a38f3400244859d3f0ef9d917296600068d6cbf49"


def get_ts():
    import tushare as ts
    return ts.pro_api(TUSHARE_TOKEN)


# ============================================================
# 缓存
# ============================================================
CACHE = {}
CACHE_UPDATED = {}

def cache_or_fetch(key, fn, ttl=60):
    now = time.time()
    if key in CACHE and (now - CACHE_UPDATED.get(key, 0)) < ttl:
        return CACHE[key]
    try:
        data = fn()
        CACHE[key] = data
        CACHE_UPDATED[key] = now
        return data
    except Exception as e:
        return {"error": str(e)}


def fetch_latest_trade_date():
    """获取最近交易日"""
    try:
        pro = get_ts()
        df = pro.daily(trade_date="", limit=1)
        if df.empty:
            # fallback: try yesterday
            from datetime import date, timedelta
            return (date.today() - timedelta(days=1)).strftime("%Y%m%d")
        return df["trade_date"].iloc[0]
    except:
        from datetime import date, timedelta
        return (date.today() - timedelta(days=1)).strftime("%Y%m%d")


def fetch_all_stocks_basic():
    """获取全市场股票基础信息（含行业）"""
    try:
        pro = get_ts()
        df = pro.stock_basic(exchange="", list_status="L",
                             fields="ts_code,symbol,name,industry,market,area")
        if df.empty:
            return {}
        return {row["ts_code"]: row.to_dict() for _, row in df.iterrows()}
    except:
        return {}


def fetch_daily_data(trade_date):
    """获取某交易日全部股票行情"""
    pro = get_ts()
    df = pro.daily(trade_date=trade_date,
                   fields="ts_code,open,high,low,close,pre_close,pct_chg,amount,vol")
    if df.empty:
        return None
    return df


def fetch_daily_basic(trade_date):
    """获取某交易日换手率等数据"""
    try:
        pro = get_ts()
        df = pro.daily_basic(trade_date=trade_date,
                             fields="ts_code,turnover_rate,volume_ratio,total_mv,circ_mv")
        if df.empty:
            return None
        return df
    except:
        return None


# ============================================================
# 数据缓存层
# ============================================================
def get_latest_date():
    return cache_or_fetch("latest_date", fetch_latest_trade_date, 300)

def get_stock_basic():
    return cache_or_fetch("stock_basic", fetch_all_stocks_basic, 3600)

def get_daily():
    date = get_latest_date()
    if isinstance(date, dict) and "error" in date:
        return None
    df = cache_or_fetch(f"daily_{date}", lambda: fetch_daily_data(date), 60)
    return df

def get_daily_basic():
    date = get_latest_date()
    if isinstance(date, dict) and "error" in date:
        return None
    df = cache_or_fetch(f"daily_basic_{date}", lambda: fetch_daily_basic(date), 60)
    return df


# ============================================================
# API: 指数
# ============================================================
# 指数 ts_code 映射
INDEX_MAP = {
    "000001.SH": "上证指数",
    "399001.SZ": "深证成指",
    "399006.SZ": "创业板指",
    "000688.SH": "科创50",
}

def fetch_indices():
    """四大指数最新行情"""
    pro = get_ts()
    today = get_latest_date()
    if isinstance(today, dict):
        today = ""
    result = []
    for code, name in INDEX_MAP.items():
        try:
            df = pro.index_daily(ts_code=code, start_date=today, end_date=today,
                                 fields="ts_code,trade_date,close,pct_chg,high,low,vol,amount")
            if df.empty:
                # fallback: last 5 days
                df = pro.index_daily(ts_code=code, limit=5,
                                     fields="ts_code,trade_date,close,pct_chg,high,low,vol,amount")
            if not df.empty:
                row = df.iloc[-1]
                result.append({
                    "name": name, "code": code,
                    "price": round(float(row["close"]), 2),
                    "change": round(float(row["pct_chg"]), 2),
                    "high": round(float(row["high"]), 2) if "high" in row else 0,
                    "low": round(float(row["low"]), 2) if "low" in row else 0,
                })
        except:
            pass
    return result


# ============================================================
# API: 板块排行
# ============================================================
def fetch_sectors():
    """行业板块排行"""
    daily = get_daily()
    basic = get_stock_basic()
    if daily is None or isinstance(daily, dict):
        return []
    if isinstance(basic, dict) and "error" in basic:
        return []

    # Merge daily with industry
    merged = daily.copy()
    merged["industry"] = merged["ts_code"].map(
        lambda c: basic.get(c, {}).get("industry", "未知") if isinstance(basic, dict) else "未知"
    )

    # Group by industry, compute stats
    groups = merged[merged["industry"] != "未知"].groupby("industry")
    result = []
    for ind, grp in groups:
        avg_chg = grp["pct_chg"].mean()
        up = len(grp[grp["pct_chg"] > 0])
        total = len(grp)
        result.append({"name": ind, "change": round(avg_chg, 2), "up_count": up, "down_count": total - up})
    result.sort(key=lambda x: x["change"], reverse=True)
    return result[:15]


# ============================================================
# API: 热门个股 + 情绪
# ============================================================
def fetch_hot_stocks():
    """热门个股涨幅 TOP 20"""
    daily = get_daily()
    if daily is None or isinstance(daily, dict):
        return []
    basic = get_stock_basic()

    # Merge with name
    daily2 = daily.copy()
    daily2["name"] = daily2["ts_code"].map(
        lambda c: basic.get(c, {}).get("name", "") if isinstance(basic, dict) else ""
    )

    sorted_df = daily2.sort_values("pct_chg", ascending=False).head(20)
    return [
        {
            "code": row["ts_code"].replace(".SZ", "").replace(".SH", ""),
            "name": row["name"],
            "price": round(float(row["close"]), 2),
            "change": round(float(row["pct_chg"]), 2),
            "volume": round(float(row.get("amount", 0)), 2),
            "pct_chg": round(float(row["pct_chg"]), 2),
        }
        for _, row in sorted_df.iterrows()
    ]


def fetch_sentiment():
    """市场状态分析 — 来自 hybrid-strategy MarketStateAnalyzer
    4 维综合评分: 指数趋势40% + 指数位置20% + 情绪20% + 成交量20%
    输出 7 级市场状态 + 建议仓位
    """
    from datetime import datetime, timedelta

    try:
        pro = get_ts()
        today = get_latest_date()
        if isinstance(today, dict):
            today = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=150)).strftime("%Y%m%d")

        # 1) 获取三大指数数据
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

        # 2) 指数趋势评分 (40%) — MA排列+斜率+价格位置
        def calc_index_score(df):
            if df is None or len(df) < 60:
                return {"trend": 0, "position": 0}
            l = df.iloc[-1]
            ma5, ma10, ma20, ma60 = l["ma5"], l["ma10"], l["ma20"], l["ma60"]
            close = l["close"]

            # 均线排列 (50分)
            align = 0
            if ma5 > ma10: align += 12.5
            if ma10 > ma20: align += 12.5
            if ma20 > ma60: align += 12.5
            if ma5 > ma60: align += 12.5
            if align == 50: align = 50  # 满分配置

            # MA20斜率 (25分)
            if len(df) >= 25:
                slope = (ma20 - df["ma20"].iloc[-5]) / df["ma20"].iloc[-20] * 100
                slope_score = min(25, max(0, slope * 10))
            else:
                slope_score = 12.5

            # 价格位置 (25分)
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

        # 3) 情绪评分 (20%) — 涨停/跌停比分段映射
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

        # 4) 成交量评分 (20%) — 指数量 vs 20日均量
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

        # 5) 综合评分
        total_score = trend_total * 0.40 + pos_total * 0.20 + sentiment_score * 0.20 + volume_score * 0.20

        # 6) 市场状态映射
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

        # 建议仓位(取区间中值)
        suggested_pos = round((pos_range[0] + pos_range[1]) / 2, 1)

        # 涨跌统计
        up = limit_up_count
        down = limit_down_count
        total_stocks = 0
        if daily is not None and not isinstance(daily, dict):
            total_stocks = len(daily)
            up = len(daily[daily["pct_chg"] > 0])
            down = len(daily[daily["pct_chg"] < 0])

        return {
            "total_score": round(total_score, 1),
            "phase": state,
            "description": state_desc,
            "position_ratio": suggested_pos,
            "position_range": f"{pos_range[0]}%-{pos_range[1]}%",
            # 维度分
            "index_trend_score": round(trend_total, 1),
            "index_position_score": round(pos_total, 1),
            "sentiment_score": sentiment_score,
            "volume_score": volume_score,
            "volume_ratio": round(vol_ratio, 2),
            # 原始数据
            "total": total_stocks,
            "up": up,
            "down": down,
            "limit_up": limit_up_count,
            "limit_down": limit_down_count,
            "zt_ratio": round(ratio, 2),
            "source": "hybrid-strategy/MarketStateAnalyzer",
            "formula": "4维综合: 趋势40%+位置20%+情绪20%+量能20%",
        }
    except Exception as e:
        return {"error": str(e)}


# ============================================================
# API: 涨停板
# ============================================================
def fetch_limit_up():
    """涨停板"""
    daily = get_daily()
    if daily is None or isinstance(daily, dict):
        return []
    basic = get_stock_basic()

    df = daily[daily["pct_chg"] >= 9.5].sort_values("pct_chg", ascending=False)
    # Get consecutive board counts (simplified — real data would need limit_list)
    daily_basic = get_daily_basic()

    result = []
    for _, row in df.iterrows():
        code = row["ts_code"].replace(".SZ", "").replace(".SH", "")
        result.append({
            "code": code,
            "name": basic.get(row["ts_code"], {}).get("name", "") if isinstance(basic, dict) else "",
            "price": round(float(row["close"]), 2),
            "change": round(float(row["pct_chg"]), 2),
            "board_count": 1,  # simplified — full data would come from limit_list
        })
    return result[:20]


# ============================================================
# 策略扫描
# ============================================================
def run_trend_scan():
    """趋势策略 — 真实 TrendDetector 评分"""
    daily = get_daily()
    basic = get_stock_basic()
    if daily is None or isinstance(daily, dict):
        return {"picked": [], "total_scanned": 0}

    # Build quick lookup for name and pct_chg
    name_map = {}
    chg_map = {}
    if isinstance(basic, dict):
        for code, info in basic.items():
            short = code.replace(".SZ","").replace(".SH","").replace(".BJ","")
            name_map[short] = info.get("name", "")
    if isinstance(daily, pd.DataFrame) and not isinstance(daily, dict):
        for _, row in daily.iterrows():
            short = row["ts_code"].replace(".SZ","").replace(".SH","").replace(".BJ","")
            chg_map[short] = float(row["pct_chg"])

    # Pre-filter: top 20 by pct_chg
    candidates = daily.sort_values("pct_chg", ascending=False).head(20)
    stock_codes = [c.replace(".SZ","").replace(".SH","").replace(".BJ","")
                   for c in candidates["ts_code"]]

    results = []
    for code in stock_codes:
        try:
            r = trend_detect(code)
            if r and r["total_score"] >= 30:
                results.append({
                    "code": code,
                    "name": name_map.get(code, ""),
                    "price": r["price"],
                    "change": round(chg_map.get(code, 0), 2),
                    "trend_score": r["total_score"],
                    "stage": r["stage"],
                    "strength": r["strength"],
                    "trend_formed": bool(r["trend_formed"]),
                })
        except:
            continue

    results.sort(key=lambda x: x["trend_score"], reverse=True)
    return {"picked": results[:15], "total_scanned": len(stock_codes),
            "engine": "TrendDetector (real)"}


def run_hybrid_scan():
    """混合策略 — 真实 MergedScorer 7维评分"""
    daily = get_daily()
    basic = get_stock_basic()
    if daily is None or isinstance(daily, dict):
        return {"picked": [], "total_scanned": 0}

    candidates = daily.sort_values("pct_chg", ascending=False).head(20)
    stock_codes = [c.replace(".SZ","").replace(".SH","").replace(".BJ","")
                   for c in candidates["ts_code"]]

    # Build sector map
    sector_map = {}
    if isinstance(basic, dict):
        sector_map = {k: v.get("industry", "") for k, v in basic.items()}

    # Build name/chg maps
    name_map = {}
    chg_map = {}
    if isinstance(basic, dict):
        for c, info in basic.items():
            short = c.replace(".SZ","").replace(".SH","").replace(".BJ","")
            name_map[short] = info.get("name", "")
    if isinstance(daily, pd.DataFrame):
        for _, row in daily.iterrows():
            short = row["ts_code"].replace(".SZ","").replace(".SH","").replace(".BJ","")
            chg_map[short] = float(row["pct_chg"])

    results = []
    for code in stock_codes:
        try:
            sector = sector_map.get(code, "")
            r = hybrid_score(code, industry=sector)
            if r and r["score"] >= 15:
                dims = r.get("dimensions", {})
                results.append({
                    "code": code,
                    "name": name_map.get(code, ""),
                    "price": r["price"],
                    "change": chg_map.get(code, r["pct_chg"]),
                    "score": r["score"],
                    "grade": r["grade"],
                    "position_pct": r["position_pct"],
                    "d_trend": dims.get("d1_trend", 0),
                    "d_momentum": dims.get("d2_momentum", 0),
                    "d_volume": dims.get("d5_volume", 0),
                    "d_safety": dims.get("d6_safety", 0),
                    "burst": dims.get("burst_bonus", 0),
                })
        except:
            pass

    results.sort(key=lambda x: x["score"], reverse=True)
    return {"picked": results[:15], "total_scanned": len(stock_codes),
            "engine": "MergedScorer 7D (real)"}


def run_dragon_scan():
    """龙头战法 — 真实 LeaderScorer 5维评分"""
    daily = get_daily()
    basic = get_stock_basic()
    if daily is None or isinstance(daily, dict):
        return {"picked": [], "total_scanned": 0}

    # Build maps
    name_map = {}
    chg_map = {}
    if isinstance(basic, dict):
        for c, info in basic.items():
            short = c.replace(".SZ","").replace(".SH","").replace(".BJ","")
            name_map[short] = info.get("name", "")
    if isinstance(daily, pd.DataFrame):
        for _, row in daily.iterrows():
            short = row["ts_code"].replace(".SZ","").replace(".SH","").replace(".BJ","")
            chg_map[short] = float(row["pct_chg"])

    # Get limit-up candidates
    limits = daily[daily["pct_chg"] >= 9.5]
    if limits.empty:
        limits = daily.sort_values("pct_chg", ascending=False).head(20)
    else:
        limits = limits.sort_values("pct_chg", ascending=False).head(20)

    stock_codes = [c.replace(".SZ","").replace(".SH","").replace(".BJ","")
                   for c in limits["ts_code"]]

    results = []
    for code in stock_codes:
        try:
            r = dragon_leader_score(code)
            if r:
                dims = r.get("dimensions", {})
                results.append({
                    "code": code,
                    "name": name_map.get(code, ""),
                    "price": r["price"],
                    "change": chg_map.get(code, r["pct_chg"]),
                    "leader_score": r["leader_score"],
                    "grade": r["grade"],
                    "board_count": r["board_count"],
                    "d_sector": dims.get("sector_rank", 0),
                    "d_consecutive": dims.get("consecutive", 0),
                    "d_time": dims.get("limit_time", 0),
                    "d_drive": dims.get("drive_effect", 0),
                })
        except:
            pass

    results.sort(key=lambda x: x["leader_score"], reverse=True)
    return {"picked": results[:15], "total_scanned": len(stock_codes),
            "engine": "LeaderScorer 5D (real)"}


# ============================================================
# 操作建议引擎
# ============================================================
def generate_advice():
    """综合三大策略 + 市场环境 → 操作建议
    使用已缓存的结果避免重复拉取 tushare"""
    # Use cached results to avoid 60+ tushare calls
    trend = cache_or_fetch("strategy_trend", run_trend_scan, 120)
    hybrid = cache_or_fetch("strategy_hybrid", run_hybrid_scan, 120)
    dragon = cache_or_fetch("strategy_dragon", run_dragon_scan, 120)
    sentiment = cache_or_fetch("sentiment", fetch_sentiment, 30)
    sectors = cache_or_fetch("sectors", fetch_sectors, 120)

    market_phase = sentiment.get("phase", "未知")
    phase_advice = {
        "强势牛市🚀": {"position": "重仓 · 80-100%", "action": "全仓出击，顺势加仓", "risk": "低"},
        "牛市📈":     {"position": "较重 · 65-80%",  "action": "积极做多，精选主线", "risk": "低"},
        "震荡偏多↗️": {"position": "中等偏重 · 45-65%", "action": "谨慎做多，控制单票仓位", "risk": "中"},
        "震荡市➡️":  {"position": "中等 · 25-45%",  "action": "平衡仓位，高抛低吸", "risk": "中"},
        "震荡偏空↘️": {"position": "轻仓 · 10-25%",  "action": "防守为主，快进快出", "risk": "较高"},
        "熊市📉":     {"position": "空仓 · 0-10%",   "action": "空仓等待，现金为王", "risk": "高"},
        "危机模式⚠️": {"position": "空仓 · 0-5%",    "action": "空仓观望，等待系统性风险释放", "risk": "极高"},
    }
    market_advice = phase_advice.get(market_phase, {"position": "30%", "action": "谨慎", "risk": "中"})

    # Build code -> info maps
    def to_map(items, key="code"):
        return {s.get(key, ""): s for s in items if s.get(key)}

    trend_map = to_map(trend.get("picked", []))
    hybrid_map = to_map(hybrid.get("picked", []))
    dragon_map = to_map(dragon.get("picked", []))

    all_codes = set(list(trend_map.keys()) + list(hybrid_map.keys()) + list(dragon_map.keys()))

    recommendations = []
    for code in all_codes:
        strategies = []
        if code in trend_map:  strategies.append("趋势")
        if code in hybrid_map: strategies.append("混合")
        if code in dragon_map: strategies.append("龙头")

        consensus = len(strategies)
        if consensus < 2:
            continue

        # Get name & price from whichever source has it
        src = trend_map.get(code) or hybrid_map.get(code) or dragon_map.get(code)
        name = src.get("name", "")
        price = float(src.get("price", 0))

        # 止盈/止损计算 — ATR 动态 + 阶梯目标
        def calc_atr_based_levels(price, atr):
            """ATR 动态止盈止损"""
            atr_pct = atr / price
            # 目标1: 2x ATR (保守)
            t1 = price + 2 * atr
            # 目标2: 3.5x ATR (中等)
            t2 = price + 3.5 * atr
            # 目标3: 6x ATR (让利润奔跑)
            t3 = price + 6 * atr
            # 止损: 1.5x ATR
            sl = price - 1.5 * atr
            # 浮动止盈建议 (从目标1开始启动)
            trailing_start = t1
            trailing_step = 0.5 * atr  # 每上涨0.5 ATR上移一次止盈
            return t1, t2, t3, sl, trailing_start, trailing_step

        # 尝试拉 kline 算 ATR
        atr = None
        max_attempts = 3
        for s_name in ["trend_map", "hybrid_map", "dragon_map"]:
            src = locals().get(s_name, {})
            s = src.get(code)
            if s and s.get("price", 0) > 0:
                try:
                    kline = get_kline(code, days=30)
                    if kline is not None and len(kline) >= 15:
                        high, low, close = kline["high"], kline["low"], kline["close"].shift(1)
                        tr = pd.concat([(kline["high"]-kline["low"]).abs(),
                                        (kline["high"]-close).abs(),
                                        (kline["low"]-close).abs()], axis=1).max(axis=1)
                        atr = float(tr.rolling(14).mean().iloc[-1])
                    break
                except:
                    pass

        if atr and atr > 0:
            t1, t2, t3, sl, trailing_start, trailing_step = calc_atr_based_levels(price, atr)
            stop_loss = f"¥{round(sl,2)} (-{round((price-sl)/price*100,1)}%)"
            entry_low = round(price * 0.985, 2)
            entry_high = round(price * 1.015, 2)
            rr1 = round((t1 - price) / (price - sl), 1) if (price - sl) > 0 else 1.0
            rr2 = round((t2 - price) / (price - sl), 1) if (price - sl) > 0 else 1.0
            t1_label = f"¥{round(t1,2)} (+{round((t1-price)/price*100,1)}%)"
            t2_label = f"¥{round(t2,2)} (+{round((t2-price)/price*100,1)}%)"
            t3_label = f"¥{round(t3,2)} (+{round((t3-price)/price*100,1)}%)"
        else:
            # 无 ATR 数据：用固定百分比（更宽）
            t1 = price * 1.10
            t2 = price * 1.20
            t3 = price * 1.35
            sl = price * 0.95
            entry_low = round(price * 0.98, 2)
            entry_high = round(price * 1.02, 2)
            stop_loss = f"¥{round(sl,2)} (-5.0%)"
            rr1 = round((t1 - price) / (price - sl), 1) if (price - sl) > 0 else 1.0
            rr2 = round((t2 - price) / (price - sl), 1) if (price - sl) > 0 else 1.0
            t1_label = f"¥{round(t1,2)} (+10.0%)"
            t2_label = f"¥{round(t2,2)} (+20.0%)"
            t3_label = f"¥{round(t3,2)} (+35.0%)"
            trailing_start = t1
            trailing_step = price * 0.03

        trailing_stop = f"从¥{round(trailing_start,2)}启动，每涨{round(trailing_step,2)}上移一次浮动止盈"

        reasons = []
        if "趋势" in strategies:
            s = trend_map[code]
            reasons.append(f"趋势评分{s.get('trend_score','?')}·{s.get('stage','?')}")
        if "混合" in strategies:
            s = hybrid_map[code]
            reasons.append(f"混合{s.get('score','?')}分·评级{s.get('grade','?')}")
        if "龙头" in strategies:
            s = dragon_map[code]
            reasons.append(f"龙头{s.get('leader_score','?')}分·{s.get('grade','?')}")

        recommendations.append({
            "code": code,
            "name": name,
            "consensus": consensus,
            "strategies": strategies,
            "signal": "⭐⭐⭐" if consensus >= 3 else "⭐⭐",
            "price": price,
            "entry_zone": f"¥{entry_low} ~ ¥{entry_high}",
            "stop_loss": stop_loss,
            "target_1": t1_label,
            "target_2": t2_label,
            "target_3": t3_label,
            "risk_reward_1": rr1,
            "risk_reward_2": rr2,
            "trailing_stop": trailing_stop,
            "position": "25%" if consensus >= 3 else "15%",
            "reason": " | ".join(reasons),
        })

    recommendations.sort(key=lambda x: (x["consensus"], -x["price"]), reverse=True)
    top_sectors = [s["name"] for s in (sectors[:5] if isinstance(sectors, list) else [])]

    return {
        "market": {
            "phase": market_phase,
            "sentiment_score": sentiment.get("sentiment_score", 0),
            "up": sentiment.get("up", 0),
            "down": sentiment.get("down", 0),
            "limit_up": sentiment.get("limit_up", 0),
            **market_advice,
        },
        "top_sectors": top_sectors,
        "recommendations": recommendations[:10],
        "generated_at": time.strftime("%H:%M:%S"),
    }


@app.route("/api/advice")
def api_advice():
    return jsonify(cache_or_fetch("advice", generate_advice, 120))


@app.route("/api/positions/evaluate", methods=["POST"])
def api_evaluate_positions():
    """批量评估持仓，返回动态止损/目标"""
    from flask import request
    data = request.get_json(force=True, silent=True) or {}
    positions = data.get("positions", [])
    if not positions:
        return jsonify({"error": "no positions", "results": []})
    results = batch_evaluate(positions)
    return jsonify({"results": results, "timestamp": time.strftime("%H:%M:%S")})


@app.route("/api/portfolio/advice")
def api_portfolio_advice():
    """持仓分析：对用户已持有的个股跑策略评分"""
    user = _require_user()
    if not user:
        return _unauthorized()

    trades = get_trades(user_id=user["id"])
    open_trades = [t for t in trades if not t.get("exit_price")]

    if not open_trades:
        return jsonify({"positions": [], "summary": {"total": 0, "advice": "无持仓"}})

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
        try:
            kline = get_kline(code, days=30)
            if kline is not None and len(kline) > 0:
                current_price = float(kline["close"].iloc[-1])
        except:
            pass

        pnl = (current_price - entry) * qty if t["direction"] == "buy" else (entry - current_price) * qty
        pnl_pct = (current_price - entry) / entry * 100 if t["direction"] == "buy" else (entry - current_price) / entry * 100
        total_pnl += pnl

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

        if sc >= 2 and safe:
            advice = "持有 ✅"
            parts = []
            if tb and trend: parts.append(f"趋势{trend['total_score']}分 {trend['stage']}")
            if hb and hybrid: parts.append(f"混合{hybrid['score']}分 {hybrid['grade']}")
            if safe: parts.append(f"浮盈+{round(pnl_pct,1)}%")
            reason = " ".join(parts)
            bullish_count += 1
        elif sc >= 1 or safe:
            advice = "观望 ⏳"
            reason = f"信号偏弱，浮盈{round(pnl_pct,1)}%"
        else:
            advice = "减仓 ⚠️"
            reason = f"策略看空，浮盈{round(pnl_pct,1)}%"

        sl_label = "--"
        try:
            ev = evaluate_position(code, entry, t["direction"])
            if ev and "stop_loss_label" in ev:
                sl_label = ev["stop_loss_label"]
        except:
            pass

        positions.append({
            "code": code, "name": t.get("name", ""),
            "entry_price": round(entry, 2), "current_price": round(current_price, 2),
            "qty": qty, "invested": round(invested, 2),
            "pnl": round(pnl, 2), "pnl_pct": round(pnl_pct, 1),
            "stop_loss": sl_label, "advice": advice, "reason": reason,
            "trend_score": trend["total_score"] if trend else None,
            "trend_stage": trend["stage"] if trend else None,
            "hybrid_score": hybrid["score"] if hybrid else None,
            "hybrid_grade": hybrid["grade"] if hybrid else None,
            "dragon_score": dragon["leader_score"] if dragon else None,
            "dragon_grade": dragon["grade"] if dragon else None,
        })

    risk = "低"
    if total_pnl < -total_invested * 0.05: risk = "高⚠️"
    elif total_pnl < 0: risk = "中"

    return jsonify({
        "positions": positions,
        "summary": {
            "total_positions": len(open_trades),
            "total_invested": round(total_invested, 2),
            "total_pnl": round(total_pnl, 2),
            "total_pnl_pct": round(total_pnl / total_invested * 100, 1) if total_invested > 0 else 0,
            "bullish_count": bullish_count,
            "risk": risk,
        }
    })


# ─── 账户与交易 API ─────────────────────────────────

def _require_user():
    """从请求头获取当前用户"""
    from flask import request
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
        user = verify_token(token)
        if user:
            return user
    return None


def _unauthorized():
    return jsonify({"error": "未登录或登录已过期"}), 401


@app.route("/api/auth/login", methods=["POST"])
def api_login():
    from flask import request
    data = request.get_json(force=True, silent=True) or {}
    username = data.get("username", "")
    password = data.get("password", "")
    result = login_user(username, password)
    if "error" in result:
        return jsonify(result), 401
    return jsonify(result)


@app.route("/api/auth/register", methods=["POST"])
def api_register():
    from flask import request
    data = request.get_json(force=True, silent=True) or {}
    result = register_user(
        data.get("username", ""),
        data.get("password", ""),
        data.get("display_name")
    )
    if "error" in result:
        return jsonify(result), 400
    return jsonify(result)


@app.route("/api/auth/me")
def api_me():
    user = _require_user()
    if not user:
        return _unauthorized()
    return jsonify({"user": user})


@app.route("/api/users")
def api_users():
    return jsonify(list_users())


@app.route("/api/trades", methods=["GET"])
def api_get_trades():
    user = _require_user()
    if not user:
        return _unauthorized()
    trades = get_trades(user_id=user["id"])
    return jsonify({"trades": trades, "summary": get_trade_summary(user["id"])})


@app.route("/api/trades/all")
def api_all_trades():
    user = _require_user()
    if not user:
        return _unauthorized()
    trades = get_trades()
    summary = get_trade_summary()
    return jsonify({"trades": trades, "summary": summary})


@app.route("/api/trades", methods=["POST"])
def api_add_trade():
    user = _require_user()
    if not user:
        return _unauthorized()
    from flask import request
    data = request.get_json(force=True, silent=True) or {}
    result = add_trade(user["id"], data)
    return jsonify(result)


@app.route("/api/trades/<int:trade_id>", methods=["PUT"])
def api_update_trade(trade_id):
    user = _require_user()
    if not user:
        return _unauthorized()
    from flask import request
    data = request.get_json(force=True, silent=True) or {}
    result = update_trade(trade_id, user["id"], data)
    if "error" in result:
        return jsonify(result), 404
    return jsonify(result)


@app.route("/api/trades/<int:trade_id>", methods=["DELETE"])
def api_delete_trade(trade_id):
    user = _require_user()
    if not user:
        return _unauthorized()
    result = delete_trade(trade_id, user["id"])
    if "error" in result:
        return jsonify(result), 404
    return jsonify(result)


@app.route("/api/health")
def health():
    date = get_latest_date()
    return jsonify({"status": "ok", "studio": "拾米交易工作室", "latest_trade_date": date})


@app.route("/api/indices")
def api_indices():
    return jsonify(cache_or_fetch("indices", fetch_indices, 30))


@app.route("/api/sectors")
def api_sectors():
    return jsonify(cache_or_fetch("sectors", fetch_sectors, 120))


@app.route("/api/hot-stocks")
def api_hot_stocks():
    return jsonify(cache_or_fetch("hot_stocks", fetch_hot_stocks, 30))


@app.route("/api/limit-up")
def api_limit_up():
    return jsonify(cache_or_fetch("limit_up", fetch_limit_up, 60))


@app.route("/api/sentiment")
def api_sentiment():
    return jsonify(cache_or_fetch("sentiment", fetch_sentiment, 30))


@app.route("/api/strategy/<name>")
def api_strategy(name):
    if name not in ["trend", "hybrid", "dragon"]:
        return jsonify({"error": f"unknown strategy: {name}"}), 404
    fns = {"trend": run_trend_scan, "hybrid": run_hybrid_scan, "dragon": run_dragon_scan}
    return jsonify(cache_or_fetch(f"strategy_{name}", fns[name], 120))


@app.route("/api/strategy/<name>/refresh")
def api_strategy_refresh(name):
    if name not in ["trend", "hybrid", "dragon"]:
        return jsonify({"error": f"unknown strategy: {name}"}), 404
    CACHE.pop(f"strategy_{name}", None)
    CACHE_UPDATED.pop(f"strategy_{name}", None)
    fns = {"trend": run_trend_scan, "hybrid": run_hybrid_scan, "dragon": run_dragon_scan}
    result = fns[name]()
    CACHE[f"strategy_{name}"] = result
    CACHE_UPDATED[f"strategy_{name}"] = time.time()
    return jsonify(result)


@app.route("/api/dashboard")
def api_dashboard():
    return jsonify({
        "indices": cache_or_fetch("indices", fetch_indices, 30),
        "sectors": cache_or_fetch("sectors", fetch_sectors, 120),
        "hot_stocks": cache_or_fetch("hot_stocks", fetch_hot_stocks, 30),
        "limit_up": cache_or_fetch("limit_up", fetch_limit_up, 60),
        "sentiment": cache_or_fetch("sentiment", fetch_sentiment, 30),
        "strategy_trend": cache_or_fetch("strategy_trend", run_trend_scan, 120),
        "strategy_hybrid": cache_or_fetch("strategy_hybrid", run_hybrid_scan, 120),
        "strategy_dragon": cache_or_fetch("strategy_dragon", run_dragon_scan, 120),
    })


@app.route("/")
def index():
    return app.send_static_file("index.html")


if __name__ == "__main__":
    app.static_folder = os.path.dirname(os.path.abspath(__file__))
    app.static_url_path = ""
    print("🚀 拾米交易工作室 Backend (tushare) 启动中...")
    print(f"   Dashboard: http://localhost:7890")
    print(f"   API:       http://localhost:7890/api/dashboard")
    app.run(host="0.0.0.0", port=7890, debug=True)
