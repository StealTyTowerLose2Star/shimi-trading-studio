"""
拾米交易工作室 - 月度翻倍股扫描引擎
从 standalone script 重构为服务层模块

提供:
1. scan_monthly_doublers() — 扫描历史月度翻倍股
2. analyze_doubler_features() — 深度特征分析
3. recommend_current_month() — 当月翻倍潜力股推荐

依赖: data/fetcher.py (数据层), cache.py (缓存层)
"""
import sys, os, time, json
from datetime import datetime, timedelta
from collections import defaultdict, Counter

import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from cache import cache_or_fetch, cache_set, cache_delete


# ============================================================
# 评分常量 (基于 113 只历史翻倍股特征)
# ============================================================
PRICE_SCORE = {"ultra_low": 30, "low": 25, "mid": 20, "mid_high": 10, "high": 5}
HOT_INDUSTRIES = [
    "软件服务", "元器件", "电气设备", "专用机械", "化工原料",
    "机械基件", "半导体", "化学制药", "影视音像", "汽车配件",
    "通信设备", "生物制药", "小金属", "航空", "医疗保健",
    "建筑工程", "染料涂料", "医药商业", "机床制造", "工程机械",
    "火力发电", "环境保护",
]
MV_SCORE = {"micro": 25, "small": 20, "mid": 15, "large": 8, "xl": 3}


def _get_pro():
    import tushare as ts
    return ts.pro_api(config.TUSHARE_TOKEN)


def _price_score(close):
    if close < 10: return PRICE_SCORE["ultra_low"]
    elif close < 20: return PRICE_SCORE["low"]
    elif close < 50: return PRICE_SCORE["mid"]
    elif close < 100: return PRICE_SCORE["mid_high"]
    else: return PRICE_SCORE["high"]


def _industry_score(industry):
    if industry in HOT_INDUSTRIES[:7]: return 20
    elif industry in HOT_INDUSTRIES: return 15
    else: return 8


def _mv_score(circ_mv_yi):
    if circ_mv_yi < 20: return MV_SCORE["micro"]
    elif circ_mv_yi < 50: return MV_SCORE["small"]
    elif circ_mv_yi < 100: return MV_SCORE["mid"]
    elif circ_mv_yi < 300: return MV_SCORE["large"]
    else: return MV_SCORE["xl"]


def _momentum_score(pct_chg, vol_ratio):
    score = 0
    if pct_chg >= 9.5: score += 12
    elif pct_chg >= 5: score += 8
    elif pct_chg >= 3: score += 5
    elif pct_chg >= 0: score += 3
    if vol_ratio and vol_ratio >= 3: score += 8
    elif vol_ratio and vol_ratio >= 2: score += 5
    elif vol_ratio and vol_ratio >= 1.5: score += 3
    else: score += 1
    return score


def _turnover_score(turnover):
    if turnover is None: return 3
    if turnover >= 25: return 10
    elif turnover >= 15: return 8
    elif turnover >= 10: return 5
    elif turnover >= 5: return 3
    else: return 1


def _early_stage_score_batch(candidates: list) -> dict:
    """启动前期过滤器 (批量版) — 魔法师核心算法

    批量获取K线数据，避免对300只股票逐个调用API。
    对每只候选股评估启动阶段并返回评分。

    Args:
        candidates: recommend_current_month 的候选列表

    Returns:
        {code: {"score": int, "level": str, "reason": str, "exclude": bool}}
    """
    from collections import defaultdict
    results = defaultdict(lambda: {"score": 0, "level": "neutral", "reason": "", "exclude": False})

    if len(candidates) < 3:
        return results

    try:
        # 批量获取K线数据 (利用 realtime_scorer 的 get_kline_batch)
        from realtime_scorer import get_kline_batch
        codes = [c["code"] for c in candidates]
        klines = get_kline_batch(codes, days=22)  # 约1个月交易日

        if not klines:
            return results

        from datetime import datetime
        today = datetime.now().strftime("%Y%m%d")

        for c in candidates:
            code = c["code"]
            close = c["close"]
            vol_ratio = c.get("vol_ratio", 0)

            kline = klines.get(code)
            if kline is None or len(kline) < 3:
                continue

            # 计算本月近似涨幅
            first_close = float(kline["close"].iloc[0]) if "close" in kline.columns else close
            monthly_change = (close / first_close - 1) * 100 if first_close > 0 else 0

            result = results[code]

            # ─── 启动前期评分逻辑 ─────────────────
            if monthly_change > 80:
                result["exclude"] = True
                result["reason"] = f"月涨幅{monthly_change:.0f}% >80%"
                result["score"] = -10
                result["level"] = "excluded"
                continue

            if monthly_change > 50:
                result["score"] = -8
                result["reason"] = f"月涨幅{monthly_change:.0f}% >50%"
                result["level"] = "late"
            elif monthly_change > 25:
                result["score"] = -3
                result["reason"] = f"月涨幅{monthly_change:.0f}%"
                result["level"] = "mid_stage"
            elif monthly_change > 10:
                result["score"] = 5
                result["reason"] = f"月涨幅{monthly_change:.0f}% 温和启动"
                result["level"] = "warming"
            elif monthly_change > -5:
                result["score"] = 10
                result["reason"] = f"月涨幅{monthly_change:.1f}% 启动前期"
                result["level"] = "early"
            else:
                result["score"] = 5
                result["reason"] = f"月跌幅{abs(monthly_change):.1f}% 回调中"
                result["level"] = "pullback"

            # ─── 横盘整理检测 ─────────────────────
            if len(kline) >= 15:
                close_arr = kline["close"].values[-15:].astype(float)
                price_range = (close_arr.max() - close_arr.min()) / close_arr.mean() * 100
                if price_range < 5 and vol_ratio > 1.5:
                    result["score"] += 5
                    result["reason"] += " | 横盘放量"

            # ─── 连续涨停检测 ─────────────────────
            if len(kline) >= 3 and "pct_chg" in kline.columns:
                recent_pct = kline["pct_chg"].values[-5:].astype(float)
                consecutive_limit = 0
                for p in reversed(recent_pct):
                    if p >= 9.5:
                        consecutive_limit += 1
                    else:
                        break
                if consecutive_limit >= 3:
                    result["exclude"] = True
                    result["reason"] = f"连续涨停{consecutive_limit}天"
                    result["score"] = -10
                    result["level"] = "excluded"
                    continue
                elif consecutive_limit >= 2:
                    result["score"] -= 5
                    result["reason"] += f" | 已{consecutive_limit}连板"

    except Exception:
        pass

    return results


def _board_score(code):
    if code.startswith('6'): return 5
    elif code.startswith('3'): return 3
    elif code.startswith('0'): return 2
    elif code.startswith('9'): return 1
    else: return 0


# ============================================================
# 历史翻倍股扫描
# ============================================================
def _get_month_boundaries(start_month="202501", end_month="202606"):
    """获取每个月首尾交易日"""
    pro = _get_pro()
    cal = pro.trade_cal(start_date=f"{start_month}01", end_date=f"{end_month}02", is_open="1")
    cal = cal.sort_values("cal_date")
    dates = cal["cal_date"].tolist()
    months = {}
    for d in dates:
        m = d[:6]
        if m not in months:
            months[m] = {"first": d, "last": d}
        else:
            months[m]["last"] = d
    return months


def _get_daily_safe(trade_date):
    """安全获取某日全市场数据"""
    pro = _get_pro()
    key = f"daily_{trade_date}"
    cached = cache_or_fetch(key, None, 3600)
    if cached is not None and not isinstance(cached, (dict, str)):
        return cached
    for attempt in range(3):
        try:
            df = pro.daily(trade_date=trade_date,
                          fields="ts_code,close,pct_chg,amount,vol")
            if df is not None and not isinstance(df, dict) and len(df) > 0:
                cache_set(key, df, 3600)
                return df
        except Exception:
            time.sleep(1)
    return None


def scan_monthly_doublers():
    """扫描历史月度翻倍股（月涨幅≥100%）

    Returns:
        dict: {
            "total": int,
            "doublers": list[dict],
            "by_month": dict,
            "by_industry": dict,
            "scan_time": str
        }
    """
    from data.fetcher import get_stock_basic

    months = _get_month_boundaries()
    stock_info = get_stock_basic()
    all_doublers = []

    for month_key, bounds in sorted(months.items()):
        first_date = bounds["first"]
        last_date = bounds["last"]
        if first_date == last_date:
            continue

        df_first = _get_daily_safe(first_date)
        # P0-1 bugfix: 首日数据不完整时fallback到后续交易日
        # 例如2025-01-02(元旦后)仅3条记录，需跳到2025-01-03
        fallback_attempts = 0
        while df_first is not None and len(df_first) < 500 and fallback_attempts < 5:
            from datetime import datetime as _dt, timedelta as _td
            next_d = _dt.strptime(first_date, "%Y%m%d") + _td(days=1)
            first_date = next_d.strftime("%Y%m%d")
            if first_date > last_date:
                df_first = None
                break
            fallback_attempts += 1
            df_first = _get_daily_safe(first_date)
            print(f"    ⚠️ 首日数据不足, fallback → {first_date}")

        df_last = _get_daily_safe(last_date)
        if df_first is None or df_last is None:
            continue

        first_price = {}
        for _, r in df_first.iterrows():
            first_price[r["ts_code"]] = float(r["close"])

        for _, r in df_last.iterrows():
            code = r["ts_code"]
            close_first = first_price.get(code)
            close_last = float(r["close"])
            if close_first and close_first > 0:
                ret = (close_last / close_first - 1) * 100
                if ret >= 100:
                    info = stock_info.get(code, {})
                    short = code.replace(".SZ","").replace(".SH","").replace(".BJ","")
                    all_doublers.append({
                        "month": month_key,
                        "code": short,
                        "ts_code": code,
                        "name": info.get("name", "?"),
                        "industry": info.get("industry", "未知"),
                        "first_close": round(close_first, 2),
                        "last_close": round(close_last, 2),
                        "return_pct": round(ret, 1),
                    })

    all_doublers.sort(key=lambda x: -x["return_pct"])

    df = pd.DataFrame(all_doublers) if all_doublers else pd.DataFrame()
    by_month = df.groupby("month").size().to_dict() if len(df) > 0 else {}
    by_industry = df["industry"].value_counts().head(15).to_dict() if len(df) > 0 else {}

    return {
        "total": len(all_doublers),
        "doublers": all_doublers,
        "by_month": {k: int(v) for k, v in by_month.items()},
        "by_industry": {k: int(v) for k, v in by_industry.items()},
        "scan_time": datetime.now().isoformat(),
    }


# ============================================================
# 当月推荐
# ============================================================
def _compute_enhanced_d9(c, pre_surge):
    """增强D9: 基础量价 + 龙虎榜 + 资金流"""
    score = 0
    if c["pct_chg"] >= 5: score += 2
    elif c["pct_chg"] >= 3: score += 1
    if c["vol_ratio"] >= 3: score += 2
    elif c["vol_ratio"] >= 2: score += 1
    if c["turnover"] >= 25: score += 2
    elif c["turnover"] >= 15: score += 1
    if c["circ_mv_yi"] < 20: score += 1
    if c["circ_mv_yi"] < 10: score += 1
    # 龙虎榜信号
    dt = pre_surge.get("dragon_tiger", {}).get(c["code"], 0)
    score += dt
    # 资金流信号
    mf = pre_surge.get("moneyflow", {}).get(c["code"], 0)
    score += mf
    return min(score, 10)


def recommend_current_month():
    """基于多维度评分模型推荐当月翻倍潜力股

    Returns:
        dict: {
            "trade_date": str,
            "top30": list[dict],
            "elite_picks": list[dict],
            "industry_heat": dict,
            "scan_time": str,
        }
    """
    from data.fetcher import get_daily, get_stock_basic, get_latest_date

    pro = _get_pro()
    today = get_latest_date()

    daily = get_daily()
    if daily is None or isinstance(daily, (dict, str)) or len(daily) == 0:
        return {"error": "daily data unavailable", "trade_date": today}

    basic = get_stock_basic()

    # 量能数据
    basic_df = None
    try:
        basic_df = pro.daily_basic(trade_date=today,
                                   fields="ts_code,total_mv,circ_mv,turnover_rate,volume_ratio")
    except Exception:
        pass

    basic_map = {}
    if basic_df is not None and len(basic_df) > 0:
        for _, r in basic_df.iterrows():
            basic_map[r["ts_code"]] = r

    info_map = basic if isinstance(basic, dict) else {}

    candidates = []
    for _, row in daily.iterrows():
        code = row["ts_code"]
        close = float(row["close"])
        pct_chg = float(row["pct_chg"])
        vol = float(row["vol"])
        amount = float(row["amount"])

        if "ST" in code or close <= 0 or vol <= 0:
            continue

        info = info_map.get(code, {})
        b_row = basic_map.get(code, None)

        circ_mv = float(b_row["circ_mv"]) / 1e4 if b_row is not None and b_row["circ_mv"] is not None else 0
        circ_mv = 0 if (circ_mv != circ_mv) else circ_mv
        total_mv = float(b_row["total_mv"]) / 1e4 if b_row is not None and b_row["total_mv"] is not None else 0
        total_mv = 0 if (total_mv != total_mv) else total_mv
        turnover = float(b_row["turnover_rate"]) if b_row is not None and b_row["turnover_rate"] is not None else 0
        turnover = 0 if (turnover != turnover) else turnover
        vol_ratio = float(b_row["volume_ratio"]) if b_row is not None and b_row["volume_ratio"] is not None else 0
        vol_ratio = 0 if (vol_ratio != vol_ratio) else vol_ratio  # NaN → 0

        short = code.replace(".SZ","").replace(".SH","").replace(".BJ","")

        ps = _price_score(close)
        ind_s = _industry_score(info.get("industry", "未知"))
        mv_s = _mv_score(circ_mv) if circ_mv > 0 else 5
        mom_s = _momentum_score(pct_chg, vol_ratio)
        turn_s = _turnover_score(turnover)
        board_s = _board_score(short)

        total = ps + ind_s + mv_s + mom_s + turn_s + board_s

        candidates.append({
            "code": short, "ts_code": code,
            "name": info.get("name", "?"),
            "industry": info.get("industry", "未知"),
            "close": round(close, 2),
            "pct_chg": round(pct_chg, 2),
            "circ_mv_yi": round(circ_mv, 2),
            "total_mv_yi": round(total_mv, 2),
            "turnover": round(turnover, 2),
            "vol_ratio": round(vol_ratio, 2) if vol_ratio else 0,
            "amount_yi": round(amount / 1e5, 2),
            "base_score": total,
            "score": total,  # 初始=base，后续叠加catalyst
            "score_breakdown": {
                "price": ps, "industry": ind_s, "market_cap": mv_s,
                "momentum": mom_s, "turnover": turn_s, "board": board_s,
            }
        })

    candidates.sort(key=lambda x: -x["base_score"])
    base_top300 = candidates[:300]

    # ═══════════════════════════════════════════
    # D7/D8: 综合催化剂 (C1政策 + C2商品 + C3合同 + C5重组 + C6概念 + C7业绩)
    # ═══════════════════════════════════════════
    catalyst_scores = {}
    pre_surge = {"dragon_tiger": {}, "moneyflow": {}}
    try:
        from services.catalyst_engine import scan_all_catalysts
        from data.pre_surge_signals import fetch_pre_surge_signals
        ym = today[:6]
        cat_result = scan_all_catalysts(ym, trade_date=f"{today[:4]}-{today[4:6]}-{today[6:]}")
        catalyst_scores = cat_result.get("stock_scores", {})
        pre_surge = fetch_pre_surge_signals()
    except Exception as e:
        print(f"[doubler] catalyst skipped (ok): {e}")

    # D0 启动前期过滤器 (批量版, 魔法师核心: 排除已涨过高的标的)
    early_stages = _early_stage_score_batch(base_top300)

    # 合并10维总分 + 启动前期过滤
    filtered_candidates = []
    for c in base_top300:
        short = c["code"]
        cat = catalyst_scores.get(short, {})
        d7 = cat.get("d7", 0)
        d8 = cat.get("d8", 0)
        cat_type = cat.get("top_event_type", "")
        resonance = cat.get("resonance", False)

        # D9 前置信号 (增强: 基础+龙虎榜+资金流)
        d9 = _compute_enhanced_d9(c, pre_surge)

        # D10 共振
        d10 = 2 if resonance else 0

        # D0 启动前期评分
        stage = early_stages.get(short, {"score": 0, "level": "neutral", "reason": "", "exclude": False})
        d0 = stage["score"]

        # 强制排除: 月涨幅 >80% 或 连续涨停 ≥3天
        if stage.get("exclude"):
            continue

        c["score"] = c["base_score"] + d0 + d7 + d8 + d9 + d10
        c["catalyst"] = {"d0_early_stage": d0, "d7": d7, "d8": d8, "d9": d9, "d10": d10,
                        "cat_type": cat_type, "resonance": resonance,
                        "early_stage": stage["level"], "early_reason": stage["reason"]}
        c["score_breakdown"]["early_stage_d0"] = d0
        c["score_breakdown"]["catalyst_d7"] = d7
        c["score_breakdown"]["catalyst_d8"] = d8
        c["score_breakdown"]["pre_surge_d9"] = d9
        c["score_breakdown"]["resonance_d10"] = d10
        filtered_candidates.append(c)

    candidates = filtered_candidates

    candidates.sort(key=lambda x: -x["score"])
    top30 = candidates[:30]

    # 行业热度
    industry_heat = defaultdict(lambda: {"count": 0, "top_stocks": []})
    for c in top30:
        ind = c["industry"]
        industry_heat[ind]["count"] += 1
        if len(industry_heat[ind]["top_stocks"]) < 2:
            industry_heat[ind]["top_stocks"].append(
                f"{c['code']} {c['name']} {c['score']}分")

    # 核心推荐 (过滤ST)
    clean = [c for c in top30 if "ST" not in c["name"]]
    elite = [c for c in clean if c["score"] >= 70][:10]
    if len(elite) < 5:
        elite = clean[:8]

    return {
        "trade_date": today,
        "top30": top30,
        "elite_picks": elite,
        "industry_heat": {ind: {"count": d["count"], "stocks": d["top_stocks"]}
                         for ind, d in sorted(industry_heat.items(),
                                             key=lambda x: -x[1]["count"])[:10]},
        "scan_time": datetime.now().isoformat(),
    }


# ============================================================
# 持仓方案 (1W 启动资金)
# ============================================================
def position_plan_10k(elite_picks):
    """基于推荐池生成 1W 仓位方案

    Args:
        elite_picks: recommend_current_month() 返回的 elite_picks

    Returns:
        dict: {plan: list, total_allocated, remaining, rules}
    """
    capital = 10000
    # 过滤买得起的 (单价 ≤ ¥25 才能买至少100股)
    affordable = [c for c in elite_picks if c["close"] <= 25]
    if len(affordable) < 3:
        affordable = elite_picks[:3]

    plan = []
    total_cost = 0
    allocs = [4000, 3000, 3000]

    for i, c in enumerate(affordable[:3]):
        alloc = allocs[i]
        shares = int(alloc / c["close"] / 100) * 100
        actual = shares * c["close"]
        total_cost += actual
        cat_type = c.get('catalyst', {}).get('cat_type', c.get('catalyst_type', '?'))
        plan.append({
            "rank": i + 1,
            "code": c["code"],
            "name": c["name"],
            "price": c["close"],
            "shares": shares,
            "cost": round(actual, 2),
            "pct": f"{actual/capital*100:.1f}%",
            "reason": (f"评分{c.get('score', c.get('potential', 0))}分"
                       f" | 流通{c.get('circ_mv_yi',0):.1f}亿"
                       f" | 催化{cat_type}"),
        })

    return {
        "plan": plan,
        "total_allocated": round(total_cost, 2),
        "allocated_pct": f"{total_cost/capital*100:.0f}%",
        "remaining": round(capital - total_cost, 2),
        "remaining_pct": f"{(capital - total_cost)/capital*100:.0f}%",
        "watchlist": affordable[3:6],
        "rules": {
            "max_loss_per_stock": "8%",
            "max_drawdown_total": "15% (¥1,500)",
            "profit_protection": [
                "+20%: 减仓30%, 止损→成本价",
                "+40%: 减仓50%, 止损→+10%",
                "+80%: 清仓80%, 留20%看翻倍",
            ],
            "stop_loss": [
                "3日未涨反跌>5%: 止损",
                "涨停次日低开>3%: 集合竞价止损",
                "单日跌幅>7%: 无条件止损",
            ]
        }
    }
