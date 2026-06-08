"""
海淘掘金 — 美股 3x 杠杆ETF扫描器
Leveraged ETF Scanner — decay analysis, risk warnings, trend signals

对标拾米交易工作室海淘框架:
  - _get_underlying: 杠杆ETF → 底层资产映射
  - _compute_decay: 月理论收益 vs 实际收益偏差
  - analyze_leveraged: 单只杠杆ETF完整分析
  - scan_leveraged_etfs: 全池扫描排名

核心逻辑:
  杠杆ETF存在波动衰减 (volatility decay / beta slippage):
    实际收益 ≈ (底层收益 × 杠杆倍数) - 衰减
  当衰减超过 LEVERAGED_DECAY_WARN_PCT(2%) 时触发预警，
  提示不适合长期持有 (LEVERAGED_MAX_HOLD_DAYS=5)。
"""
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from haitao.us_fetcher import get_history, calc_technical_indicators
from haitao.config import (
    LEVERAGED_3X_ETFS,
    LEVERAGED_MAX_HOLD_DAYS,
    LEVERAGED_DECAY_WARN_PCT,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# 杠杆ETF → 底层资产映射表
# ═══════════════════════════════════════════════════════════════
#
# (etf_ticker, (underlying, leverage_factor))
#  正数 = 做多型, 负数 = 做空型/反向型
#
LEVERAGED_MAP: Dict[str, Tuple[str, float]] = {
    # ── 纳斯达克100 (QQQ) ──────────────────────
    "TQQQ": ("QQQ", 3.0),     # ProShares UltraPro QQQ (3x bull)
    "SQQQ": ("QQQ", -3.0),    # ProShares UltraPro Short QQQ (3x bear)
    "QLD":  ("QQQ", 2.0),     # ProShares Ultra QQQ (2x bull)
    "PSQ":  ("QQQ", -2.0),    # ProShares Short QQQ (2x bear)

    # ── 标普500 (SPY) ──────────────────────────
    "SPXL": ("SPY", 3.0),     # Direxion Daily S&P500 Bull 3x
    "SPXS": ("SPY", -3.0),    # Direxion Daily S&P500 Bear 3x
    "SSO":  ("SPY", 2.0),     # ProShares Ultra S&P500 (2x bull)
    "SDS":  ("SPY", -2.0),    # ProShares UltraShort S&P500 (2x bear)

    # ── 罗素2000 (IWM) ─────────────────────────
    "TNA":  ("IWM", 3.0),     # Direxion Daily Small Cap Bull 3x
    "TZA":  ("IWM", -3.0),    # Direxion Daily Small Cap Bear 3x
    "UDOW": ("IWM", 3.0),     # ProShares UltraPro Dow30 (3x bull)
    "SDOW": ("IWM", -3.0),    # ProShares UltraPro Short Dow30 (3x bear)

    # ── 费城半导体 (SOXX) ──────────────────────
    "SOXL": ("SOXX", 3.0),    # Direxion Daily Semiconductor Bull 3x
    "SOXS": ("SOXX", -3.0),   # Direxion Daily Semiconductor Bear 3x

    # ── 金融板块 (XLF) ─────────────────────────
    "FAS":  ("XLF", 3.0),     # Direxion Daily Financial Bull 3x
    "FAZ":  ("XLF", -3.0),    # Direxion Daily Financial Bear 3x

    # ── 生物科技 (XBI) ─────────────────────────
    "LABU": ("XBI", 3.0),     # Direxion Daily S&P Biotech Bull 3x
    "LABD": ("XBI", -3.0),    # Direxion Daily S&P Biotech Bear 3x

    # ── 金矿指数 (GDX) ─────────────────────────
    "JNUG": ("GDX", 2.0),     # Direxion Daily Junior Gold Miners Bull 2x
    "JDST": ("GDX", -2.0),    # Direxion Daily Junior Gold Miners Bear 2x
    "NUGT": ("GDX", 2.0),     # Direxion Daily Gold Miners Bull 2x
    "DUST": ("GDX", -2.0),    # Direxion Daily Gold Miners Bear 2x

    # ── 能源板块 (XLE) ─────────────────────────
    "DRIP": ("XLE", -3.0),    # Direxion Daily Energy Bear 3x
    "ERX":  ("XLE", 3.0),     # Direxion Daily Energy Bull 3x
    "ERY":  ("XLE", -3.0),    # Direxion Daily Energy Bear 3x
}


def _get_underlying(ticker: str) -> Tuple[str, float]:
    """获取杠杆ETF对应的底层资产和杠杆倍数

    Args:
        ticker: 杠杆ETF代码 (如 'TQQQ')

    Returns:
        (underlying_ticker, leverage_factor)
        例: ("QQQ", 3.0)  TQQQ → QQQ 3x做多
             ("QQQ", -3.0) SQQQ → QQQ 3x做空
             ("GDX", 2.0)  JNUG → GDX 2x做多

    Raises:
        ValueError: ticker不在已知映射中
    """
    ticker = ticker.upper().strip()
    if ticker not in LEVERAGED_MAP:
        raise ValueError(f"未知的杠杆ETF: {ticker}，支持的: {list(LEVERAGED_MAP.keys())}")
    return LEVERAGED_MAP[ticker]


def _compute_decay(
    ticker: str,
    underlying: str,
    df_etf: pd.DataFrame,
    df_und: pd.DataFrame,
) -> dict:
    """计算杠杆ETF的波动衰减 (volatility decay)

    衰减 = 实际收益 - (底层收益 × 杠杆倍数)
    正值表示超额收益（跟踪良好）,
    负值表示存在衰减（偏离理论值）。

    Args:
        ticker: 杠杆ETF代码
        underlying: 底层资产代码
        df_etf:  杠杆ETF的近1月日线DataFrame
        df_und: 底层资产的近1月日线DataFrame

    Returns:
        dict with:
          - ticker, underlying
          - leverage: 杠杆倍数
          - etf_return_pct: ETF实际收益率(%)
          - underlying_return_pct: 底层收益率(%)
          - theoretical_return_pct: 理论收益率(底层×杠杆)(%)
          - decay_pct: 衰减值(%)
          - decay_warning: bool 是否触发预警
          - tracking_quality: str ('良好'/'轻微偏离'/'严重衰减')
          - max_drawdown_pct: ETF区间最大回撤(%)
          - volatility_ratio: ETF波动率/底层波动率
          - data_days: 有效交易日数
    """
    try:
        close_etf = df_etf["Close"].values.astype(float)
        close_und = df_und["Close"].values.astype(float)
    except (KeyError, TypeError, IndexError):
        return {"ticker": ticker, "error": "数据列缺失", "decay_pct": None}

    if len(close_etf) < 5 or len(close_und) < 5:
        return {"ticker": ticker, "error": "数据不足", "decay_pct": None}

    # 同步长度（取较短者）
    n = min(len(close_etf), len(close_und))
    close_etf = close_etf[-n:]
    close_und = close_und[-n:]

    # ── 1. 实际收益率 ─────────────────────────
    etf_return_pct = (close_etf[-1] / close_etf[0] - 1) * 100.0
    underlying_return_pct = (close_und[-1] / close_und[0] - 1) * 100.0

    # ── 2. 杠杆倍数 ───────────────────────────
    _, leverage = _get_underlying(ticker)

    # ── 3. 理论收益率 ─────────────────────────
    theoretical_return_pct = underlying_return_pct * leverage

    # ── 4. 衰减 ───────────────────────────────
    decay_pct = etf_return_pct - theoretical_return_pct

    # ── 5. 日波动均值对比（衡量跟踪偏差的波动）───
    daily_etf = np.diff(close_etf) / close_etf[:-1] * 100.0
    daily_und = np.diff(close_und) / close_und[:-1] * 100.0
    vol_etf = float(np.std(daily_etf, ddof=1))
    vol_und = float(np.std(daily_und, ddof=1))
    vol_ratio = vol_etf / vol_und if vol_und > 0 else 0.0

    # ── 6. ETF最大回撤 ────────────────────────
    peak = np.maximum.accumulate(close_etf)
    drawdown = (close_etf / peak - 1) * 100.0
    max_dd = float(np.min(drawdown))

    # ── 7. 跟踪质量判断 ──────────────────────
    abs_decay = abs(decay_pct)
    decay_warning = abs_decay > LEVERAGED_DECAY_WARN_PCT

    if abs_decay <= LEVERAGED_DECAY_WARN_PCT:
        tracking_quality = "良好"
    elif abs_decay <= LEVERAGED_DECAY_WARN_PCT * 2:
        tracking_quality = "轻微偏离"
    else:
        tracking_quality = "严重衰减"

    return {
        "ticker": ticker,
        "underlying": underlying,
        "leverage": leverage,
        "data_days": n,
        "etf_return_pct": round(etf_return_pct, 2),
        "underlying_return_pct": round(underlying_return_pct, 2),
        "theoretical_return_pct": round(theoretical_return_pct, 2),
        "decay_pct": round(decay_pct, 2),
        "decay_warning": decay_warning,
        "tracking_quality": tracking_quality,
        "max_drawdown_pct": round(max_dd, 2),
        "volatility_ratio": round(vol_ratio, 3),
    }


def analyze_leveraged(ticker: str) -> dict:
    """分析单只杠杆ETF的完整状态

    包含:
      - 底层资产信息 (underlying, leverage)
      - 1月收益与衰减分析
      - 技术指标 (趋势, 超买超卖)
      - 持有建议 (基于衰减和趋势)

    Args:
        ticker: 杠杆ETF代码

    Returns:
        dict with comprehensive analysis
    """
    ticker = ticker.upper().strip()

    # ── 1. 获取映射 ─────────────────────────────
    try:
        underlying, leverage = _get_underlying(ticker)
    except ValueError as e:
        return {"ticker": ticker, "error": str(e)}

    result = {
        "ticker": ticker,
        "underlying": underlying,
        "leverage": leverage,
        "max_hold_days": LEVERAGED_MAX_HOLD_DAYS,
    }

    # ── 2. 获取数据 ─────────────────────────────
    df_etf = get_history(ticker, days=30)
    df_und = get_history(underlying, days=30)

    if df_etf is None or len(df_etf) < 5:
        result["error"] = f"ETF数据不足: {ticker}"
        return result
    if df_und is None or len(df_und) < 5:
        result["error"] = f"底层数据不足: {underlying}"
        return result

    # ── 3. 衰减分析 ─────────────────────────────
    decay = _compute_decay(ticker, underlying, df_etf, df_und)
    result.update(decay)

    # ── 4. 技术指标 ─────────────────────────────
    tech = calc_technical_indicators(df_etf)
    if tech:
        # 当前价格 vs MA
        close_vals = df_etf["Close"].values.astype(float)
        current_price = float(close_vals[-1])
        result["current_price"] = round(current_price, 2)

        # MA位置
        if "MA20" in tech:
            result["ma20"] = round(float(tech["MA20"]), 2)
            result["above_ma20"] = current_price > float(tech["MA20"])
        if "MA50" in tech:
            result["ma50"] = round(float(tech["MA50"]), 2)
            result["above_ma50"] = current_price > float(tech["MA50"])

        # RSI (超买超卖)
        if "RSI" in tech:
            rsi = float(tech["RSI"])
            result["rsi"] = round(rsi, 1)
            if rsi > 70:
                result["rsi_signal"] = "超买"
            elif rsi < 30:
                result["rsi_signal"] = "超卖"
            else:
                result["rsi_signal"] = "中性"

        # 成交量变化
        if "Volume" in df_etf.columns:
            vol = df_etf["Volume"].values.astype(float)
            vol_ma = np.mean(vol[-20:]) if len(vol) >= 20 else np.mean(vol)
            vol_recent = np.mean(vol[-5:])
            if vol_ma > 0:
                result["vol_ratio"] = round(float(vol_recent / vol_ma), 2)

    # ── 5. 持有建议 ─────────────────────────────
    advices = []
    if result.get("decay_warning"):
        advices.append("⚠️ 衰减过大，建议仅日内/隔夜交易")
    elif leverage is not None and abs(leverage) >= 3:
        advices.append(f"📊 {int(abs(leverage))}x杠杆: 仅适合短线(<5天)")

    if result.get("rsi_signal") == "超买":
        advices.append("📈 RSI超买，做多型慎追高")
    elif result.get("rsi_signal") == "超卖":
        if leverage and leverage > 0:
            advices.append("📉 RSI超卖，做多型可关注反弹")
        else:
            advices.append("📉 RSI超卖，做空型注意轧空风险")

    result["advice"] = advices if advices else ["✅ 跟踪正常，按策略执行"]
    result["hold_days_caution"] = f"建议持仓≤{LEVERAGED_MAX_HOLD_DAYS}天避免衰减侵蚀"

    return result


def scan_leveraged_etfs(filters: Optional[dict] = None) -> dict:
    """扫描全库杠杆ETF，按衰减/趋势/风险排名

    Args:
        filters: 可选过滤条件
            - underlying: str  只扫描指定底层资产 (如 'QQQ')
            - min_leverage: float  最小杠杆倍数 (如 2.0)
            - direction: str  'bull' (做多) / 'bear' (做空)
            - sort_by: str  'decay' / 'etf_return' / 'underlying_return'
            - sort_asc: bool  是否升序 (默认False=降序)
            - decay_warning_only: bool  只显示有衰减预警的 (默认False)

    Returns:
        dict with:
          - timestamp: 扫描时间
          - total_scanned: 扫描ETF数量
          - warnings: 衰减预警数量
          - results: List[dict] 每只ETF的分析
          - filter_applied: dict 使用的过滤条件
          - market_summary: dict 各底层资产概览
    """
    filters = filters or {}
    tickers = list(LEVERAGED_MAP.keys())

    results = []
    errors = []

    for ticker in tickers:
        try:
            analysis = analyze_leveraged(ticker)
            if "error" in analysis:
                errors.append({"ticker": ticker, "error": analysis["error"]})
                continue

            # ── 应用过滤 ─────────────────────────
            underlying_filter = filters.get("underlying")
            if underlying_filter and analysis.get("underlying", "").upper() != underlying_filter.upper():
                continue

            min_leverage = filters.get("min_leverage")
            if min_leverage is not None:
                lev = analysis.get("leverage", 0)
                if abs(lev) < min_leverage:
                    continue

            direction = filters.get("direction", "").lower()
            if direction == "bull" and analysis.get("leverage", 0) < 0:
                continue
            if direction == "bear" and analysis.get("leverage", 0) > 0:
                continue

            decay_warning_only = filters.get("decay_warning_only", False)
            if decay_warning_only and not analysis.get("decay_warning", False):
                continue

            results.append(analysis)

        except Exception as e:
            logger.warning(f"扫描 {ticker} 失败: {e}")
            errors.append({"ticker": ticker, "error": str(e)})

    # ── 排序 ─────────────────────────────────────
    sort_by = filters.get("sort_by", "decay")
    sort_asc = filters.get("sort_asc", False)

    valid_sort_keys = {"decay", "etf_return", "underlying_return", "theoretical_return",
                       "decay_pct", "etf_return_pct", "underlying_return_pct",
                       "volatility_ratio", "max_drawdown_pct"}

    if sort_by in valid_sort_keys:
        results.sort(
            key=lambda r: r.get(sort_by if sort_by.endswith("_pct") or sort_by in ("volatility_ratio", "max_drawdown_pct")
                                else sort_by + "_pct", 0) or 0,
            reverse=not sort_asc,
        )

    # ── 底层资产汇总 ───────────────────────────
    underlyings_seen = {}
    for r in results:
        und = r.get("underlying", "?")
        if und not in underlyings_seen:
            underlyings_seen[und] = {"count": 0, "etfs": [], "avg_decay": 0.0}
        underlyings_seen[und]["count"] += 1
        underlyings_seen[und]["etfs"].append(r["ticker"])
        if r.get("decay_pct") is not None:
            underlyings_seen[und]["avg_decay"] += r["decay_pct"]

    for und, info in underlyings_seen.items():
        if info["count"] > 0:
            info["avg_decay"] = round(info["avg_decay"] / info["count"], 2)

    # ── 统计 ─────────────────────────────────────
    warnings_count = sum(1 for r in results if r.get("decay_warning"))
    severe_decay = sum(1 for r in results if r.get("tracking_quality") == "严重衰减")

    return {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_scanned": len(tickers),
        "results_count": len(results),
        "errors_count": len(errors),
        "warnings": {
            "decay_warning": warnings_count,
            "severe_decay": severe_decay,
            "no_issues": len(results) - warnings_count,
        },
        "results": results,
        "errors": errors if errors else None,
        "filter_applied": {
            k: v for k, v in filters.items() if v is not None
        } if filters else None,
        "market_summary": {
            und: {
                "count": info["count"],
                "etfs": info["etfs"],
                "avg_decay_pct": info["avg_decay"],
            }
            for und, info in underlyings_seen.items()
        },
        "hold_days_limit": LEVERAGED_MAX_HOLD_DAYS,
        "decay_warn_threshold_pct": LEVERAGED_DECAY_WARN_PCT,
    }


# ═══════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════

def list_supported_etfs() -> dict:
    """列出所有支持的杠杆ETF及其映射信息"""
    groups = {}
    for ticker, (underlying, leverage) in sorted(LEVERAGED_MAP.items()):
        direction = "做多" if leverage > 0 else "做空"
        label = f"{int(abs(leverage))}x{direction}"
        if underlying not in groups:
            groups[underlying] = {"underlying": underlying, "etfs": []}
        groups[underlying]["etfs"].append({
            "ticker": ticker,
            "leverage": leverage,
            "direction": direction,
            "label": label,
        })
    return {
        "total": len(LEVERAGED_MAP),
        "underlying_groups": len(groups),
        "groups": {und: info for und, info in sorted(groups.items())},
    }
