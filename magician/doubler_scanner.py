"""
Magician 美股翻倍股评估引擎 (US Doubler Scanner)

对标A股魔法师V4架构，适配美股双向交易特性:
   做多评分 (0-100): 12维评分 + 启动前期检测(D0多模式)
   额外维度: C8财报窗口/C9做空压制/C10杠杆弹性

复用:
   haitao/us_scanner.py      — 技术评分 (趋势+动量+成交量)
   haitao/us_gold_scanner.py — 黄金挖掘 (技术面+催化剂+空间)

信条:
   - 美股可双向交易 — 做多和做空都是武器
   - 盘前盘后是信息金矿 — gap分析专用
   - 做多逻辑不做空套 — 多空信号分离
"""
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd

from haitao.us_fetcher import get_quotes, get_history, calc_technical_indicators
from haitao.us_scanner import score_stock
from magician.config import (
    DOUBLER_SEED_POOL, CATALYST_WEIGHTS,
    DOUBLER_SCORE_THRESHOLD,
    COILED_SPRING_MIN_DROP, COILED_SPRING_MAX_DROP,
    SILENT_ACCUM_DAYS, EARLY_WARMING_MIN_VOL,
    SMART_PULLBACK_MIN_DROP, SMART_PULLBACK_MAX_DROP,
)
from haitao.config import CACHE_TTL_SCAN
from haitao.us_fetcher import _cached, _set_cache

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# 核心评分引擎
# ═══════════════════════════════════════════════════════════════

def score_doubler(ticker: str) -> dict:
    """对一只美股进行翻倍潜力评分 (0-100)

    12维评分体系 (对齐A股魔法师C1-C7, 新增C8-C10):

    做多维度:
      C1 量价启动(15) — 近5日放量+价格突破
      C2 趋势强度(12) — MA多头排列+价格位置
      C3 机构进场(10) — 成交量激增+资金关注
      C4 估值合理(10) — PE合理区间
      C5 财报窗口(10) — 14天内财报发布
      C6 赛道热度(10) — AI/半导体/新能源等热点
      C7 资金流入(8) — 盘前异动+上涨延续性
      C8 做空压制释放(8) — 高做空比+近期未下跌
      C9 盘前异动(7) — 盘前涨>2%且非消息驱动
      C10 波动弹性(5) — ATR适中保障上涨空间

    D0多模式检测 (启动前期):
      - coiled_spring: 蓄力待发(回调后缩量企稳)
      - silent_accumulation: 默默吸筹(窄幅盘整+成交量温和)
      - early_warming: 早期预热(量价齐升初段)
      - smart_pullback: 聪明回调(上升趋势中的健康回调)

    Returns:
        dict with score, patterns, signals, catalyst details
    """
    # ── 1. 获取数据 ────────────────────────────
    df = get_history(ticker, days=180)
    if df is None or len(df) < 20:
        return _empty_result(ticker, "数据不足")

    tech = calc_technical_indicators(df)
    if not tech:
        return _empty_result(ticker, "技术指标计算失败")

    close = df["Close"].values.astype(float)
    volume = df["Volume"].values.astype(float) if "Volume" in df.columns else None
    current_price = float(close[-1])

    score = 0
    signals = []
    catalyst_scores = {}
    patterns = []

    # ── 2. D0多模式检测 (启动前期识别) ───────────
    pattern, pattern_score = _detect_pattern(tech, close, volume)
    if pattern:
        patterns.append(pattern)
        catalyst_scores["D0"] = {"name": pattern["mode"], "score": pattern_score}
        if pattern["mode"] in ("coiled_spring", "silent_accumulation"):
            signals.append(f"🔄 {pattern['mode_label']}")
            score += pattern_score

    # ── 3. C1 量价启动 (15) ────────────────────
    c1 = _score_c1_volume_price(tech, close, volume)
    catalyst_scores["C1"] = {"name": "量价启动", "score": c1}
    if c1 >= 10:
        signals.append(f"🔥 量价启动({c1}分)")
    score += c1

    # ── 4. C2 趋势强度 (12) ────────────────────
    c2 = _score_c2_trend(tech, close)
    catalyst_scores["C2"] = {"name": "趋势强度", "score": c2}
    if c2 >= 8:
        signals.append(f"📈 趋势偏强({c2}分)")
    score += c2

    # ── 5. C3 机构进场 (10) ────────────────────
    c3 = _score_c3_institutional(tech, close, volume)
    catalyst_scores["C3"] = {"name": "机构进场", "score": c3}
    if c3 >= 6:
        signals.append(f"🏦 资金关注({c3}分)")
    score += c3

    # ── 6. C4 估值合理 (10) ────────────────────
    c4, pe_ratio = _score_c4_valuation(ticker)
    catalyst_scores["C4"] = {"name": "估值合理", "score": c4, "pe": pe_ratio}
    if c4 >= 6:
        signals.append(f"💰 PE合理({pe_ratio})({c4}分)")
    score += c4

    # ── 7. C5 财报窗口 (10) ────────────────────
    c5, earnings_days = _score_c5_earnings(ticker)
    catalyst_scores["C5"] = {"name": "财报窗口", "score": c5, "days_to": earnings_days}
    if c5 >= 6:
        signals.append(f"📅 {earnings_days}天内财报({c5}分)")
    score += c5

    # ── 8. C6 赛道热度 (10) ────────────────────
    c6, sector_hint = _score_c6_sector(ticker, df)
    catalyst_scores["C6"] = {"name": "赛道热度", "score": c6, "sector": sector_hint}
    if c6 >= 6:
        signals.append(f"⚡ {sector_hint}({c6}分)")
    score += c6

    # ── 9. C7 资金持续流入 (8) ────────────────
    c7 = _score_c7_flow(tech, close, volume)
    catalyst_scores["C7"] = {"name": "资金流入", "score": c7}
    if c7 >= 5:
        signals.append(f"💵 资金持续({c7}分)")
    score += c7

    # ── 10. C8 做空压制释放 (8) ──────────────
    c8 = _score_c8_short_squeeze(ticker, tech, close)
    catalyst_scores["C8"] = {"name": "做空释放", "score": c8}
    if c8 >= 5:
        signals.append(f"🔄 做空压制释放({c8}分)")
    score += c8

    # ── 11. C9 盘前异动 (7) ──────────────────
    c9 = _score_c9_premarket(ticker)
    catalyst_scores["C9"] = {"name": "盘前异动", "score": c9}
    if c9 >= 4:
        signals.append(f"🌅 盘前异动({c9}分)")
    score += c9

    # ── 12. C10 波动弹性 (5) ──────────────────
    c10 = _score_c10_volatility(tech, current_price)
    catalyst_scores["C10"] = {"name": "波动弹性", "score": c10}
    if c10 >= 3:
        signals.append(f"🎯 弹性充足({c10}分)")
    score += c10

    # ── 13. 流动性惩罚 ────────────────────────
    penalty = _liquidity_penalty(volume, current_price, tech)
    if penalty < 0:
        signals.append(f"⚠️ 流动性折价({penalty})")
    score += penalty

    # ── 最终评分（封顶100）────────────────────
    score = max(0, min(100, score))
    rating = _doubler_rating(score)

    return {
        "ticker": ticker,
        "score": score,
        "rating": rating,
        "current_price": round(current_price, 2),
        "patterns": patterns,
        "signals": signals,
        "catalysts": catalyst_scores,
        "D0_mode": pattern["mode"] if pattern else "无",
        "scanned_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


# ═══════════════════════════════════════════════════════════════
# D0 启动前期模式检测
# ═══════════════════════════════════════════════════════════════

def _detect_pattern(tech: dict, close: np.ndarray,
                    volume: Optional[np.ndarray]) -> Tuple[Optional[dict], int]:
    """检测D0多模式，返回(模式, 加分)"""
    patterns = []

    # 1. coiled_spring: 蓄力待发
    cs = _detect_coiled_spring(tech, close, volume)
    if cs:
        patterns.append((cs, 15))

    # 2. silent_accumulation: 默默吸筹
    sa = _detect_silent_accumulation(tech, close, volume)
    if sa:
        patterns.append((sa, 12))

    # 3. early_warming: 早期预热
    ew = _detect_early_warming(tech, close, volume)
    if ew:
        patterns.append((ew, 8))

    # 4. smart_pullback: 聪明回调
    sp = _detect_smart_pullback(tech, close, volume)
    if sp:
        patterns.append((sp, 8))

    if not patterns:
        # 5. danger_pullback: 危险回调（减分）
        dp = _detect_danger_pullback(tech, close)
        if dp:
            patterns.append((dp, -3))

    # 返回得分最高的模式
    if patterns:
        patterns.sort(key=lambda x: x[1], reverse=True)
        return patterns[0]

    return None, 0


def _detect_coiled_spring(tech: dict, close: np.ndarray,
                          volume: Optional[np.ndarray]) -> Optional[dict]:
    """蓄力模式: 前期下跌→近期缩量企稳→开始放量"""
    if len(close) < 30:
        return None

    sma5 = tech.get("sma5")
    sma20 = tech.get("sma20")
    if not sma5 or not sma20:
        return None

    current_price = float(close[-1])
    pv20 = tech.get("price_vs_sma20", 0)

    # 近期缩量（成交量连续萎缩后企稳）
    vol_shrinking = False
    if volume is not None and len(volume) >= 10:
        vol_5d = float(np.mean(volume[-5:]))
        vol_10d = float(np.mean(volume[-10:-5]))
        if vol_10d > 0 and vol_5d / vol_10d < 0.85:
            vol_shrinking = True

    # 价格在SMA20附近（刚站上或微破）
    near_sma20 = -3 < pv20 < 5

    # SMA5开始上穿SMA20（蓄力完成信号）
    sma5_cross = sma5 > sma20

    if near_sma20 and vol_shrinking and sma5_cross:
        return {
            "mode": "coiled_spring",
            "mode_label": "Coiled Spring 蓄力待发",
            "description": "回调缩量企稳+SMA5金叉, 蓄力完成",
        }
    return None


def _detect_silent_accumulation(tech: dict, close: np.ndarray,
                                volume: Optional[np.ndarray]) -> Optional[dict]:
    """默默吸筹: 窄幅盘整15天+成交量温和"""
    if len(close) < SILENT_ACCUM_DAYS + 5:
        return None

    recent = close[-SILENT_ACCUM_DAYS:]
    price_range = (float(np.max(recent)) - float(np.min(recent))) / float(np.mean(recent)) * 100

    # 窄幅盘整 (<10%)
    if price_range > 10:
        return None

    # SMA5 ≈ SMA20 粘合
    sma5 = tech.get("sma5")
    sma20 = tech.get("sma20")
    if not sma5 or not sma20:
        return None

    ma_gap = abs(sma5 / sma20 - 1) * 100
    if ma_gap > 3:
        return None

    # 成交量温和（没有巨量也没有极度缩量）
    vol_even = True
    if volume is not None and len(volume) >= SILENT_ACCUM_DAYS:
        recent_vol = volume[-SILENT_ACCUM_DAYS:].astype(float)
        vol_cv = float(np.std(recent_vol) / np.mean(recent_vol))
        vol_even = vol_cv < 0.6  # 变异系数<0.6表示温和

    if ma_gap < 2 and vol_even:
        return {
            "mode": "silent_accumulation",
            "mode_label": "Silent Accumulation 默默吸筹",
            "description": f"窄幅盘整{price_range:.1f}%+均线粘合+量温和",
        }
    return None


def _detect_early_warming(tech: dict, close: np.ndarray,
                          volume: Optional[np.ndarray]) -> Optional[dict]:
    """早期预热: 量价齐升初段(SMA5刚上穿SMA20+放量)"""
    sma5 = tech.get("sma5")
    sma20 = tech.get("sma20")
    sma60 = tech.get("sma60")
    rsi = tech.get("rsi14")
    vol_ratio = tech.get("volume_ratio", 1)

    if not all([sma5, sma20]):
        return None

    # SMA5 > SMA20 (刚金叉)
    if sma5 <= sma20:
        return None

    # RSI在40-65（未过热）
    if rsi and rsi > 65:
        return None

    # 放量
    if vol_ratio < EARLY_WARMING_MIN_VOL:
        return None

    # 价格在SMA60附近（刚突破或即将突破）
    if sma60:
        current = float(close[-1])
        dist_to_sma60 = (current / sma60 - 1) * 100
        if dist_to_sma60 < -5:
            return None

    return {
        "mode": "early_warming",
        "mode_label": "Early Warming 早期预热",
        "description": f"量价齐升初段(RSI{rsi},量比{vol_ratio:.1f})",
    }


def _detect_smart_pullback(tech: dict, close: np.ndarray,
                           volume: Optional[np.ndarray]) -> Optional[dict]:
    """聪明回调: 上升趋势中的健康回调(缩量回踩MA)"""
    if len(close) < 10:
        return None

    sma20 = tech.get("sma20")
    sma60 = tech.get("sma60")
    rsi = tech.get("rsi14")
    vol_ratio = tech.get("volume_ratio", 1)

    if not sma20:
        return None

    current_price = float(close[-1])

    # MA多头排列 (上升趋势)
    sma5 = tech.get("sma5")
    if sma5 and sma60 and not (sma5 > sma20 > sma60):
        return None

    # 近期回调但幅度有限
    if len(close) >= 5:
        ret_5d = (close[-1] / close[-5] - 1) * 100
        if not (SMART_PULLBACK_MIN_DROP <= ret_5d <= SMART_PULLBACK_MAX_DROP):
            return None

    # 缩量回调
    if vol_ratio and vol_ratio > 1.2:
        return None

    # RSI中性偏弱 (30-50 回调中)
    if rsi and rsi > 55:
        return None

    # 价格在SMA20附近（得到支撑）
    pv20 = tech.get("price_vs_sma20", 0)
    if not (-5 < pv20 < 2):
        return None

    return {
        "mode": "smart_pullback",
        "mode_label": "Smart Pullback 聪明回调",
        "description": "上升趋势中健康缩量回调至SMA20支撑",
    }


def _detect_danger_pullback(tech: dict, close: np.ndarray) -> Optional[dict]:
    """危险回调: 放量下跌破位"""
    if len(close) < 5:
        return None

    sma20 = tech.get("sma20")
    if not sma20:
        return None

    current_price = float(close[-1])
    pv20 = tech.get("price_vs_sma20", 0)
    vol_ratio = tech.get("volume_ratio", 1)

    # 放量跌穿SMA20
    if pv20 < -3 and vol_ratio and vol_ratio > 1.5:
        return {
            "mode": "danger_pullback",
            "mode_label": "Danger Pullback 危险回调",
            "description": f"放量跌穿SMA20({pv20:.1f}%)",
        }

    # 连续下跌破位
    if len(close) >= 3:
        d1 = close[-3] > close[-2]
        d2 = close[-2] > close[-1]
        if d1 and d2:
            return {
                "mode": "danger_pullback",
                "mode_label": "Danger Pullback 危险回调",
                "description": "三连阴破位",
            }

    return None


# ═══════════════════════════════════════════════════════════════
# C1-C10 催化剂评分
# ═══════════════════════════════════════════════════════════════

def _score_c1_volume_price(tech: dict, close: np.ndarray,
                           volume: Optional[np.ndarray]) -> int:
    """C1 量价启动评分 (0-15)"""
    score = 0
    vol_ratio = tech.get("volume_ratio", 1)

    # 成交量放大
    if vol_ratio > 2.0:
        score += 6
    elif vol_ratio > 1.5:
        score += 4
    elif vol_ratio > 1.2:
        score += 2

    # 价格突破信号
    sma5 = tech.get("sma5")
    sma20 = tech.get("sma20")
    if sma5 and sma20 and sma5 > sma20:
        score += 4

    # 近5日上涨
    if len(close) >= 5:
        ret_5d = (close[-1] / close[-5] - 1) * 100
        if 1 <= ret_5d <= 8:
            score += 5
        elif 0 < ret_5d < 1:
            score += 2
        elif ret_5d > 8:
            score += 2  # 过热折半

    return min(15, score)


def _score_c2_trend(tech: dict, close: np.ndarray) -> int:
    """C2 趋势强度评分 (0-12)"""
    score = 0
    sma5 = tech.get("sma5")
    sma20 = tech.get("sma20")
    sma60 = tech.get("sma60")
    sma100 = tech.get("sma100")
    current_price = float(close[-1])

    if sma5 and sma20 and sma60:
        if sma5 > sma20 > sma60:
            score += 5  # 完美多头
        elif sma5 > sma20:
            score += 3

    if sma100 and current_price > sma100:
        score += 3  # 站上牛熊线
    elif sma100 and current_price > sma100 * 0.95:
        score += 1

    if sma60 and current_price > sma60:
        dist = (current_price / sma60 - 1) * 100
        if 2 <= dist <= 20:
            score += 4  # 合理偏离
        elif dist > 20:
            score += 1  # 偏离太大

    return min(12, score)


def _score_c3_institutional(tech: dict, close: np.ndarray,
                            volume: Optional[np.ndarray]) -> int:
    """C3 机构进场评分 (0-10)"""
    score = 0
    vol_ratio = tech.get("volume_ratio", 1)

    # 成交量激增（机构进场特征）
    if vol_ratio > 2.5:
        score += 5
    elif vol_ratio > 1.8:
        score += 3
    elif vol_ratio > 1.3:
        score += 2

    # 连续放量 (近5日量 > 近20日均量)
    if volume is not None and len(volume) >= 25:
        vol_5d = float(np.mean(volume[-5:]))
        vol_20d = float(np.mean(volume[-25:-5]))
        if vol_20d > 0:
            surge = vol_5d / vol_20d
            if surge > 1.3:
                score += 4
            elif surge > 1.1:
                score += 2

    # 布林带突破
    bb_upper = tech.get("bb_upper")
    bb_mid = tech.get("bb_mid")
    if bb_upper and bb_mid and float(close[-1]) > bb_upper:
        score += 3

    # 避免重复计分
    return min(10, score)


def _score_c4_valuation(ticker: str) -> Tuple[int, Optional[float]]:
    """C4 估值合理评分 (0-10)

    美股PE容忍度比A股高:
      PE<15 = 低估(8-10)
      PE 15-25 = 合理(5-7)
      PE 25-40 = 偏高(2-4)
      PE>40 或无PE = 不确定(0-2)
    """
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info
        pe = info.get("trailingPE") or info.get("forwardPE")

        if pe is None or pe <= 0:
            return 2, None

        if pe < 10:
            return 10, round(pe, 1)
        elif pe < 15:
            return 8, round(pe, 1)
        elif pe < 25:
            return 6, round(pe, 1)
        elif pe < 40:
            return 3, round(pe, 1)
        else:
            return 1, round(pe, 1)
    except Exception:
        return 2, None


def _score_c5_earnings(ticker: str) -> Tuple[int, Optional[int]]:
    """C5 财报窗口评分 (0-10)"""
    try:
        import yfinance as yf
        tk = yf.Ticker(ticker)
        cal = tk.calendar
        if cal is None or cal.empty:
            return 0, None

        earnings = cal.get("Earnings Date")
        if earnings is None:
            return 0, None

        if hasattr(earnings, '__iter__') and not isinstance(earnings, str):
            next_date = earnings.iloc[0] if hasattr(earnings, 'iloc') else earnings[0]
        else:
            next_date = earnings

        if not hasattr(next_date, 'date'):
            try:
                from datetime import datetime as dt2
                next_date = dt2.fromisoformat(str(next_date))
            except Exception:
                return 0, None

        now = datetime.now()
        days_to = (next_date - now).days if hasattr(next_date, 'days') else 30

        if days_to < 0:
            return 1, days_to  # 财报已过, 看post-earnings drift
        elif days_to <= 3:
            return 10, days_to  # 爆发前夕
        elif days_to <= 7:
            return 8, days_to
        elif days_to <= 14:
            return 6, days_to
        elif days_to <= 30:
            return 4, days_to
        else:
            return 2, days_to
    except Exception:
        return 0, None


def _score_c6_sector(ticker: str, df: pd.DataFrame) -> Tuple[int, str]:
    """C6 赛道热度评分 (0-10)

    识别热点赛道: AI/半导体/新能源/生物科技/金融科技
    """
    # 热门赛道关键词
    HOT_SECTORS = {
        "AI": ["AI", "INTELLIGENCE", "MACHINE LEARNING", "GPT"],
        "半导体": ["SEMICONDUCTOR", "CHIP", "GPU", "ASIC"],
        "新能源": ["EV", "ELECTRIC", "SOLAR", "RENEWABLE", "BATTERY"],
        "生物科技": ["BIOTECH", "GENETIC", "GENE", "THERAPEUTIC"],
        "金融科技": ["FINTECH", "CRYPTO", "BLOCKCHAIN", "DIGITAL ASSET"],
    }

    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info
        sector = (info.get("sector") or "").upper()
        industry = (info.get("industry") or "").upper()
        long_name = (info.get("longName") or "").upper()
        short_name = (info.get("shortName") or "").upper()

        search_text = f"{sector} {industry} {long_name} {short_name}"

        for sector_name, keywords in HOT_SECTORS.items():
            for kw in keywords:
                if kw in search_text:
                    if sector_name in ("AI", "半导体"):
                        return 10, sector_name
                    elif sector_name in ("新能源", "生物科技"):
                        return 8, sector_name
                    else:
                        return 7, sector_name

        # 大盘/指数ETF不计赛道分
        if ticker in ("SPY", "QQQ", "IWM", "DIA"):
            return 3, "宽基指数"

        return 4, sector[:10] if sector else "普通行业"
    except Exception:
        return 3, "未知"


def _score_c7_flow(tech: dict, close: np.ndarray,
                   volume: Optional[np.ndarray]) -> int:
    """C7 资金流入评分 (0-8)"""
    score = 0

    # 近5日连续上涨
    if len(close) >= 5:
        up_days = sum(1 for i in range(1, 6) if close[-i] > close[-(i+1)])
        if up_days >= 4:
            score += 4
        elif up_days >= 3:
            score += 2

    # 成交量持续放大
    if volume is not None and len(volume) >= 5:
        vol_trend = float(volume[-1]) / float(np.mean(volume[-5:-1])) if float(np.mean(volume[-5:-1])) > 0 else 1
        if vol_trend > 1.2:
            score += 3
        elif vol_trend > 0.9:
            score += 1

    # 日内涨幅配合量
    vol_ratio = tech.get("volume_ratio", 1)
    if len(close) >= 2 and vol_ratio > 1.3:
        ret_1d = (close[-1] / close[-2] - 1) * 100
        if ret_1d > 0:
            score += 2

    return min(8, score)


def _score_c8_short_squeeze(ticker: str, tech: dict,
                            close: np.ndarray) -> int:
    """C8 做空压制释放评分 (0-8)"""
    score = 0

    # 尝试获取做空比例
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info
        short_ratio = info.get("shortRatio")  # 做空比率(天)
        short_pct = info.get("shortPercentOfFloat")  # 流通股做空比例

        if short_pct is not None:
            if short_pct > 0.2:  # >20%被做空
                score += 4
            elif short_pct > 0.1:
                score += 2

        if short_ratio is not None:
            if short_ratio > 5:
                score += 3  # 做空者需要5天以上回补
            elif short_ratio > 2:
                score += 2

        # 高做空+近期未大跌 = 轧空潜力
        if (short_pct and short_pct > 0.15) and len(close) >= 10:
            ret_10d = (close[-1] / close[-10] - 1) * 100
            if ret_10d > -3:  # 没有大跌
                score += 3
    except Exception:
        pass

    # 技术面补充: 没有MACD顶背离
    macd = tech.get("macd")
    if macd and macd.get("macd") is not None and macd.get("signal") is not None:
        if macd["macd"] > macd["signal"]:
            score += 1  # MACD多头支持下做空释放更容易

    return min(8, score)


def _score_c9_premarket(ticker: str) -> int:
    """C9 盘前异动评分 (0-7)"""
    try:
        from haitao.us_fetcher import get_pre_post_market
        prepost = get_pre_post_market()
        if isinstance(prepost, dict):
            pre = prepost.get("premarket", [])
        elif isinstance(prepost, list):
            pre = prepost
        else:
            pre = []

        for item in pre:
            if isinstance(item, dict) and item.get("ticker", "").upper() == ticker:
                change_pct = item.get("change_pct", 0)
                if change_pct is not None:
                    if change_pct > 5:
                        return 7  # 盘前大涨>5%
                    elif change_pct > 3:
                        return 5
                    elif change_pct > 1:
                        return 3
                    elif change_pct > 0:
                        return 1
                break
    except Exception:
        pass

    return 0


def _score_c10_volatility(tech: dict, current_price: float) -> int:
    """C10 波动弹性评分 (0-5)"""
    score = 0
    atr = tech.get("atr14")
    if atr and current_price > 0:
        atr_pct = (atr / current_price) * 100
        if 2 <= atr_pct <= 5:
            score += 4  # 适中波动=健康弹性
        elif 1 <= atr_pct < 2:
            score += 3  # 低波=弹性不足
        elif 5 < atr_pct <= 8:
            score += 2  # 高波=双刃剑
        elif atr_pct > 8:
            score += 1  # 极高波=风险

    # 布林带宽度检查
    bb_upper = tech.get("bb_upper")
    bb_lower = tech.get("bb_lower")
    if bb_upper and bb_lower and bb_upper > bb_lower and current_price > 0:
        bb_width = (bb_upper - bb_lower) / current_price * 100
        if 5 <= bb_width <= 15:
            score += 2  # 合理宽度
        elif bb_width < 5:
            score += 1  # 太窄=将要突破

    return min(5, score)


def _liquidity_penalty(volume: Optional[np.ndarray],
                       current_price: float, tech: dict) -> int:
    """流动性惩罚: 小盘低价股折价"""
    penalty = 0

    # 量流动性
    if volume is not None and len(volume) >= 5:
        avg_vol = float(np.mean(volume[-5:]))
        notional = avg_vol * current_price
        if notional < 5_000_000:  # <$5M日成交额
            penalty -= 8
        elif notional < 20_000_000:
            penalty -= 4
        elif notional < 50_000_000:
            penalty -= 2

    # 低价股（< $5 流动性和波动性风险）
    if current_price < 3:
        penalty -= 5
    elif current_price < 5:
        penalty -= 3
    elif current_price < 10:
        penalty -= 1

    return max(-10, penalty)


# ═══════════════════════════════════════════════════════════════
# 批量扫描
# ═══════════════════════════════════════════════════════════════

def scan_doublers(tickers: List[str] = None) -> List[dict]:
    """批量扫描翻倍潜力股"""
    if not tickers:
        tickers = DOUBLER_SEED_POOL

    results = []
    with ThreadPoolExecutor(max_workers=4) as ex:
        fut_map = {ex.submit(score_doubler, t): t for t in tickers}
        for f in as_completed(fut_map):
            try:
                r = f.result(timeout=30)
                if "error" not in r:
                    results.append(r)
            except Exception as e:
                logger.warning("扫描 %s 失败: %s", fut_map[f], e)

    results.sort(key=lambda x: x.get("score", 0), reverse=True)
    return results


def recommend_doublers() -> dict:
    """生成当月翻倍推荐（缓存版）"""
    cached = _cached("us_doubler_recommend", CACHE_TTL_SCAN)
    if cached is not None:
        return cached

    all_results = scan_doublers()
    elite = [r for r in all_results if r.get("score", 0) >= DOUBLER_SCORE_THRESHOLD]
    watch = [r for r in all_results if DOUBLER_SCORE_THRESHOLD - 10 <= r.get("score", 0) < DOUBLER_SCORE_THRESHOLD]

    result = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "total_scanned": len(all_results),
        "elite_picks": elite[:10],
        "watch_list": watch[:15],
        "patterns_summary": _get_pattern_summary(elite + watch),
        "top_pick": elite[0] if elite else None,
    }

    _set_cache("us_doubler_recommend", result, CACHE_TTL_SCAN)
    return result


def _get_pattern_summary(picks: List[dict]) -> dict:
    """汇总模式分布"""
    modes = {}
    for p in picks:
        mode = p.get("D0_mode", "无")
        if mode not in modes:
            modes[mode] = 0
        modes[mode] += 1
    return modes


# ═══════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════

def _empty_result(ticker: str, reason: str) -> dict:
    return {
        "ticker": ticker, "score": 0, "rating": "数据不足",
        "error": reason, "scanned_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


def _doubler_rating(score: int) -> str:
    if score >= 85:
        return "⭐⭐⭐ 强烈翻倍潜力"
    elif score >= 75:
        return "⭐⭐ 高翻倍潜力"
    elif score >= 65:
        return "⭐ 关注翻倍潜力"
    elif score >= 50:
        return "👀 一般关注"
    else:
        return "⏳ 等待时机"
