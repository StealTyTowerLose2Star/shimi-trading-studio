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
    """动量评分 (V2 魔法师修订版)
    
    核心理念变更：翻倍股的关键不在「今天涨了多少」，
    而在「启动前的蓄力信号 + 催化剂」。
    因此大幅降低当日涨幅的权重，把分数空间留给 D0(启动前期)和 D7-D10(催化剂)。
    
    原版: pct_chg≥5=+8分, vol_ratio≥3=+8分, 满分20
    新版: pct_chg≥5=+4分, vol_ratio≥3=+4分, 满分10
    """
    score = 0
    # 当日涨幅 — 权重减半 (原8→4, 原5→2)
    if pct_chg >= 9.5: score += 5
    elif pct_chg >= 5: score += 3
    elif pct_chg >= 3: score += 2
    elif pct_chg >= 0: score += 1
    # 量比 — 权重减半 (原8→4, 原5→2)
    if vol_ratio and vol_ratio >= 3: score += 5
    elif vol_ratio and vol_ratio >= 2: score += 3
    elif vol_ratio and vol_ratio >= 1.5: score += 2
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
    """启动前期多模式检测器 (V2 魔法师核心) — 批量版

    基于113只历史翻倍股回测的5种启动前模式：

    Pattern A "Coiled Spring"   — 压缩+缩量+横盘 → +15 (最优)
    Pattern B "Silent Accumulation" — 横盘+ATR收缩+缩量 → +12
    Pattern C "Early Warming"    — 温和启动(5-15%)+放量 → +8
    Pattern D "Sector Rotation"  — 热门行业+个股滞涨 → +5
    Pattern E "Bottoming Out"    — 回调触底+缩量 → +3

    强排除: 月涨>80% | 连续涨停≥3天 | ST股

    Returns: {code: {score, level, reason, exclude, pattern}}
    """
    from collections import defaultdict
    results = defaultdict(lambda: {
        "score": 0, "level": "neutral", "reason": "", "exclude": False, "pattern": ""
    })

    if len(candidates) < 3:
        return results

    try:
        from realtime_scorer import get_kline_batch
        codes = [c["code"] for c in candidates]
        klines = get_kline_batch(codes, days=35)

        if not klines:
            return results

        import numpy as np

        for c in candidates:
            code = c["code"]
            close = c["close"]
            vol_ratio = c.get("vol_ratio", 0) or 0
            industry = c.get("industry", "未知")
            name = c.get("name", "")

            # ─── ST股排除 ───
            if "ST" in name or "*ST" in name:
                result = results[code]
                result["exclude"] = True
                result["score"] = -15
                result["level"] = "excluded"
                result["reason"] = "ST股排除"
                continue

            kline = klines.get(code)
            if kline is None or len(kline) < 5:
                continue

            result = results[code]
            nk = len(kline)
            half = max(nk // 2, 3)

            # ─── 提取基础特征 ───
            closes = kline["close"].values.astype(float)[-nk:]
            vols = kline["volume"].values.astype(float)[-nk:]
            highs = kline["high"].values.astype(float)[-nk:]
            lows = kline["low"].values.astype(float)[-nk:]
            pct_col = "pct_chg" if "pct_chg" in kline.columns else None

            c_min, c_max = closes.min(), closes.max()

            # 月涨幅
            monthly_change = (close / closes[0] - 1) * 100 if closes[0] > 0 else 0

            # Price compression: 后段波幅 / 前段波幅
            range_first = (closes[:half].max() - closes[:half].min()) / closes[:half].mean() * 100 if closes[:half].mean() > 0 else 0
            range_second = (closes[-half:].max() - closes[-half:].min()) / closes[-half:].mean() * 100 if closes[-half:].mean() > 0 else 0
            compression = range_second / range_first if range_first > 0 else 1.0

            # Volume pattern: 中段/前段 (dry-up), 末段/中段 (expansion)
            seg = max(nk // 5, 1)
            vol_first = vols[:seg].mean()
            vol_mid = vols[nk//3:2*nk//3].mean()
            vol_last = vols[-seg:].mean()
            vol_dry_up = vol_mid / vol_first if vol_first > 0 else 1.0
            vol_expand = vol_last / vol_mid if vol_mid > 0 else 1.0

            # ATR contraction
            trs = []
            for j in range(1, nk):
                tr = max(highs[j] - lows[j], abs(highs[j] - closes[j-1]), abs(lows[j] - closes[j-1]))
                trs.append(tr)
            atr_first = np.mean(trs[:half]) / closes[:half].mean() * 100 if closes[:half].mean() > 0 else 0
            atr_second = np.mean(trs[-half:]) / closes[-half:].mean() * 100 if closes[-half:].mean() > 0 else 0
            atr_contract = atr_second / atr_first if atr_first > 0 else 1.0

            # Price position
            price_pos = (close - c_min) / (c_max - c_min) * 100 if c_max > c_min else 50

            # Last 3-day return
            last3 = min(3, nk)
            last3_ret = (closes[-1] / closes[-last3] - 1) * 100 if nk >= last3 else 0

            # ─── 强排除规则 ───
            if monthly_change > 80:
                result["exclude"] = True
                result["score"] = -10
                result["level"] = "excluded"
                result["reason"] = f"月涨幅{monthly_change:.0f}% >80%"
                continue

            consecutive_limit = 0
            if pct_col:
                recent_pct = kline[pct_col].values[-5:].astype(float)
                for p in reversed(recent_pct):
                    if p >= 9.5:
                        consecutive_limit += 1
                    else:
                        break
                if consecutive_limit >= 3:
                    result["exclude"] = True
                    result["score"] = -10
                    result["level"] = "excluded"
                    result["reason"] = f"连续涨停{consecutive_limit}天"
                    continue

            # ─── 趋势过滤器 (排除单边下行) ───
            trend_score = 0
            if nk >= 20:
                import numpy as np
                ma20 = np.mean(closes[-20:])
                trend_20d = (closes[-1] / closes[-min(20, nk)] - 1) * 100
                trend_10d = (closes[-1] / closes[-min(10, nk)] - 1) * 100
                below_ma20 = closes[-1] < ma20 * 0.98
                down_days = sum(1 for i in range(1, min(5, nk)) if closes[-i] < closes[-i-1])
                
                if below_ma20 and trend_20d < -15:
                    result["exclude"] = True
                    result["score"] = -15
                    result["level"] = "excluded"
                    result["reason"] = f"单边下行{trend_20d:.0f}%"
                    result["pattern"] = "单边下行"
                    result["mtm_gain"] = round(monthly_change, 1)
                    continue
                elif below_ma20 and trend_10d < -8:
                    trend_score = -10
                    reasons_extra = [f"弱势{trend_10d:.0f}%"]
                elif below_ma20 and down_days >= 4:
                    trend_score = -8
                    reasons_extra = ["连阴下行"]
                else:
                    reasons_extra = []
            else:
                reasons_extra = []

            # ─── 多模式评分 ───
            score = 0
            reasons = []

            # Pattern A: 弹簧蓄力 (压缩 + 缩量 + 横盘)
            if compression < 1.2 and vol_dry_up < 1.1 and -3 <= monthly_change <= 10:
                score += 15
                pattern = "弹簧蓄力"
                reasons.append("弹簧蓄力")
            # Pattern B: 静默吸筹 (横盘 + ATR收缩)
            elif -5 <= monthly_change <= 10 and atr_contract < 0.9 and vol_dry_up < 0.95:
                score += 12
                pattern = "静默吸筹"
                reasons.append("静默吸筹")
            # Pattern C: 放量启动 (5-20% + 放量)
            elif 5 <= monthly_change <= 20 and vol_expand > 1.2 and consecutive_limit < 2:
                score += 8
                pattern = "放量启动"
                reasons.append("放量启动")
            # Pattern D: 板块轮动 (热门行业 + 个股滞涨)
            elif industry in HOT_INDUSTRIES and -3 <= monthly_change <= 5 and price_pos < 55:
                score += 5
                pattern = "板块轮动"
                reasons.append(f"板块轮动({industry})")
            # ─── Default: monthly change + pullback classification ───
            else:
                if monthly_change > 50:
                    # V3: 直接排除而非仅减分
                    result["exclude"] = True
                    result["score"] = -15
                    result["level"] = "excluded"
                    result["reason"] = f"涨幅过高{monthly_change:.0f}% >50%"
                    result["pattern"] = "排除-涨幅过高"
                    continue
                elif monthly_change > 25:
                    score += -5
                    pattern = "涨幅已大"
                    reasons.append(f"月涨{monthly_change:.0f}%")
                elif monthly_change > 10:
                    score += 5
                    pattern = "上涨中"
                    reasons.append(f"月涨{monthly_change:.0f}%")
                elif monthly_change > -5:
                    score += 8
                    pattern = "横盘蓄势"
                    reasons.append(f"横盘{monthly_change:.1f}%")
                else:
                    # 回调精细化: 缩量到底 vs 放量下跌
                    if price_pos < 30 and vol_dry_up < 0.9:
                        score += 8
                        pattern = "缩量回调"
                        reasons.append(f"缩量回调{abs(monthly_change):.1f}%")
                    elif vol_expand > 1.5 and monthly_change < -8:
                        score += -3
                        pattern = "放量下跌⚠️"
                        reasons.append(f"放量下跌{abs(monthly_change):.1f}%")
                    else:
                        score += 3
                        pattern = "回调中"
                        reasons.append(f"回调{abs(monthly_change):.1f}%")

            # ─── 额外加权信号 ───
            # 横盘放量 (15日内振幅<5% + 量比>1.5)
            if nk >= 15:
                c15 = closes[-15:]
                range15 = (c15.max() - c15.min()) / c15.mean() * 100 if c15.mean() > 0 else 0
                if range15 < 5 and vol_ratio > 1.5:
                    score += 4
                    reasons.append("横盘放量")

            # 价格低位 + 缩量 (超卖反弹潜力)
            if price_pos < 25 and vol_dry_up < 0.8:
                score += 3
                reasons.append("低位缩量")

            # 连板减分
            if pct_col:
                if consecutive_limit >= 2:
                    score -= 6
                    reasons.append(f"已{consecutive_limit}连板")

            # ─── 趋势惩罚 ───
            score += trend_score
            if reasons_extra:
                reasons.extend(reasons_extra)
                # Override pattern if trend penalty dominates
                if trend_score <= -10:
                    pattern = "弱势下行"
                elif trend_score <= -8:
                    pattern = "连阴下行"

            result["score"] = score
            result["level"] = pattern or "neutral"
            result["reason"] = " | ".join(reasons) if reasons else "无特殊信号"
            result["pattern"] = pattern
            result["mtm_gain"] = round(monthly_change, 1)

            # ─── MA均线收敛检测 (额外加分, 需上行斜率) ───
            # 检测 MA5/MA20 是否粘合且 MA5>MA20 (上行趋势)
            if nk >= 20:
                try:
                    import numpy as np
                    ma20_val = np.mean(closes[-min(20, nk):])
                    ma5_val = np.mean(closes[-min(5, nk):])
                    ma_gap = abs(ma5_val - ma20_val) / max(ma20_val, 0.01) * 100
                    # 斜率: MA20 10日前vs当前
                    ma20_10d = np.mean(closes[-min(30, nk):-min(20, nk)]) if nk >= 30 else ma20_val
                    slope_up = ma5_val > ma20_val and ma20_val > ma20_10d  # 均线向上发散
                    if ma_gap < 2.5 and slope_up:
                        result["score"] += 5
                        result["reason"] = result.get("reason","") + " | MA粘合↑"
                except Exception:
                    pass

    except Exception as e:
        import sys
        print(f"[doubler] _early_stage_score_batch failed: {e}", file=sys.stderr)
        # Fallback: use pct_chg as mtm_gain for all candidates
        for c in candidates:
            code = c["code"]
            results[code] = {
                "score": 0, "level": "neutral",
                "reason": "K线数据不可用", "exclude": False,
                "pattern": "", "mtm_gain": round(c.get("pct_chg", 0), 1)
            }

    return results


def _board_score(code):
    if code.startswith('6'): return 5
    elif code.startswith('3'): return 3
    elif code.startswith('0'): return 2
    elif code.startswith('9'): return 1
    else: return 0


def _liquidity_penalty(code, circ_mv_yi, turnover):
    """流动性惩罚 — 针对北交所/微盘股流动性不足的问题
    
    北交所小票天然低价+小市值 → 基础分虚高。
    引入流动性维度让真正可交易的标的浮上来。
    
    Returns: 负分 (惩罚)
    """
    penalty = 0
    # 北交所惩罚: 流动性不足的扣分
    if code.startswith('9'):
        if turnover is None or turnover < 3:
            penalty = -15  # 北交所僵尸股
        elif turnover < 5:
            penalty = -10
        elif turnover < 8:
            penalty = -5
    # 微盘股(<3亿) + 换手率<5%: 流动性陷阱
    if circ_mv_yi < 3 and (turnover is None or turnover < 5):
        penalty = max(penalty, -12)
    return penalty


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
    # 兜底: today为None或异常时用最新交易日
    if not today or not isinstance(today, str) or len(today) < 8:
        try:
            from datetime import datetime as _dt
            today = _dt.now().strftime("%Y%m%d")
        except Exception:
            today = datetime.now().strftime("%Y%m%d")
    today = str(today)[:8]  # 保证是8位日期字符串

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
        # 跳过北交所 (用户无交易权限)
        if code.endswith(".BJ"):
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

        # V4: 流动性惩罚 — 北交所僵尸股和微盘流动性陷阱扣分
        liq_penalty = _liquidity_penalty(short, circ_mv, turnover)
        total += liq_penalty

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
                        "early_stage": stage["level"], "early_reason": stage["reason"],
                        "early_pattern": stage.get("pattern", "")}
        c["score_breakdown"]["early_stage_d0"] = d0
        c["score_breakdown"]["catalyst_d7"] = d7
        c["score_breakdown"]["catalyst_d8"] = d8
        c["score_breakdown"]["pre_surge_d9"] = d9
        c["score_breakdown"]["resonance_d10"] = d10
        # 前端直接用的字段
        c["mtm_gain"] = stage.get("mtm_gain", round(c.get("pct_chg", 0), 1))
        # 翻倍潜力 = (催化剂+前期信号) * 小盘弹性
        size_boost = 1.5 if c.get("circ_mv_yi", 100) < 10 else (1.3 if c.get("circ_mv_yi", 100) < 20 else 1.0)
        c["potential"] = round((d0 + d7 + d8 + d9 + d10) * size_boost, 1)
        # 催化剂摘要 (前端显示用)
        c["catalyst_type"] = cat_type
        c["catalyst_d7"] = d7
        filtered_candidates.append(c)

    candidates = filtered_candidates

    # ═══════════════════════════════════════════
    # D11: MA均线收敛检测 (5日/20日/60日/120日粘合)
    # ═══════════════════════════════════════════
    try:
        from realtime_scorer import get_kline_batch
        top50 = candidates[:50]
        deep_codes = [c["code"] for c in top50]
        deep_klines = get_kline_batch(deep_codes, days=150)

        if deep_klines:
            import numpy as np
            for c in top50:
                kline = deep_klines.get(c["code"])
                if kline is None or len(kline) < 60:
                    continue
                closes = kline["close"].values.astype(float)
                nk = len(closes)

                # 计算4条均线
                ma5 = np.mean(closes[-min(5, nk):])
                ma20 = np.mean(closes[-min(20, nk):])
                ma60 = np.mean(closes[-min(60, nk):]) if nk >= 60 else None
                ma120 = np.mean(closes[-min(120, nk):]) if nk >= 120 else None

                # 均线粘合度: 最大均线差 / 当前价 < 5% 即为粘合
                mas = [ma5, ma20] + ([ma60] if ma60 else []) + ([ma120] if ma120 else [])
                ma_range = (max(mas) - min(mas)) / ma20 * 100 if ma20 > 0 else 100

                # 均线斜率: MA20 10日前 vs 当前 (必须>0才加分)
                ma20_10d_ago = np.mean(closes[-min(30, nk):-min(20, nk)]) if nk >= 30 else ma20
                ma_slope = (ma20 - ma20_10d_ago) / ma20_10d_ago * 100 if ma20_10d_ago > 0 else -1

                bonus = 0
                if ma_slope <= 0:
                    bonus = 0  # 均线下行, 粘合不加分
                elif ma_range < 2 and len(mas) >= 3:
                    bonus = 12  # 多均线高度粘合+上行
                elif ma_range < 3 and len(mas) >= 3:
                    bonus = 8
                elif ma_range < 4:
                    bonus = 5
                elif ma_range < 5:
                    bonus = 3

                if bonus > 0:
                    c["score"] += bonus
                    c["score_breakdown"]["ma_convergence"] = bonus
                    c["catalyst"]["ma_bonus"] = bonus
                    c["catalyst"]["ma_range"] = round(ma_range, 1)
                    c["catalyst"]["ma_slope"] = round(ma_slope, 1)
    except Exception:
        pass

    candidates.sort(key=lambda x: -x["score"])
    top30 = candidates[:30]

    # V5: 过滤北交所 (用户无北证交易权限)
    top30 = [c for c in top30 if not c["code"].startswith("92")]
    # 补位到30只
    extra = [c for c in candidates if c not in top30 and not c["code"].startswith("92")]
    top30 = (top30 + extra)[:30]

    # 行业热度
    industry_heat = defaultdict(lambda: {"count": 0, "top_stocks": []})
    for c in top30:
        ind = c.get("industry") or "未知"  # 行业为空→"未知"
        industry_heat[ind]["count"] += 1
        if len(industry_heat[ind]["top_stocks"]) < 2:
            industry_heat[ind]["top_stocks"].append(
                f"{c['code']} {c['name']} {c['score']}分")

    # 核心推荐 (过滤ST)
    clean = [c for c in top30 if "ST" not in c["name"]]
    elite = [c for c in clean if c["score"] >= 70][:10]
    if len(elite) < 5:
        elite = clean[:8]

    # ─── 月度锁定 + 版本保存 + 时间窗口 ───
    now = datetime.now()
    month_key = now.strftime("%Y%m")  # 202606
    version_path = os.path.join(os.path.dirname(__file__), "..", f"picks_{month_key}.json")
    current_path = os.path.join(os.path.dirname(__file__), "..", "current_month_picks_v2.json")

    # 检测本月是否已锁定
    locked = False
    if os.path.exists(current_path):
        try:
            with open(current_path) as f:
                prev = json.load(f)
            prev_month = prev.get("month_key", "")
            if prev_month == month_key and prev.get("locked"):
                locked = True
                top30 = prev["top30"]
                elite = prev.get("elite_picks", top30[:10])
        except Exception:
            pass

    # 时间窗口预估 (基于113只历史翻倍股: 涨幅中位数119%, 月内完成)
    time_window = {
        "typical": "20±5个交易日",
        "early": "0-10天(启动前)→观察期",
        "acceleration": "11-20天(加速)→第一目标+40%",
        "doubling": "15-25天→翻倍位",
        "note": "历史翻倍股均在月内完成, 首月未达目标则重新评估",
    }

    result = {
        "month_key": month_key,
        "locked": locked,
        "trade_date": today,
        "top30": top30,
        "elite_picks": elite,
        "industry_heat": {ind: {"count": d["count"], "stocks": d["top_stocks"]}
                         for ind, d in sorted(industry_heat.items(),
                                             key=lambda x: -x[1]["count"])[:10]},
        "scan_time": datetime.now().isoformat(),
        "time_window": time_window,
    }

    # 保存: 版本文件 (每月一份) + 当前文件 (锁定用)
    for path in [version_path, current_path]:
        try:
            with open(path, "w") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # 魔法师 → 拾米A股: 自动推送
    push_doubler_picks_to_alerts({
        "trade_date": today,
        "top30": top30,
        "scan_time": datetime.now().isoformat(),
    })

    # 首次锁定时自动生成1W方案
    if not locked and len(elite) >= 3:
        try:
            from services.plan_1w import create_plan
            plan_date = now.strftime("%Y-%m-%d")
            # 展平catalyst字段
            for c in elite:
                cat = c.get("catalyst", {})
                c["early_pattern"] = cat.get("early_pattern", "")
                c["early_reason"] = cat.get("early_reason", "")
            create_plan(plan_date, elite[:3], today)
        except Exception:
            pass

    return result


# ============================================================
# 魔法师 → 拾米A股 预警对接
# ============================================================
def auto_create_doubler_alert():
    """扫描完成后自动创建翻倍股预警规则 (幂等: 已存在则跳过)

    在拾米A股的预警表中创建一条 strategy_signal(doubler) 规则，
    使得哨兵的定时检查 (cron_alert_check.sh) 能自动发现魔法师的推荐。
    """
    from services.alert import list_alerts, create_alert

    # 检查是否已存在 doubler 预警规则
    for a in list_alerts():
        if a.get("type") == "strategy_signal" and a.get("params", {}).get("strategy") == "doubler":
            return a  # 已存在，跳过

    # 创建默认 doubler 预警: 评分≥80分时触发
    alert = create_alert("strategy_signal", {
        "strategy": "doubler",
        "min_score": 80,
    }, enabled=True)
    return alert


def push_doubler_picks_to_alerts(result: dict):
    """扫描完成后将 Top 推荐推送到预警消息队列

    直接写入 message_queue.json，供通讯员发送。
    同时确保预警规则存在。
    """
    try:
        import json, os
        from datetime import datetime

        # 1. 确保预警规则存在
        auto_create_doubler_alert()

        # 2. 推送 Top5 到消息队列
        top30 = result.get("top30", [])
        if not top30:
            return

        queue_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "message_queue.json"
        )
        queue = []
        if os.path.exists(queue_path):
            with open(queue_path) as f:
                queue = json.load(f)

        top5 = top30[:5]
        lines = []
        for i, c in enumerate(top5):
            cat = c.get("catalyst", {})
            lines.append(
                f"#{i+1} {c['code']} {c['name']} {c['score']}分 "
                f"({cat.get('early_pattern','?')}: {cat.get('early_reason','?')})"
            )

        queue.append({
            "source": "doubler",
            "priority": "high",
            "title": "🔮魔法师翻倍股推荐",
            "message": (
                f"交易日 {result.get('trade_date','?')} | "
                f"Top30 启动模式: "
                + " | ".join(lines)
            ),
            "data": {
                "top5": top5,
                "scan_time": result.get("scan_time", ""),
                "trade_date": result.get("trade_date", ""),
            },
            "time": datetime.now().strftime("%H:%M:%S"),
            "status": "pending",
        })

        with open(queue_path, "w") as f:
            json.dump(queue, f, indent=1, ensure_ascii=False)

    except Exception:
        pass  # 推送失败不阻塞扫描


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
    # 直接取排名前3 (不按价格过滤)
    top3 = elite_picks[:3]

    plan = []
    total_cost = 0
    allocs = [4000, 3000, 3000]

    for i, c in enumerate(top3):
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
        "watchlist": top3[3:6] if len(top3) > 3 else [],
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
