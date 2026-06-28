"""
拾米交易工作室 - 个股雷达 · 多维分析引擎
归属: 🍚 拾米 A股
契约: 所有函数签名不可变

五大分析器:
  ① price_position   — 历史价位阶段 (分位数/52周/多周期)
  ② trend             — 技术面趋势与阶段 (MA/MACD/RSI/布林/量价)
  ③ fundamental       — 基本面可靠性 (估值/盈利/成长/财务健康)
  ④ capital_flow      — 资金面 (主力/北向/融资/换手率)
  ⑤ radar_score       — 五维评分 + 综合结论
"""
import time
from typing import Dict, Any, Optional
import pandas as pd
import numpy as np


# ═══════════════════════════════════════════════
# 帮助函数
# ═══════════════════════════════════════════════

def _get_kline(code: str, days: int = 250):
    """统一 K 线获取"""
    try:
        from realtime_scorer import get_kline
        df = get_kline(code, days=days)
        if df is not None and len(df) >= 15:
            return df
    except Exception:
        pass
    return None


def _close(df):
    return df["close"].astype(float)


# ═══════════════════════════════════════════════
# ① 历史价位阶段
# ═══════════════════════════════════════════════

def analyze_price_position(code: str) -> Dict[str, Any]:
    df = _get_kline(code, days=1250)  # 5 years
    if df is None:
        return {"pct_1y": None, "pct_3y": None, "pct_5y": None,
                "high_52w": None, "low_52w": None,
                "dist_from_high": None, "dist_from_low": None,
                "zone": "数据不足", "monthly_position": "数据不足",
                "weekly_position": "数据不足", "daily_position": "数据不足"}

    close = _close(df)
    price = float(close.iloc[-1])
    n = len(close)

    def _pct(series):
        if len(series) < 10:
            return None
        return round((series < price).sum() / len(series) * 100, 1)

    pct_1y = _pct(close.iloc[-min(n, 250):])
    pct_3y = _pct(close.iloc[-min(n, 750):]) if n >= 750 else None
    pct_5y = _pct(close) if n >= 1250 else None

    w52 = close.iloc[-min(n, 250):]
    high_52w = round(float(w52.max()), 2)
    low_52w = round(float(w52.min()), 2)
    dist_high = round((price - high_52w) / high_52w * 100, 1)
    dist_low = round((price - low_52w) / low_52w * 100, 1)

    if pct_1y is not None:
        zone = "高位区⚠️" if pct_1y > 85 else ("低位区💰" if pct_1y < 15 else "中位区")
    else:
        zone = "数据不足"

    def _pos(s, price):
        if len(s) < 5:
            return "数据不足"
        pct = round((s < price).sum() / len(s) * 100, 1)
        if pct > 85:
            return "高位"
        elif pct < 15:
            return "低位"
        else:
            return "中位"

    monthly = _pos(close.iloc[-min(n, 20):], price)
    weekly = _pos(close.iloc[-min(n, 5):], price)
    daily = "高位" if price >= float(close.iloc[-3:].max()) else (
        "低位" if price <= float(close.iloc[-3:].min()) else "中位")

    return {
        "pct_1y": pct_1y, "pct_3y": pct_3y, "pct_5y": pct_5y,
        "high_52w": high_52w, "low_52w": low_52w,
        "dist_from_high": dist_high, "dist_from_low": dist_low,
        "zone": zone, "monthly_position": monthly,
        "weekly_position": weekly, "daily_position": daily,
    }


# ═══════════════════════════════════════════════
# ② 技术面趋势与阶段
# ═══════════════════════════════════════════════

def _resample_weekly(df):
    """日线→周线"""
    df = df.copy()
    date_col = "date" if "date" in df.columns else "trade_date"
    df[date_col] = pd.to_datetime(df[date_col])
    df = df.set_index(date_col)
    weekly = df.resample("W").agg({
        "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"
    }).dropna()
    return weekly.reset_index()


def _resample_monthly(df):
    """日线→月线"""
    df = df.copy()
    date_col = "date" if "date" in df.columns else "trade_date"
    df[date_col] = pd.to_datetime(df[date_col])
    df = df.set_index(date_col)
    monthly = df.resample("M").agg({
        "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"
    }).dropna()
    return monthly.reset_index()


def _analyze_single_period(df, label: str) -> Dict:
    """单周期趋势分析"""
    if df is None or len(df) < 20:
        return {"direction": "数据不足", "phase": "数据不足", "ma_arrangement": "数据不足"}

    close = df["close"].astype(float)
    ma5 = close.rolling(5).mean()
    ma10 = close.rolling(10).mean()
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()

    m5, m10, m20, m60 = ma5.iloc[-1], ma10.iloc[-1], ma20.iloc[-1], ma60.iloc[-1]
    if pd.isna(m60):
        return {"direction": "数据不足", "phase": "数据不足", "ma_arrangement": "数据不足"}

    # MA 排列
    if m5 > m10 > m20 > m60:
        arrangement = "多头排列"
    elif m5 < m10 < m20 < m60:
        arrangement = "空头排列"
    elif abs(m5 - m10) / max(m10, 0.01) < 0.01:
        arrangement = "粘合"
    else:
        arrangement = "交叉"

    # 方向: 用 MA20 斜率
    slp20 = (float(ma20.iloc[-1]) - float(ma20.iloc[-6])) / max(float(ma20.iloc[-6]), 0.01) * 100 if len(ma20) >= 7 else 0
    direction = "上升" if slp20 > 0.5 else ("下降" if slp20 < -0.5 else "横盘")

    # 阶段
    price = float(close.iloc[-1])
    high_n = float(close.iloc[-min(len(close), 20):].max())
    low_n = float(close.iloc[-min(len(close), 20):].min())
    pct = (price - low_n) / max(high_n - low_n, 0.01) * 100
    if direction == "上升":
        phase = "加速期" if pct > 80 else ("中继期" if pct > 40 else "蓄力期")
    elif direction == "下降":
        phase = "下跌中继" if pct < 20 else ("筑底期" if pct < 40 else "下跌初期")
    else:
        phase = "盘整"

    return {"direction": direction, "phase": phase, "ma_arrangement": arrangement}


def _compute_macd(close):
    """MACD 计算"""
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    diff = ema12 - ema26
    dea = diff.ewm(span=9, adjust=False).mean()
    hist = 2 * (diff - dea)
    return diff, dea, hist


def _analyze_volume_divergence(df, close):
    """6-factor volume-price divergence analysis model (PRD-019)
    
    Replaces simple single-day comparison with multi-period multi-factor analysis:
      V1 - 放量滞涨 (20pts): 5d vol/20d vol > 1.5 AND 5d return < 1%
      V2 - 放量下跌 (15pts): 5d vol/20d vol > 1.3 AND 5d return < -2%
      V3 - 价涨量缩 (25pts): 5d vol/prev 5d vol < 0.7 AND 5d return > 3%
      V4 - 价跌量缩 (10pts): 5d vol/20d vol < 0.6 AND 5d return < -3%
      V5 - OBV背离 (20pts top/15pts bottom)
      V6 - 单日异动 (10pts): vol[-1]/vol[-2] extreme AND price/vol direction mismatch
    """
    n = len(df)
    if n < 20:
        return {
            "health": "数据不足", "score": 0, "divergence_types": [],
            "details": {
                "V1_surge_no_gain": {"triggered": False, "vol_ratio": 0, "return_5d": 0, "label": ""},
                "V2_surge_drop": {"triggered": False, "vol_ratio": 0, "return_5d": 0, "label": ""},
                "V3_rising_shrink": {"triggered": False, "vol_decline": 0, "return_5d": 0, "decline_pct": 0, "label": ""},
                "V4_falling_shrink": {"triggered": False, "vol_ratio": 0, "return_5d": 0, "label": ""},
                "V5_obv": {"triggered": False, "type": None, "obv_ratio": None, "label": ""},
                "V6_single_day": {"triggered": False, "vol_change": 0, "price_up": False, "vol_up": False, "label": ""},
            },
            "vs_20d_avg": 1.0, "trend": "数据不足",
        }

    vol = df["volume"].astype(float)

    # Backward-compat: single-day vs 20d moving average
    vol_ma20 = vol.rolling(20).mean()
    vs_avg = round(float(vol.iloc[-1] / max(vol_ma20.iloc[-1], 1)), 2)
    vol_trend = "放量" if vs_avg > 1.5 else ("缩量" if vs_avg < 0.5 else "持平")

    # 5-day volume averages
    vol_5d = float(vol.iloc[-5:].mean())
    vol_20d = float(vol.iloc[-20:].mean())
    vol_prev5 = float(vol.iloc[-10:-5].mean()) if n >= 10 else vol_5d

    # 5-day return (%)
    return_5d = round(float((close.iloc[-1] - close.iloc[-6]) / max(abs(close.iloc[-6]), 0.01) * 100), 2) if n >= 6 else 0

    # Single-day direction
    price_up = float(close.iloc[-1]) > float(close.iloc[-2])
    vol_up = float(vol.iloc[-1]) > float(vol.iloc[-2])

    # ── V1: 放量滞涨 (20pts) ──
    v1_vol_ratio = round(vol_5d / max(vol_20d, 1), 2)
    v1_triggered = v1_vol_ratio > 1.5 and return_5d < 1
    v1_label = f"放量滞涨: 量比{v1_vol_ratio}, 5日涨幅仅{return_5d}%" if v1_triggered else ""

    # ── V2: 放量下跌 (15pts) ──
    v2_vol_ratio = round(vol_5d / max(vol_20d, 1), 2)
    v2_triggered = v2_vol_ratio > 1.3 and return_5d < -2
    v2_label = f"放量下跌: 量比{v2_vol_ratio}, 5日跌幅{abs(return_5d)}%" if v2_triggered else ""

    # ── V3: 价涨量缩 (25pts) ──
    v3_vol_decline = round(vol_5d / max(vol_prev5, 1), 2)
    v3_decline_pct = round((1 - v3_vol_decline) * 100)
    v3_triggered = v3_vol_decline < 0.7 and return_5d > 3
    v3_label = f"价涨量缩: 量缩{v3_decline_pct}%, 5日涨幅{return_5d}%" if v3_triggered else ""

    # ── V4: 价跌量缩 (10pts) ──
    v4_vol_ratio = round(vol_5d / max(vol_20d, 1), 2)
    v4_triggered = v4_vol_ratio < 0.6 and return_5d < -3
    v4_label = f"价跌量缩: 量比{v4_vol_ratio}, 5日跌幅{abs(return_5d)}%" if v4_triggered else ""

    # ── V5: OBV背离 (20pts top / 15pts bottom) ──
    v5_triggered = False
    v5_type = None
    v5_obv_ratio = None
    v5_label = ""
    v5_score = 0
    try:
        price_changes = close.diff()
        obv = (vol * np.sign(price_changes.fillna(0))).cumsum()
        if len(obv) >= 10:
            current_obv = float(obv.iloc[-1])
            obv_max10 = float(obv.iloc[-10:].max())
            obv_min10 = float(obv.iloc[-10:].min())
            close_max10 = float(close.iloc[-10:].max())
            close_min10 = float(close.iloc[-10:].min())

            # Top divergence: price at/near 10-day high but OBV not confirming
            if float(close.iloc[-1]) >= close_max10 and obv_max10 > 0:
                obv_ratio_v5 = round(current_obv / max(obv_max10, 1), 2)
                if obv_ratio_v5 < 0.9:
                    v5_triggered = True
                    v5_type = "top"
                    v5_obv_ratio = obv_ratio_v5
                    v5_score = 20
                    v5_label = f"OBV顶背离: 价格高位但OBV仅峰值的{int(obv_ratio_v5 * 100)}%"

            # Bottom divergence: price at/near 10-day low but OBV not confirming
            if not v5_triggered and float(close.iloc[-1]) <= close_min10 and obv_min10 > 0:
                obv_ratio_v5 = round(current_obv / max(obv_min10, 1), 2)
                if obv_ratio_v5 > 1.1:
                    v5_triggered = True
                    v5_type = "bottom"
                    v5_obv_ratio = obv_ratio_v5
                    v5_score = 15
                    v5_label = f"OBV底背离: 价格低位但OBV为谷值的{int(obv_ratio_v5 * 100)}%"
    except Exception:
        pass

    # ── V6: 单日异动 (10pts) ──
    v6_vol_change = round(float(vol.iloc[-1] / max(vol.iloc[-2], 1)), 2)
    v6_triggered = (v6_vol_change > 2.0 or v6_vol_change < 0.5) and (price_up != vol_up)
    v6_label = ""
    if v6_triggered:
        if v6_vol_change > 2.0:
            v6_label = f"单日异动: 量变{v6_vol_change}倍, 价{'涨' if price_up else '跌'}量{'涨' if vol_up else '跌'}"
        else:
            v6_label = f"单日异动: 量缩至{v6_vol_change}倍"

    # ── Scoring & Classification ──
    score = 0
    divergence_types = []
    if v1_triggered:
        score += 20
        divergence_types.append("放量滞涨")
    if v2_triggered:
        score += 15
        divergence_types.append("放量下跌")
    if v3_triggered:
        score += 25
        divergence_types.append("价涨量缩")
    if v4_triggered:
        score += 10
        divergence_types.append("价跌量缩")
    if v5_triggered:
        score += v5_score
        divergence_types.append(f"OBV{'顶' if v5_type == 'top' else '底'}背离")
    if v6_triggered:
        score += 10
        divergence_types.append("单日异动")

    if score >= 60:
        health = "严重背离⚠️"
    elif score >= 40:
        health = "轻微背离"
    elif score >= 1:
        health = "正常"
    else:
        health = "健康"

    return {
        "health": health, "score": score, "divergence_types": divergence_types,
        "details": {
            "V1_surge_no_gain": {"triggered": v1_triggered, "vol_ratio": v1_vol_ratio, "return_5d": return_5d, "label": v1_label},
            "V2_surge_drop": {"triggered": v2_triggered, "vol_ratio": v2_vol_ratio, "return_5d": return_5d, "label": v2_label},
            "V3_rising_shrink": {"triggered": v3_triggered, "vol_decline": v3_vol_decline, "return_5d": return_5d, "decline_pct": v3_decline_pct, "label": v3_label},
            "V4_falling_shrink": {"triggered": v4_triggered, "vol_ratio": v4_vol_ratio, "return_5d": return_5d, "label": v4_label},
            "V5_obv": {"triggered": v5_triggered, "type": v5_type, "obv_ratio": v5_obv_ratio, "label": v5_label},
            "V6_single_day": {"triggered": v6_triggered, "vol_change": v6_vol_change, "price_up": price_up, "vol_up": vol_up, "label": v6_label},
        },
        "vs_20d_avg": vs_avg, "trend": vol_trend,
    }


def analyze_trend(code: str) -> Dict[str, Any]:
    df = _get_kline(code, days=500)
    if df is None:
        return {"daily": {"direction": "数据不足", "phase": "数据不足", "ma_arrangement": "数据不足"},
                "weekly": {"direction": "数据不足", "phase": "数据不足", "ma_arrangement": "数据不足"},
                "monthly": {"direction": "数据不足", "phase": "数据不足", "ma_arrangement": "数据不足"},
                "indicators": {"macd": {"signal": "数据不足", "diff": 0, "dea": 0, "histogram": 0},
                               "rsi_14": None, "bollinger": {"position": "数据不足", "bandwidth": 0, "squeeze": False},
                               "atr_14": None},
                "volume": {"vs_20d_avg": 1.0, "trend": "数据不足", "health": "数据不足", "score": 0, "divergence_types": [], "details": {}}}

    close = _close(df)
    daily = _analyze_single_period(df, "日线")
    weekly = _analyze_single_period(_resample_weekly(df), "周线") if len(df) >= 100 else daily
    monthly = _analyze_single_period(_resample_monthly(df), "月线") if len(df) >= 250 else daily

    # MACD
    try:
        diff, dea, hist = _compute_macd(close)
        d_val, e_val, h_val = float(diff.iloc[-1]), float(dea.iloc[-1]), float(hist.iloc[-1])
        if h_val > 0 and diff.iloc[-1] > dea.iloc[-1]:
            if diff.iloc[-2] <= dea.iloc[-2]:
                signal = "金叉"
            else:
                signal = "零轴上方"
        elif h_val < 0:
            signal = "零轴下方"
        else:
            signal = "死叉" if diff.iloc[-1] < dea.iloc[-1] else "金叉"
    except Exception:
        d_val, e_val, h_val, signal = 0, 0, 0, "数据不足"

    # RSI
    try:
        delta = close.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss.replace(0, 1)
        rsi = round(float(100 - (100 / (1 + rs.iloc[-1]))), 1)
    except Exception:
        rsi = None

    # ATR
    try:
        high, low = df["high"].astype(float), df["low"].astype(float)
        tr = pd.concat([(high - low).abs(), (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
        atr = round(float(tr.rolling(14).mean().iloc[-1]), 2)
    except Exception:
        atr = None

    # Bollinger
    try:
        ma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        upper = ma20 + 2 * std20
        lower = ma20 - 2 * std20
        bandwidth = round(float((upper.iloc[-1] - lower.iloc[-1]) / ma20.iloc[-1] * 100), 2)
        squeeze = bandwidth < 5
        price = float(close.iloc[-1])
        if price > upper.iloc[-1]:
            bb_pos = "上轨"
        elif price > ma20.iloc[-1]:
            bb_pos = "中轨上方"
        elif price > lower.iloc[-1]:
            bb_pos = "中轨下方"
        else:
            bb_pos = "下轨"
    except Exception:
        bandwidth, squeeze, bb_pos = 0, False, "数据不足"

    # Volume (PRD-019: 6-factor multi-period divergence model)
    vol_analysis = _analyze_volume_divergence(df, close)

    return {
        "daily": daily, "weekly": weekly, "monthly": monthly,
        "indicators": {
            "macd": {"signal": signal, "diff": round(d_val, 4), "dea": round(e_val, 4), "histogram": round(h_val, 4)},
            "rsi_14": rsi,
            "bollinger": {"position": bb_pos, "bandwidth": bandwidth, "squeeze": squeeze},
            "atr_14": atr,
        },
        "volume": vol_analysis,
    }


# ═══════════════════════════════════════════════
# ③ 基本面可靠性
# ═══════════════════════════════════════════════

def analyze_fundamental(code: str) -> Dict[str, Any]:
    result = {"pe": None, "pe_percentile": None, "pb": None, "pb_percentile": None,
              "ps": None, "roe": None, "roa": None,
              "gross_margin": None, "net_margin": None,
              "revenue_cagr_3y": None, "profit_cagr_3y": None,
              "debt_ratio": None, "cash_flow": "数据不足",
              "goodwill_ratio": None, "industry_rank": "数据不足",
              "market_cap_yi": None, "assessment": "数据不足"}

    try:
        from data.fetcher_core import get_ts
        pro = get_ts()
        ts_code = code + (".SZ" if code.startswith(("0", "3")) else ".SH")

        # 日线指标 (PE/PB/PS/总市值)
        df_basic = pro.daily_basic(ts_code=ts_code, fields="ts_code,trade_date,pe,pb,ps,ps_ttm,total_mv,circ_mv,turnover_rate,turnover_rate_f")
        if df_basic is not None and not df_basic.empty:
            df_basic = df_basic.sort_values("trade_date")
            last = df_basic.iloc[-1]
            result["pe"] = round(float(last.get("pe", 0)), 2) if last.get("pe") else None
            result["pb"] = round(float(last.get("pb", 0)), 2) if last.get("pb") else None
            result["ps"] = round(float(last.get("ps", 0)), 2) if last.get("ps") else None
            total_mv = last.get("total_mv")
            result["market_cap_yi"] = round(float(total_mv) / 1e4, 1) if total_mv else None

            # PE 分位数
            pe_series = pd.to_numeric(df_basic["pe"], errors="coerce").dropna()
            if len(pe_series) > 0 and result["pe"]:
                result["pe_percentile"] = round(float((pe_series < result["pe"]).sum() / len(pe_series) * 100), 1)
            pb_series = pd.to_numeric(df_basic["pb"], errors="coerce").dropna()
            if len(pb_series) > 0 and result["pb"]:
                result["pb_percentile"] = round(float((pb_series < result["pb"]).sum() / len(pb_series) * 100), 1)

        # 财务指标 (ROE/ROA/利润率/负债率)
        df_fina = pro.fina_indicator(ts_code=ts_code, fields="end_date,roe,roa,grossprofit_margin,netprofit_margin,debt_to_assets,current_ratio,quick_ratio")
        if df_fina is not None and not df_fina.empty:
            df_fina = df_fina.sort_values("end_date")
            last_fina = df_fina.iloc[-1]
            result["roe"] = round(float(last_fina.get("roe", 0)), 2) if last_fina.get("roe") else None
            result["roa"] = round(float(last_fina.get("roa", 0)), 2) if last_fina.get("roa") else None
            result["gross_margin"] = round(float(last_fina.get("grossprofit_margin", 0)), 2) if last_fina.get("grossprofit_margin") else None
            result["net_margin"] = round(float(last_fina.get("netprofit_margin", 0)), 2) if last_fina.get("netprofit_margin") else None
            result["debt_ratio"] = round(float(last_fina.get("debt_to_assets", 0)), 2) if last_fina.get("debt_to_assets") else None

        # 综合评估
        score = 0
        if result["pe"] and result["pe"] > 0:
            if result["pe"] < 20:
                score += 25
            elif result["pe"] < 40:
                score += 15
        if result["roe"] and result["roe"] > 15:
            score += 25
        elif result["roe"] and result["roe"] > 8:
            score += 15
        if result["debt_ratio"] is not None:
            if result["debt_ratio"] < 40:
                score += 20
            elif result["debt_ratio"] < 60:
                score += 10
        if result["gross_margin"] and result["gross_margin"] > 30:
            score += 15
        if result["net_margin"] and result["net_margin"] > 10:
            score += 15

        if score >= 70:
            assessment = "优秀"
        elif score >= 50:
            assessment = "良好"
        elif score >= 30:
            assessment = "一般"
        else:
            assessment = "待观察"
        result["assessment"] = assessment

    except Exception:
        pass

    return result


# ═══════════════════════════════════════════════
# ④ 资金面
# ═══════════════════════════════════════════════

def analyze_capital_flow(code: str) -> Dict[str, Any]:
    result = {"main_force_5d": None, "main_force_20d": None,
              "north_bound_holding": None, "north_bound_change_1m": None,
              "margin_balance": None, "margin_change_5d": None,
              "turnover_rate": None, "turnover_vs_avg": None,
              "assessment": "数据不足"}

    try:
        from data.fetcher_core import get_ts
        from data.fetcher import get_margin_detail
        pro = get_ts()
        ts_code = code + (".SZ" if code.startswith(("0", "3")) else ".SH")

        # 换手率 (来自 daily_basic)
        df_basic = pro.daily_basic(ts_code=ts_code, fields="ts_code,trade_date,turnover_rate,turnover_rate_f")
        if df_basic is not None and not df_basic.empty:
            df_basic = df_basic.sort_values("trade_date")
            last = df_basic.iloc[-1]
            result["turnover_rate"] = round(float(last.get("turnover_rate_f", 0)), 2) if last.get("turnover_rate_f") else None
            avg_20 = float(df_basic["turnover_rate_f"].tail(20).mean()) if len(df_basic) >= 20 else None
            if avg_20 and result["turnover_rate"] and avg_20 > 0:
                result["turnover_vs_avg"] = round(result["turnover_rate"] / avg_20, 2)

        # 融资融券
        try:
            margin = get_margin_detail(ts_code=ts_code)
            if margin and "error" not in margin:
                result["margin_balance"] = round(float(margin.get("rzye", 0)), 2) if margin.get("rzye") else None
        except Exception:
            pass

        # 综合评估
        score = 0
        if result["turnover_rate"]:
            if result["turnover_rate"] > 5:
                score += 30
            elif result["turnover_rate"] > 2:
                score += 15
        if result["turnover_vs_avg"]:
            if result["turnover_vs_avg"] > 1.5:
                score += 25
            elif result["turnover_vs_avg"] > 1.0:
                score += 15
        if result["margin_balance"] and result["margin_balance"] > 0:
            score += 15

        if score >= 50:
            assessment = "资金活跃"
        elif score >= 25:
            assessment = "资金一般"
        else:
            assessment = "资金冷淡"
        result["assessment"] = assessment

    except Exception:
        pass

    return result


# ═══════════════════════════════════════════════
# ⑤ 五维雷达评分 + 综合结论
# ═══════════════════════════════════════════════

def compute_radar_scores(price_pos, trend, fundamental, capital) -> Dict[str, Any]:
    scores = {"价格阶段": 0, "技术趋势": 0, "基本面": 0, "资金面": 0, "情绪": 0}

    # 价格阶段分
    pct = price_pos.get("pct_1y")
    if pct is not None:
        if pct < 20:
            scores["价格阶段"] = 85
        elif pct < 40:
            scores["价格阶段"] = 70
        elif pct < 60:
            scores["价格阶段"] = 55
        elif pct < 80:
            scores["价格阶段"] = 35
        else:
            scores["价格阶段"] = 15
    zone = price_pos.get("zone", "")
    if "低位" in zone:
        scores["价格阶段"] = max(scores["价格阶段"], 75)

    # 技术趋势分
    daily = trend.get("daily", {})
    if daily.get("direction") == "上升":
        scores["技术趋势"] += 40
    elif daily.get("direction") == "横盘":
        scores["技术趋势"] += 20
    if daily.get("ma_arrangement") == "多头排列":
        scores["技术趋势"] += 25
    macd = trend.get("indicators", {}).get("macd", {})
    if "金叉" in macd.get("signal", ""):
        scores["技术趋势"] += 20
    elif "零轴上方" in macd.get("signal", ""):
        scores["技术趋势"] += 10
    vol = trend.get("volume", {})
    if vol.get("health") == "健康":
        scores["技术趋势"] += 15

    # 基本面分
    fa = fundamental.get("assessment", "")
    if fa == "优秀":
        scores["基本面"] = 85
    elif fa == "良好":
        scores["基本面"] = 65
    elif fa == "一般":
        scores["基本面"] = 40
    elif fa == "待观察":
        scores["基本面"] = 20

    # 资金面分
    ca = capital.get("assessment", "")
    if "活跃" in ca:
        scores["资金面"] = 75
    elif "一般" in ca:
        scores["资金面"] = 50
    elif "冷淡" in ca:
        scores["资金面"] = 25

    # 情绪分 (从 RSI + BB 估算)
    rsi = trend.get("indicators", {}).get("rsi_14")
    if rsi is not None:
        if 45 <= rsi <= 65:
            scores["情绪"] = 70
        elif 30 <= rsi <= 70:
            scores["情绪"] = 50
        elif rsi > 70:
            scores["情绪"] = 30
        else:
            scores["情绪"] = 60

    total = round(
        scores["价格阶段"] * 0.15 + scores["技术趋势"] * 0.30 +
        scores["基本面"] * 0.30 + scores["资金面"] * 0.15 +
        scores["情绪"] * 0.10
    )

    return {**scores, "综合": total}


def generate_conclusion(price_pos, trend, fundamental, capital, radar) -> Dict[str, str]:
    total = radar.get("综合", 0)
    risks = collect_risks(price_pos, trend, fundamental, capital)
    risk_count = len(risks)

    if total >= 70 and risk_count <= 2:
        level = "强烈推荐"
        label = "🟢 强烈推荐"
    elif total >= 55:
        level = "关注"
        label = "🟡 关注"
    elif total >= 35:
        level = "观望"
        label = "🟠 观望"
    else:
        level = "回避"
        label = "🔴 回避"

    reasons = []
    if risk_count > 0:
        reasons.append(f"{risk_count}个风险点需注意")

    daily = trend.get("daily", {})
    if daily.get("direction") == "上升":
        reasons.append(f"{daily.get('phase', '')}中")
    elif daily.get("direction") == "下降":
        reasons.append(f"趋势走弱")

    pct = price_pos.get("pct_1y")
    if pct is not None:
        if pct > 85:
            reasons.append("价格处于高位")
        elif pct < 15:
            reasons.append("价格处于低位")

    fa = fundamental.get("assessment", "")
    if fa in ("优秀", "良好"):
        reasons.append(f"基本面{fa}")

    return {"level": level, "label": label, "reason": " | ".join(reasons) if reasons else "数据不足"}


def collect_risks(price_pos, trend, fundamental, capital) -> list:
    risks = []

    pct = price_pos.get("pct_1y")
    if pct is not None and pct > 85:
        risks.append(f"价格处于1年高位 ({pct}% 分位)")
    if price_pos.get("zone", "").startswith("高位"):
        risks.append("当前处于历史高位区")

    daily = trend.get("daily", {})
    if daily.get("direction") == "下降":
        risks.append(f"日线趋势{daily.get('phase', '走弱')}")
    if daily.get("ma_arrangement") == "空头排列":
        risks.append("均线空头排列")
    vol = trend.get("volume", {})
    divergence_types = vol.get("divergence_types", [])
    for dt in divergence_types:
        risks.append(f"量价背离: {dt}")
    rsi = trend.get("indicators", {}).get("rsi_14")
    if rsi is not None and rsi > 75:
        risks.append(f"RSI 超买 ({rsi})")

    if fundamental.get("debt_ratio") and float(fundamental["debt_ratio"]) > 60:
        risks.append(f"资产负债率偏高 ({fundamental['debt_ratio']}%)")
    if fundamental.get("roe") is not None and fundamental["roe"] < 0:
        risks.append("ROE 为负")
    fa = fundamental.get("assessment", "")
    if fa in ("一般", "待观察"):
        risks.append(f"基本面{fa}")

    return risks


# ═══════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════

def analyze_stock(code: str, name: str = "") -> Dict[str, Any]:
    from data.fetcher import get_kline

    try:
        df = get_kline(code, days=250)
        if df is not None and not df.empty:
            current_price = round(float(df.iloc[-1]["close"]), 2)
        else:
            current_price = None
    except Exception:
        current_price = None

    results = {"code": code, "name": name, "price": current_price,
               "date": time.strftime("%Y-%m-%d")}

    for analyzer_name, analyzer_fn in [
        ("price_position", analyze_price_position),
        ("trend", analyze_trend),
        ("fundamental", analyze_fundamental),
        ("capital_flow", analyze_capital_flow),
    ]:
        try:
            results[analyzer_name] = analyzer_fn(code)
        except Exception as e:
            results[analyzer_name] = {"error": str(e)}

    results["radar"] = compute_radar_scores(
        results.get("price_position", {}), results.get("trend", {}),
        results.get("fundamental", {}), results.get("capital_flow", {}),
    )
    results["risk"] = collect_risks(
        results.get("price_position", {}), results.get("trend", {}),
        results.get("fundamental", {}), results.get("capital_flow", {}),
    )
    results["conclusion"] = generate_conclusion(
        results.get("price_position", {}), results.get("trend", {}),
        results.get("fundamental", {}), results.get("capital_flow", {}),
        results.get("radar", {}),
    )
    return _sanitize(results)


# ═══════════════════════════════════════════════
# JSON 安全化：递归替换 NaN/Inf 为 None
# ═══════════════════════════════════════════════

def _sanitize(obj):
    import math
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    return obj
