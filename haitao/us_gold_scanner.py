"""
海淘掘金 — 美股翻倍挖掘机 v2.0
对标 A股 magic doubler 模型 + 美股月内翻倍股历史数据分析

8维评分 (100分) — v3.0 质量成长
1. 💰 价格分层 (5分) — 对标A股五档，阈值适配美股
2. 🏢 市值弹性 (5分) — 微盘<100M:60% doublers
3. 🔥 行业热度 (8分) — AI/量子/生物医药=核心产区
4. 📐 技术形态 (20分) — MA/RSI/量比/D0启动模式 ⬆️ +10
5. 📊 估值安全 (12分) — PE/PEG合理区间 ✨ — 低流通盘+高空头=翻倍催化剂
6. ⚡ 催化事件 (10分) — 财报窗口+52周低位+量能异动
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd

from haitao.us_fetcher import get_quotes, get_history, calc_technical_indicators

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# 评分常量 (对标 A股 doubler_scanner)
# ═══════════════════════════════════════════════════════════════

# ─── 价格分层 (5分) — v3.1 质量反转 ────
PRICE_TIERS = {
    "ultra_low":  {"max": 5,    "score": 0,  "label": "仙股风险"},
    "low":        {"max": 20,   "score": 1,  "label": "低价"},
    "mid":        {"max": 50,   "score": 2,  "label": "中价"},
    "mid_high":   {"max": 150,  "score": 3,  "label": "中高价"},
    "high":       {"max": float("inf"), "score": 5, "label": "高价(质量)"},
}

# ─── 市值弹性 (5分) — v3.1 质量反转 ────
MV_TIERS = {
    "micro":  {"max": 100e6,   "score": 0,  "label": "微型(风险)"},
    "small":  {"max": 500e6,   "score": 1,  "label": "小型"},
    "mid":    {"max": 2e9,     "score": 2,  "label": "中型"},
    "large":  {"max": 50e9,    "score": 3,  "label": "大型"},
    "mega":   {"max": float("inf"), "score": 5, "label": "超大型(赢家)"},
}

# ─── 行业热度 (占比来源: doublers 报告) ────
HOT_SECTORS = {
    "quantum":       {"keywords": ["quantum"],                     "score": 10, "label": "量子计算"},
    "space":         {"keywords": ["space", "aerospace", "defense"], "score": 9, "label": "航天国防"},
    "ai_semi":       {"keywords": ["semiconductor", "ai", "artificial intelligence",
                                   "software", "computer", "technology",
                                   "communication", "internet", "digital",
                                   "information"],                "score": 9, "label": "AI/科技"},
    "biotech":       {"keywords": ["biotechnology", "drug manufacturers",
                                   "medical devices", "healthcare",
                                   "pharmaceutical"],             "score": 8, "label": "生物医药"},
    "new_energy":    {"keywords": ["solar", "battery", "electric vehicle",
                                   "renewable", "clean energy"],   "score": 7, "label": "新能源"},
    "fintech_crypto":{"keywords": ["crypto", "blockchain", "fintech",
                                   "financial", "capital markets"],"score": 6, "label": "金融科技"},
    "robotics":      {"keywords": ["robotics", "automation"],       "score": 5, "label": "机器人"},
}

# ─── 流通盘/做空 ────────────────────────
FLOAT_THRESHOLD = 20_000_000      # <2000万股 = 超低流通盘
SHORT_FLOAT_THRESHOLD = 0.20      # >20% 空头持仓 = 逼空潜力
DAYS_TO_COVER_THRESHOLD = 3       # >3天 = 逼空压力大

# ─── 技术形态 ────────────────────────────
VOL_SURGE_THRESHOLD = 2.0         # 量比 > 2x = 异动（v2.1: 3.0→2.0更敏感）

# ═══════════════════════════════════════════════════════════════
# 过滤列表
# ═══════════════════════════════════════════════════════════════

ETF_BLACKLIST = {
    "SPY", "QQQ", "IWM", "DIA", "VTI", "VOO", "IVV",
    "QQQM", "VEA", "VWO", "BND", "AGG", "GLD", "SLV",
    "TLT", "IEF", "SHY", "LQD", "HYG", "EEM", "EFA",
    "XLF", "XLE", "XLK", "XLV", "XLI", "XLP", "XLY",
    "XLB", "XLU", "XLRE", "SOXX", "SMH", "IBB", "XBI",
    "TQQQ", "SQQQ", "SPXL", "SPXS", "SOXL", "SOXS",
    "TNA", "TZA", "UDOW", "SDOW", "LABU", "LABD",
}

def _is_etf(ticker: str, info: dict = None) -> bool:
    """检测是否为ETF/指数基金"""
    if ticker.upper() in ETF_BLACKLIST:
        return True
    if info:
        quote_type = (info.get("quoteType") or "").upper()
        if quote_type in ("ETF", "INDEX", "MUTUALFUND"):
            return True
    return False

# ═══════════════════════════════════════════════════════════════
# 核心评分引擎
# ═══════════════════════════════════════════════════════════════

def gold_score(ticker: str) -> dict:
    """对美股进行翻倍潜力评分 (0-100)

    6维评分 + D0模式检测，对标 A股 doubler_scanner
    """
    # ── 1. 获取K线数据 ───────────────────
    hist = get_history(ticker, 180)
    if hist is None or len(hist) < 5:
        hist = get_history(ticker, 60)
    if hist is None or len(hist) < 5:
        hist = get_history(ticker, 30)
    if hist is None or len(hist) < 5:
        return {"ticker": ticker, "score": 0, "rating": "数据不足", "error": "insufficient_data"}

    if not isinstance(hist, pd.DataFrame):
        hist = pd.DataFrame(hist)

    tech = calc_technical_indicators(hist)
    days = len(hist)
    data_quality = "优" if days >= 60 else ("中" if days >= 20 else "低")

    close = hist["Close"].values.astype(float)
    volume = hist["Volume"].values.astype(float) if "Volume" in hist.columns else None
    current_price = float(close[-1])

    # ── 2. 获取基本面数据 (yfinance info) ──
    info = _get_ticker_info(ticker)

    # ── ETF 过滤 ──────────────────────────
    if _is_etf(ticker, info):
        return {"ticker": ticker, "score": 0, "rating": "ETF跳过",
                "phase": "不参与评分", "current_price": round(current_price, 2),
                "gold_signals": ["📦 ETF/指数基金"], "detail": {},
                "d0_mode": "无", "market_cap": "", "sector": info.get("sector", ""),
                "error": "etf_skip"}

    score = 0
    signals = []
    detail = {}

    # ── 3. 六维评分 ─────────────────────
    # D1: 价格分层 (15)
    d1, price_tier = _score_price(current_price)
    score += d1
    detail["price"] = {"score": d1, "tier": price_tier}
    if d1 >= 8:
        signals.append(f"💰 {price_tier}(${current_price:.2f})")

    # D2: 市值弹性 (7)
    d2, mv_tier = _score_market_cap(info)
    score += d2
    detail["market_cap"] = {"score": d2, "tier": mv_tier, "value": info.get("market_cap")}
    if d2 >= 8:
        signals.append(f"🏢 {mv_tier}({_fmt_mv(info.get('market_cap'))})")

    # D3: 行业热度 (8)
    d3, sector_label = _score_sector(info)
    score += d3
    detail["sector"] = {"score": d3, "sector": info.get("sector", ""), "label": sector_label}
    if d3 >= 7:
        signals.append(f"🔥 {sector_label}")

    # D4: 技术形态 (30) — 含 D0 启动模式
    d4, tech_signals, d0_mode = _score_technical_v2(tech, close, volume)
    score += d4
    detail["technical"] = {"score": d4, "d0_mode": d0_mode}
    signals.extend(tech_signals)

    # D5: 估值安全 (12) — v3.0 质量成长
    d5, val_signals = _score_valuation(info)
    score += d5
    detail["valuation"] = {"score": d5, "pe": info.get("pe", 0)}
    signals.extend(val_signals)

    # D6: 催化事件 (10)
    d6, cat_signals = _score_catalyst_v2(close, volume, info)
    score += d6
    detail["catalyst"] = {"score": d6}
    signals.extend(cat_signals)

    # D7: 基本面质量 (20)
    d7, fund_signals = _score_fundamentals(info)
    score += d7
    detail["fundamentals"] = {"score": d7}
    signals.extend(fund_signals)

    # D8: 增长潜力 (20)
    d8, growth_signals = _score_growth(info)
    score += d8
    detail["growth"] = {"score": d8}
    signals.extend(growth_signals)

    # ── 4. 总分与评级 ─────────────────────
    score = max(0, min(100, score))
    rating = _gold_rating(score)
    phase = _gold_phase(score, d0_mode)

    return {
        "ticker": ticker,
        "score": score,
        "rating": rating,
        "phase": phase,
        "data_quality": data_quality,
        "current_price": round(current_price, 2),
        "gold_signals": signals,
        "detail": detail,
        "technicals": {
            "rsi14": tech.get("rsi14"),
            "sma20": tech.get("sma20"),
            "sma60": tech.get("sma60"),
            "atr14": tech.get("atr14"),
            "vol_ratio": tech.get("volume_ratio"),
        },
        "d0_mode": d0_mode,
        "market_cap": _fmt_mv(info.get("market_cap")),
        "sector": info.get("sector", ""),
    }


# ═══════════════════════════════════════════════════════════════
# D1: 价格分层 (15分) — 对标 A股 _price_score
# ═══════════════════════════════════════════════════════════════

def _score_price(price: float) -> Tuple[int, str]:
    for tier, cfg in PRICE_TIERS.items():
        if price <= cfg["max"]:
            return cfg["score"], cfg["label"]
    return 2, "高价"


# ═══════════════════════════════════════════════════════════════
# D2: 市值弹性 (15分) — 对标 A股 _mv_score
# ═══════════════════════════════════════════════════════════════

def _score_market_cap(info: dict) -> Tuple[int, str]:
    mc = info.get("market_cap") or 0
    for tier, cfg in MV_TIERS.items():
        if mc <= cfg["max"]:
            return cfg["score"], cfg["label"]
    return 1, "超大型"


# ═══════════════════════════════════════════════════════════════
# D3: 行业热度 (10分) — 对标 A股 _industry_score
# ═══════════════════════════════════════════════════════════════

def _score_sector(info: dict) -> Tuple[int, str]:
    sector = (info.get("sector") or "").lower()
    industry = (info.get("industry") or "").lower()
    combined = f"{sector} {industry}"

    for key, cfg in HOT_SECTORS.items():
        for kw in cfg["keywords"]:
            if kw.lower() in combined:
                return cfg["score"], cfg["label"]
    return 4, "其他"


# ═══════════════════════════════════════════════════════════════
# D4: 技术形态 (30分) — 对标 A股 D0 多模式 + 技术指标
# ═══════════════════════════════════════════════════════════════

def _score_technical_v2(tech: dict, close: np.ndarray,
                         volume: np.ndarray) -> Tuple[int, list, str]:
    """趋势质量评分 (0-20) — v3.1 质量成长：长期均线健康+低波稳定+RSI合理"""
    ts = 0
    sigs = []
    current_price = float(close[-1])

    # ── 长期趋势健康 (10分) — SMA60/120上行 + 价格在均线上方 ──
    sma20, sma60 = tech.get("sma20"), tech.get("sma60")
    if sma20 and sma60:
        if current_price > sma20 > sma60:
            ts += 10; sigs.append("📈 长期上升趋势")
        elif current_price > sma60:
            ts += 7; sigs.append("站上SMA60")
        elif current_price > sma20:
            ts += 4
        elif current_price < sma20 < sma60:
            ts -= 2; sigs.append("⚠️ 下行趋势")

    # ── 低波动稳定 (5分) — 低ATR=机构控盘 ──
    atr14 = tech.get("atr14")
    if atr14 and current_price > 0:
        atr_pct = atr14 / current_price * 100
        if atr_pct < 3:
            ts += 5; sigs.append(f"📐 低波稳定(ATR{atr_pct:.1f}%)")
        elif atr_pct < 5:
            ts += 3
        elif atr_pct < 8:
            ts += 1

    # ── RSI健康 (4分) — 不超买不超卖 ──
    rsi = tech.get("rsi14")
    if rsi is not None:
        if 40 <= rsi <= 65:
            ts += 4; sigs.append(f"RSI健康({rsi:.0f})")
        elif 30 <= rsi < 40:
            ts += 3; sigs.append(f"RSI偏弱({rsi:.0f})")
        elif rsi > 75:
            ts -= 2; sigs.append(f"⚠️ RSI过热({rsi:.0f})")
        else:
            ts += 1

    # ── 均线支撑确认 (3分) — 价格在关键MA之上 ──
    sma100 = tech.get("sma100")
    if sma100 and current_price > sma100:
        ts += 3

    d0_mode = "趋势" if ts >= 12 else "整理"

    return max(-5, min(20, ts)), sigs, d0_mode


def _detect_d0_pattern(close: np.ndarray, volume: np.ndarray,
                       tech: dict) -> str:
    """D0 启动前期模式检测（对标 A股 4模式）"""
    chg_5d = (close[-1] / close[-5] - 1) * 100 if len(close) >= 5 else 0
    chg_20d = (close[-1] / close[-20] - 1) * 100 if len(close) >= 20 else 0

    # coiled_spring: 20日微跌(<5%) + 5日启动 + RSI 30-50
    rsi = tech.get("rsi14")
    if -8 <= chg_20d <= 3 and chg_5d > 0 and rsi and 30 <= rsi <= 50:
        return "coiled_spring"

    # silent_accumulation: 窄幅震荡 + 缩量后放量
    if abs(chg_20d) <= 10 and chg_5d > 0 and rsi and 40 <= rsi <= 60:
        return "silent_accumulation"

    # early_warming: 量价初升 + RSI刚过50
    if chg_5d > 3 and chg_20d > 0 and rsi and 50 <= rsi <= 65:
        return "early_warming"

    # smart_pullback: 上升趋势中的健康回调
    sma20 = tech.get("sma20")
    if sma20 and close[-1] > sma20 and -5 <= chg_5d <= 0 and chg_20d > 5:
        return "smart_pullback"

    return "启动中"


# ═══════════════════════════════════════════════════════════════
# D5: 逼空潜力 (15分) — US only
# ═══════════════════════════════════════════════════════════════

def _score_short_squeeze(info: dict) -> Tuple[int, list]:
    ss = 0
    sigs = []
    float_shares = info.get("float_shares") or 0
    short_float = info.get("short_float") or 0

    # 低流通盘 (8分)
    if 0 < float_shares < 10_000_000:
        ss += 8; sigs.append(f"🩳 超低流通盘({float_shares/1e6:.1f}M股)")
    elif float_shares < FLOAT_THRESHOLD:
        ss += 5; sigs.append(f"低流通盘({float_shares/1e6:.1f}M股)")
    elif float_shares < 50_000_000:
        ss += 2

    # 高空头持仓 (7分)
    if short_float > 0.30:
        ss += 7; sigs.append(f"💣 高空头({short_float*100:.0f}%)←强逼空")
    elif short_float > SHORT_FLOAT_THRESHOLD:
        ss += 5; sigs.append(f"空头持仓{short_float*100:.0f}%")
    elif short_float > 0.10:
        ss += 2

    return min(15, ss), sigs


# ═══════════════════════════════════════════════════════════════
# D6: 催化事件 (15分) — 对标 A股 catalyst
# ═══════════════════════════════════════════════════════════════

def _score_catalyst_v2(close: np.ndarray, volume: np.ndarray,
                        info: dict) -> Tuple[int, list]:
    cs = 0
    sigs = []

    # ── 52周低位反弹 (4分) ──────────────
    if len(close) >= 60:
        high_52w = float(np.max(close[-250:])) if len(close) >= 250 else float(np.max(close))
        current = float(close[-1])
        decline = (1 - current / high_52w) * 100 if high_52w > 0 else 0
        if decline > 70:
            cs += 4; sigs.append(f"📉 距高点-{decline:.0f}%←超跌反弹")
        elif decline > 40:
            cs += 3; sigs.append(f"距高点-{decline:.0f}%")
        elif decline > 20:
            cs += 1

    # ── 稳定上行趋势 (4分) — v3.1 质量股专属 ──
    if len(close) >= 60:
        ret_3m = (close[-1] / close[-60] - 1) * 100 if len(close) >= 60 else 0
        if ret_3m > 20:
            cs += 4; sigs.append(f"📈 3月+{ret_3m:.0f}%(稳步上行)")
        elif ret_3m > 10:
            cs += 3; sigs.append(f"3月+{ret_3m:.0f}%")
        elif ret_3m > 5:
            cs += 2
        elif ret_3m > 0:
            cs += 1

    # ── 近期动量 (5分) ─────────────────
    if len(close) >= 20:
        ret_1m = (close[-1] / close[-20] - 1) * 100
        if 3 <= ret_1m <= 15:
            cs += 5; sigs.append(f"📈 1月+{ret_1m:.0f}%(温和启动)")
        elif 15 < ret_1m <= 30:
            cs += 3; sigs.append(f"1月+{ret_1m:.0f}%(主升段)")
        elif -20 <= ret_1m < 0:
            cs += 2

    # ── 财报窗口 (4分) ─────────────────
    try:
        import yfinance as yf
        cal = yf.Ticker(info.get("ticker", "")).calendar
        if cal is not None and not cal.empty:
            ed = cal.get("Earnings Date")
            if ed is not None:
                next_e = ed[0] if hasattr(ed, '__iter__') and len(ed) > 0 else ed
                if next_e:
                    from datetime import datetime
                    days_to = (next_e - datetime.now()).days if hasattr(next_e, 'days') else 30
                    if 0 <= days_to <= 14:
                        cs += 4; sigs.append(f"📅 {days_to}天财报")
                    elif days_to <= 30:
                        cs += 2
    except Exception:
        pass

    return min(10, cs), sigs


# ═══════════════════════════════════════════════════════════════
# D7: 基本面质量 (15分) — v2.2 NEW
# ═══════════════════════════════════════════════════════════════

def _score_fundamentals(info: dict) -> Tuple[int, list]:
    """基本面评分：营收增速 + 利润率 + ROE"""
    fs = 0
    sigs = []

    # 营收增速 (7分)
    rev_growth = (info.get("revenue_growth") or 0) * 100
    if rev_growth > 40:
        fs += 7; sigs.append(f"📊 营收+{rev_growth:.0f}%🚀")
    elif rev_growth > 20:
        fs += 5; sigs.append(f"营收+{rev_growth:.0f}%")
    elif rev_growth > 10:
        fs += 3
    elif rev_growth > 5:
        fs += 1
    elif rev_growth < -10:
        fs -= 2

    # 利润率 (7分)
    margin = (info.get("profit_margin") or 0) * 100
    if margin > 30:
        fs += 7; sigs.append(f"💎 利润率{margin:.0f}%")
    elif margin > 15:
        fs += 5; sigs.append(f"利润率{margin:.0f}%")
    elif margin > 5:
        fs += 2
    elif margin > 0:
        fs += 1
    elif margin < -10:
        fs -= 2; sigs.append(f"⚠️ 亏损{margin:.0f}%")

    # ROE (6分)
    roe = (info.get("roe") or 0) * 100
    if roe > 30:
        fs += 6; sigs.append(f"📈 ROE{roe:.0f}%")
    elif roe > 15:
        fs += 4; sigs.append(f"ROE{roe:.0f}%")
    elif roe > 8:
        fs += 2

    return max(-5, min(20, fs)), sigs


# ═══════════════════════════════════════════════════════════════
# D8: 增长潜力 (10分) — v2.2 NEW
# ═══════════════════════════════════════════════════════════════

def _score_growth(info: dict) -> Tuple[int, list]:
    """增长潜力：分析师评级 + 目标价空间 + 机构持仓"""
    gs = 0
    sigs = []

    # 分析师共识 (7分)
    rec = info.get("recommendation") or ""
    rec_lower = rec.lower()
    if "strong" in rec_lower:
        gs += 7; sigs.append("🎯 强烈买入")
    elif "buy" in rec_lower:
        gs += 5; sigs.append("📋 分析师买入")
    elif "overweight" in rec_lower or "outperform" in rec_lower:
        gs += 3
    elif "hold" in rec_lower or "neutral" in rec_lower:
        gs += 1

    # 目标价上涨空间 (7分)
    target = info.get("target_price") or 0
    current = info.get("current_price") or 0
    if target > 0 and current > 0:
        upside = (target / current - 1) * 100
        if upside > 50:
            gs += 7; sigs.append(f"🎯 目标+{upside:.0f}%")
        elif upside > 25:
            gs += 5; sigs.append(f"目标+{upside:.0f}%")
        elif upside > 10:
            gs += 3
        elif upside > 5:
            gs += 1

    # 机构持仓 (6分)
    inst_held = (info.get("institutional_holding") or 0) * 100
    if inst_held > 80:
        gs += 6; sigs.append(f"🏦 机构{inst_held:.0f}%(重仓)")
    elif inst_held > 60:
        gs += 4; sigs.append(f"机构{inst_held:.0f}%")
    elif inst_held > 40:
        gs += 2
    elif inst_held > 20:
        gs += 1

    return min(20, gs), sigs


# ═══════════════════════════════════════════════════════════════
# D5' 估值安全 (12分) — v3.0 质量成长 NEW
# ═══════════════════════════════════════════════════════════════

def _score_valuation(info: dict) -> Tuple[int, list]:
    """估值安全评分：PE合理 + PEG合理 + PS不泡沫"""
    vs = 0
    sigs = []

    # PE估值 (6分)
    pe = info.get("pe") or 0
    if 0 < pe <= 20:
        vs += 6; sigs.append(f"📊 PE{pe:.0f}(低估)")
    elif 20 < pe <= 35:
        vs += 4; sigs.append(f"PE{pe:.0f}(合理)")
    elif 35 < pe <= 60:
        vs += 2
    elif pe > 60:
        vs -= 1; sigs.append(f"⚠️ PE{pe:.0f}(偏高)")
    # pe=0 means no data or negative earnings → neutral

    # PEG估值 (4分) — 增速匹配度
    rev_growth = (info.get("revenue_growth") or 0) * 100
    if pe > 0 and rev_growth > 0:
        peg = pe / rev_growth if rev_growth > 0 else 99
        if 0 < peg <= 1.0:
            vs += 4; sigs.append(f"🎯 PEG{peg:.1f}(低估)")
        elif peg <= 2.0:
            vs += 2
        elif peg > 4:
            vs -= 1

    # PS估值 (2分) — 市销率不泡沫
    mc = info.get("market_cap") or 0
    if mc > 0 and rev_growth > 0:
        # Rough estimate: PS ~= marketCap / (marketCap * revenueYield)
        # Simpler: if revenue is growing fast and market cap is reasonable
        if rev_growth > 20 and mc < 50e9:
            vs += 2

    return max(-5, min(12, vs)), sigs


# ═══════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════

def _get_ticker_info(ticker: str) -> dict:
    """获取 yfinance 基本面数据（带缓存避免重复调用）"""
    try:
        import yfinance as yf
        tk = yf.Ticker(ticker)
        i = tk.info or {}

        # 做空数据 — yfinance 返回的是小数(0.1877=18.77%), 不是百分比
        raw_short = i.get("shortPercentOfFloat")
        if raw_short is not None and raw_short > 0:
            short_pct = float(raw_short) if raw_short <= 1 else float(raw_short) / 100.0
        else:
            short_pct = 0.0

        return {
            "market_cap": i.get("marketCap") or 0,
            "float_shares": i.get("floatShares") or i.get("sharesOutstanding", 0) * 0.7,
            "short_float": short_pct,
            "sector": i.get("sector", ""),
            "industry": i.get("industry", ""),
            "pe": i.get("trailingPE") or i.get("forwardPE") or 0,
            "ticker": ticker,
            # v2.2 基本面数据
            "revenue_growth": i.get("revenueGrowth") or 0,
            "profit_margin": i.get("profitMargins") or 0,
            "roe": i.get("returnOnEquity") or 0,
            "recommendation": i.get("recommendationKey") or "",
            "target_price": i.get("targetMeanPrice") or 0,
            "current_price": i.get("currentPrice") or i.get("regularMarketPrice") or 0,
            "institutional_holding": i.get("heldPercentInstitutions") or 0,
            "quoteType": i.get("quoteType") or "",
        }
    except Exception:
        return {"market_cap": 0, "float_shares": 0, "short_float": 0,
                "sector": "", "industry": "", "pe": 0, "ticker": ticker,
                "revenue_growth": 0, "profit_margin": 0, "roe": 0,
                "recommendation": "", "target_price": 0, "current_price": 0,
                "institutional_holding": 0, "quoteType": ""}


def _fmt_mv(market_cap) -> str:
    if not market_cap:
        return "N/A"
    mc = float(market_cap)
    if mc >= 1e12:
        return f"${mc/1e12:.1f}T"
    elif mc >= 1e9:
        return f"${mc/1e9:.1f}B"
    elif mc >= 1e6:
        return f"${mc/1e6:.0f}M"
    return f"${mc:.0f}"


def _gold_rating(score: int) -> str:
    if score >= 75:  return "🥇 金矿"
    elif score >= 60: return "🥈 银矿"
    elif score >= 45: return "🥉 铜矿"
    elif score >= 30: return "🪨 石矿"
    else:             return "⏳ 待观察"


def _gold_phase(score: int, d0_mode: str = "") -> str:
    if d0_mode and d0_mode != "无" and d0_mode != "启动中":
        return f"🚀 {d0_mode}"
    if score >= 75:  return "🔥 黄金爆发期"
    elif score >= 60: return "⛏️ 掘金窗口期"
    elif score >= 45: return "🔍 值得关注"
    elif score >= 30: return "👀 保持观察"
    else:             return "⏳ 等待时机"


# ═══════════════════════════════════════════════════════════════
# 批量掘金扫描 (保持接口不变)
# ═══════════════════════════════════════════════════════════════

def gold_pan(tickers: List[str]) -> List[dict]:
    """批量淘金——扫描多个标的返回黄金评分排序"""
    results = []
    with ThreadPoolExecutor(max_workers=2) as ex:
        fut_map = {ex.submit(gold_score, t): t for t in tickers}
        for f in as_completed(fut_map):
            try:
                r = f.result(timeout=25)
                if "error" not in r:
                    results.append(r)
            except Exception:
                pass
    results.sort(key=lambda x: x.get("score", 0), reverse=True)
    return results


def gold_pan_hot() -> List[dict]:
    from haitao.config import HOT_US_STOCKS
    return gold_pan(HOT_US_STOCKS)


def gold_pan_adr() -> List[dict]:
    from haitao.config import CHINESE_ADR
    return gold_pan(CHINESE_ADR)


def gold_pan_top_gainers() -> List[dict]:
    from haitao.us_fetcher import get_hot_stocks
    hot = get_hot_stocks()
    tickers = [h["ticker"] for h in hot if h.get("change_pct", 0) is not None][:10]
    return gold_pan(tickers) if tickers else []


def gold_report(tickers: List[str] = None) -> dict:
    from haitao.config import HOT_US_STOCKS, CHINESE_ADR
    if not tickers:
        tickers = list(set(HOT_US_STOCKS[:8] + CHINESE_ADR[:8]))
    results = gold_pan(tickers)
    golds = [r for r in results if r.get("score", 0) >= 60]
    silvers = [r for r in results if 45 <= r.get("score", 0) < 60]
    return {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "total_scanned": len(results),
        "golds": golds,
        "silvers": silvers,
        "top_pick": results[0] if results else None,
    }
