"""回测绩效指标 — 纯计算模块 (无项目内依赖, 无 IO)。

移植自 HKUDS/Vibe-Trading (MIT License) `agent/backtest/metrics.py`:
  来源: https://github.com/HKUDS/Vibe-Trading/blob/main/agent/backtest/metrics.py
  上游版权: Copyright (c) HKUDS, MIT License
  (许可证全文见 https://github.com/HKUDS/Vibe-Trading/blob/main/LICENSE)

本地化适配 (计算公式与上游逐字一致, 仅做接口裁剪):
- 去除 `from backtest.models import FillRecord, TradeRecord` 依赖; 成交/交易记录
  改用 dict (duck-typing): trades 需含 pnl/holding_bars/symbol 键, 换手用记录另需
  entry_time/exit_time/entry_margin/exit_margin 键。
- `calc_bars_per_year` 的年化查找表裁剪到本项目实际使用的区间 (1D/1W/1M 与
  tushare 默认 252); 上游完整多周期×多数据源表见原文件。
- 未移植 `effective_bars_per_year` / `buy_and_hold_return` / `by_exit_reason_stats`
  / `calc_fill_turnover_series` (本项目未使用)。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

# ─── Annualisation factor mapping (裁剪: 仅 1D/1W/1M + tushare 252) ───

_TRADING_DAYS = {
    "tushare": 252, "akshare": 252, "baostock": 252,
    "yfinance": 252, "yahoo": 252,
}

_BARS_PER_DAY = {
    "1D": {"tushare": 1, "akshare": 1, "baostock": 1, "yfinance": 1, "yahoo": 1},
}

# Weekly and monthly bars count calendar periods, whatever the market's
# trading days: every week and every month holds one bar (#1479).
_CALENDAR_BARS_PER_YEAR = {"1W": 52, "1M": 12}

# Runner/loaders also emit these aliases; map them onto the table keys above.
_SOURCE_ALIASES = {"yahoo": "yfinance"}


def _normalize_interval(interval: str) -> str:
    """Map project interval tokens onto ``_BARS_PER_DAY`` keys (上游逐字)。

    ``1M`` is a month and is matched before the case fold, which would read
    it as a minute (#1479).
    """
    token = str(interval or "1D").strip()
    if token == "1M":
        return token
    lower = token.lower()
    if lower == "1w":
        return "1W"
    if lower in ("1m", "5m", "15m", "30m"):
        return lower
    if lower in ("1h", "4h", "1d"):
        return lower.upper()
    return token


def calc_bars_per_year(interval: str = "1D", source: str = "tushare") -> int:
    """Number of bars per year for annualisation (上游逐字逻辑, 表已裁剪)。

    Defaults to 252 days, 1 bar/day when source is missing from the table.
    """
    interval_key = _normalize_interval(interval)
    if interval_key in _CALENDAR_BARS_PER_YEAR:
        return _CALENDAR_BARS_PER_YEAR[interval_key]
    source_key = str(source or "").strip().lower()
    source_key = _SOURCE_ALIASES.get(source_key, source_key)
    trading_days = _TRADING_DAYS.get(source_key, 252)
    bars_per_day = _BARS_PER_DAY.get(interval_key, {}).get(source_key, 1)
    return trading_days * bars_per_day


# ─── Sign-safe returns ───

_log = logging.getLogger(__name__)


def bar_returns(close: Any, *, label: str = "") -> Any:
    """Per-bar simple returns, defined only where the prior price is positive
    (上游逐字; 见上游 docstring / issue #872 说明)。

    A return is defined only when the previous price is finite and strictly
    positive; otherwise it is undefined and reported as ``0.0``, never
    ``inf`` or ``nan``. For an all-positive series this is identical to
    ``pct_change().fillna(0.0)``. The prior price is carried forward across
    gaps (explicit ffill, matching ``pct_change``'s old pad default).
    """
    # ffill *then* shift: the divisor is the last known price, not NaN.
    prev = close.ffill().shift(1)
    # ``inf > 0`` is True, so finiteness has to be asserted, not assumed.
    usable_prev = np.isfinite(prev) & (prev > 0)
    positive_prev = prev.where(usable_prev)

    undefined = int((prev.notna() & ~usable_prev).to_numpy().sum())
    if undefined:
        _log.warning(
            "%s: %d bar(s) follow a non-positive or non-finite prior price; "
            "their return is undefined and reported as 0.0 (issue #872)",
            label or "returns",
            undefined,
        )

    # ``close / prev - 1`` is exactly the expression ``pct_change`` uses, kept
    # verbatim so results for positive series are bit-identical.
    ret = close / positive_prev - 1
    return ret.replace([np.inf, -np.inf], np.nan).fillna(0.0)


def win_rate_and_stats(trades: List[dict]) -> Dict[str, float]:
    """Win rate and P&L statistics from completed trades (上游逐字逻辑)。

    适配: TradeRecord.pnl / .holding_bars → dict 键访问。
    """
    if not trades:
        return {
            "win_rate": 0.0,
            "profit_loss_ratio": 0.0,
            "max_consecutive_loss": 0,
            "avg_holding_bars": 0.0,
            "profit_factor": 0.0,
        }

    wins = [t["pnl"] for t in trades if t["pnl"] > 0]
    losses = [t["pnl"] for t in trades if t["pnl"] < 0]

    win_rate = len(wins) / len(trades)

    avg_win = float(np.mean(wins)) if wins else 0.0
    avg_loss = abs(float(np.mean(losses))) if losses else 1e-10
    profit_loss_ratio = avg_win / avg_loss if avg_loss > 1e-10 else 0.0

    gross_profit = sum(wins) if wins else 0.0
    gross_loss = abs(sum(losses)) if losses else 1e-10
    profit_factor = gross_profit / gross_loss if gross_loss > 1e-10 else 0.0

    max_consec = 0
    cur_consec = 0
    for t in trades:
        if t["pnl"] < 0:
            cur_consec += 1
            max_consec = max(max_consec, cur_consec)
        else:
            cur_consec = 0

    hold_bars = [t["holding_bars"] for t in trades if t["holding_bars"] > 0]
    avg_holding = float(np.mean(hold_bars)) if hold_bars else 0.0

    return {
        "win_rate": win_rate,
        "profit_loss_ratio": round(profit_loss_ratio, 4),
        "max_consecutive_loss": max_consec,
        "avg_holding_bars": round(avg_holding, 1),
        "profit_factor": round(profit_factor, 4),
    }


def by_symbol_stats(trades: List[dict]) -> Dict[str, Dict[str, Any]]:
    """Per-symbol trade statistics (上游逐字逻辑, dict 适配)。"""
    groups: Dict[str, list] = {}
    for t in trades:
        groups.setdefault(t["symbol"], []).append(t)

    result = {}
    for sym, sym_trades in groups.items():
        pnls = [t["pnl"] for t in sym_trades]
        wins = [p for p in pnls if p > 0]
        result[sym] = {
            "count": len(sym_trades),
            "win_rate": round(len(wins) / len(sym_trades), 4) if sym_trades else 0.0,
            "total_pnl": round(sum(pnls), 2),
            "avg_pnl": round(float(np.mean(pnls)), 2) if pnls else 0.0,
        }
    return result


def calc_turnover_series(positions: pd.DataFrame) -> pd.Series:
    """Per-bar weight-implied portfolio turnover from a position frame
    (上游逐字)。

    Turnover for a bar is ``0.5 * sum_i |w_{t,i} - w_{t-1,i}|``, so a full
    rotation from one asset to another counts as 1.0. The first bar's
    turnover is ``0.5 * sum_i |w_{0,i}|`` (entry from cash).
    """
    if positions is None or positions.empty:
        return pd.Series(dtype=float)
    filled = positions.fillna(0.0)
    prev = filled.shift(1).fillna(0.0)
    return 0.5 * (filled - prev).abs().sum(axis=1)


def calc_trade_turnover_series(
    trades: List[dict],
    equity_curve: pd.Series,
) -> pd.Series:
    """Per-bar turnover from actual entry and exit allocations (上游逐字逻辑)。

    Each filled leg contributes its margin-equivalent traded value; dividing
    gross traded value by twice the portfolio equity preserves the convention:
    entering a 100% allocation counts as 0.5, a full rotation counts as 1.0.

    适配: trade.entry_time/.exit_time/.entry_margin/.exit_margin → dict 键。
    """
    if equity_curve is None or equity_curve.empty:
        return pd.Series(dtype=float)

    traded_margin = pd.Series(0.0, index=equity_curve.index, dtype=float)
    for trade in trades:
        for timestamp, margin in (
            (trade.get("entry_time"), trade.get("entry_margin")),
            (trade.get("exit_time"), trade.get("exit_margin")),
        ):
            try:
                margin_value = float(margin)
            except (TypeError, ValueError):
                continue
            if (
                timestamp in traded_margin.index
                and np.isfinite(margin_value)
                and margin_value > 0
            ):
                traded_margin.loc[timestamp] += margin_value

    denominator = 2.0 * equity_curve.abs().replace(0.0, np.nan)
    return (traded_margin / denominator).replace([np.inf, -np.inf], np.nan).fillna(0.0)


def calc_metrics(
    equity_curve: pd.Series,
    trades: List[dict],
    initial_cash: float,
    bars_per_year: Optional[int] = 252,
    bench_ret: Optional[pd.Series] = None,
    positions: Optional[pd.DataFrame] = None,
    turnover_series: Optional[pd.Series] = None,
) -> Dict[str, Any]:
    """Full set of performance metrics (上游逐字逻辑, dict trades 适配)。

    bars_per_year=None 的跨市场日历年自动检测在本移植中不支持 (本地面板恒为
    A股日线 252); 传 None 会回落到 252。
    """
    if len(equity_curve) == 0:
        return _empty_metrics(initial_cash)

    n = len(equity_curve)

    bpy = bars_per_year if bars_per_year is not None else 252

    port_ret = equity_curve.pct_change().fillna(0.0)
    returns_finite = bool(np.isfinite(port_ret.to_numpy(dtype=float, copy=False)).all())

    total_ret = float(equity_curve.iloc[-1] / initial_cash - 1)
    # A leveraged/short book can end at or below zero equity; a total wipeout
    # annualises to -100%.
    growth = 1 + total_ret
    if growth <= 0:
        ann_ret = -1.0
    else:
        try:
            ann_ret = float(growth ** (bpy / max(n, 1)) - 1)
        except OverflowError:
            ann_ret = float("inf")
    if not np.isfinite(ann_ret):
        ann_ret = float("inf")
    # ``Series.std()`` uses ddof=1, so a single-observation return series
    # yields NaN and poisons the Sharpe ratio. Guard the small sample.
    vol = float(port_ret.std()) if len(port_ret) > 1 and returns_finite else 0.0
    sharpe = (
        float(port_ret.mean() / (vol + 1e-10) * np.sqrt(bpy))
        if returns_finite
        else 0.0
    )
    if not np.isfinite(sharpe):
        sharpe = 0.0

    # Drawdown: the account starts at ``initial_cash`` before the first
    # recorded bar, so that value is the initial high-water mark.
    peak = equity_curve.cummax().clip(lower=float(initial_cash))
    dd = (equity_curve - peak) / peak.replace(0, 1)
    max_dd = float(dd.min())

    calmar = ann_ret / abs(max_dd) if abs(max_dd) > 1e-10 else 0.0

    # Sortino
    if returns_finite:
        downside = port_ret[port_ret < 0]
        downside_std = float(downside.std()) if len(downside) > 1 else 1e-10
        sortino = float(port_ret.mean() / (downside_std + 1e-10) * np.sqrt(bpy))
    else:
        sortino = 0.0
    if not np.isfinite(sortino):
        sortino = 0.0

    trade_stats = win_rate_and_stats(trades)

    # Prefer execution-derived turnover; retain the position-frame fallback.
    turnover_values = (
        turnover_series.reindex(equity_curve.index).fillna(0.0).clip(lower=0.0)
        if turnover_series is not None
        else calc_turnover_series(positions)
        if positions is not None
        else pd.Series(dtype=float)
    )
    avg_turnover = float(turnover_values.mean()) if len(turnover_values) > 0 else 0.0
    total_turnover = float(turnover_values.sum()) if len(turnover_values) > 0 else 0.0

    # Benchmark comparison
    bench_return = 0.0
    excess = 0.0
    ir = 0.0
    tracking_error = 0.0
    bench_beta = 0.0
    if bench_ret is not None and len(bench_ret) > 0:
        bench_return = float((1 + bench_ret).prod() - 1)
        excess = total_ret - bench_return
        aligned_bench = bench_ret.reindex(port_ret.index).fillna(0.0)
        active_ret = port_ret - aligned_bench
        active_std = float(active_ret.std()) if len(active_ret) > 1 and returns_finite else 0.0
        ir = (
            float(active_ret.mean() / (active_std + 1e-10) * np.sqrt(bpy))
            if returns_finite
            else 0.0
        )
        if not np.isfinite(ir):
            ir = 0.0
        tracking_error = active_std * np.sqrt(bpy) if returns_finite else 0.0
        if not np.isfinite(tracking_error):
            tracking_error = 0.0
        bench_var = float(aligned_bench.var()) if len(aligned_bench) > 1 else 0.0
        if returns_finite and bench_var > 0:
            covariance = float(port_ret.cov(aligned_bench))
            bench_beta = covariance / bench_var
        if not np.isfinite(bench_beta):
            bench_beta = 0.0

    return {
        "final_value": float(equity_curve.iloc[-1]),
        "total_return": total_ret,
        "annual_return": ann_ret,
        "max_drawdown": max_dd,
        "sharpe": sharpe,
        "calmar": round(calmar, 4),
        "sortino": round(sortino, 4),
        "win_rate": trade_stats["win_rate"],
        "profit_loss_ratio": trade_stats["profit_loss_ratio"],
        "profit_factor": trade_stats["profit_factor"],
        "max_consecutive_loss": trade_stats["max_consecutive_loss"],
        "avg_holding_days": trade_stats["avg_holding_bars"],
        "trade_count": len(trades),
        "benchmark_return": round(bench_return, 6),
        "excess_return": round(excess, 6),
        "information_ratio": round(ir, 4),
        "tracking_error": round(float(tracking_error), 6),
        "benchmark_beta": round(float(bench_beta), 4),
        "avg_turnover": round(avg_turnover, 6),
        "total_turnover": round(total_turnover, 6),
    }


def _empty_metrics(initial_cash: float) -> Dict[str, Any]:
    """Return zero-valued metrics when no data is available (上游逐字)。"""
    return {
        "final_value": initial_cash,
        "total_return": 0, "annual_return": 0, "max_drawdown": 0,
        "sharpe": 0, "calmar": 0, "sortino": 0,
        "win_rate": 0, "profit_loss_ratio": 0, "profit_factor": 0,
        "max_consecutive_loss": 0, "avg_holding_days": 0, "trade_count": 0,
        "benchmark_return": 0, "excess_return": 0, "information_ratio": 0,
        "tracking_error": 0.0, "benchmark_beta": 0.0,
        "avg_turnover": 0.0, "total_turnover": 0.0,
    }
