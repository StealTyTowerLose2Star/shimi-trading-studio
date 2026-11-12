"""策略信号区间回测 (本地真实只读模式) — 防未来函数口径。

2026-10-27 启用 (操作者授权范围: 三策略扫描增加「回测」视图):
- 数据源: 复用 local_tushare_data 全市场日线面板 (kline_panel:150, tushare 日线 EOD,
  与线上扫描同一面板与快照缓存, 不新增出站域名);
  面板深度规范化为最近 150 个已发布开市日 — 回测窗口受此限制, 输出中显式标注。
- 信号口径: 与 services/strategy.py 线上扫描同一计算路径 — 预筛选 (排除北证/剔除
  当前名录ST/涨跌幅区间/成交额近似排序取头部) 后, 调用 realtime_scorer 的纯数据
  评分函数 (trend_detect_df / hybrid_score_df / dragon_leader_score_df) 对
  截至信号日 T 的 K线切片评分 (与线上 get_kline(days=120/60) 等长的尾部切片)。
- 防未来函数: 信号日 T 仅用 ≤T 的数据; 成交价 = T+1 开市日开盘价 (T+1 停牌则信号
  作废跳过, 不向后顺延 stale 信号); 固定持有 H 个交易日, 于 T+H+1 开市日开盘价卖出
  (当日停牌顺延至复牌首日开盘价; 窗口尽头仍未复牌则按最后收盘浮动计值, 不计入
  已完成交易)。
- 组合规则 (研究用简化, 输出中标注): 每笔定额 = 初始资金/top_n (等权, 不复利),
  现金不足定额或标的已持仓则跳过; 允许零碎股 (仅用于指标, 不代表现实可成交);
  手续费口径假设: 买入 0.025% (佣金万2.5), 卖出 0.075% (佣金万2.5+印花税万5)。
- 指标: services/backtest_metrics (HKUDS/Vibe-Trading MIT 移植), 年化 252。
- 成交额近似: 面板无 amount 列, 预筛选排序用 vol×close (量×价单调近似), 已标注。
- ST 覆盖状态 (2026-10-27 验收返修): 响应带 st_filter=applied|degraded_no_namelist
  + st_note — 名录缺失时回测仍返回但显式声明未筛 ST, 不冒充已过滤;
  ST 名单为当前名录回填历史, 存在时点偏差, notes 保留「不宣称整体无未来信息」口径。
- 仅研究用途, 不构成投资建议; 券商交易/自动下单/推送保持永禁。
"""
import time

import numpy as np
import pandas as pd

from realtime_scorer import trend_detect_df, hybrid_score_df, dragon_leader_score_df
from services.backtest_metrics import (
    calc_metrics, calc_trade_turnover_series, calc_bars_per_year,
)

FEE_BUY = 0.00025    # 买入佣金 万2.5 (口径假设)
FEE_SELL = 0.00075   # 卖出佣金 万2.5 + 印花税 万5
INITIAL_CASH = 1_000_000.0
PANEL_DAYS = 150     # 全市场面板规范化深度 (real_get_kline_batch 同值)

_STRATEGIES = ("trend", "hybrid", "dragon")

# 与 services/strategy.py 三扫描的预筛选/阈值逐条对应
_SIGNAL_SPEC = {
    "trend": {
        "lookback": 120, "warmup": 60, "prescreen_top": 80,
        "pct_lo": 1.0, "pct_hi": 9.0, "fallback_lo": 0.5,
        "prescreen_sort": "amount",     # 按成交额近似 (vol×close) 降序
        "score_fn": trend_detect_df, "score_key": "total_score",
        "threshold": 30.0, "picked_top": 25,
        "max_prior_gain_pct": None,
        "require_index_above_ma": None,
        "require_index_ma_rising": None,  # 额外要求MA_N较N日前上升(趋势强化,过滤震荡)
        "require_index_gain_20d": None,   # 强趋势过滤: 指数近20日涨幅不足此值(如0.03)暂停开仓
        "stop_loss_pct": None,        # E1: 如 -0.07 浮亏7%次日开盘提前卖出
        "exit_below_ma": None,        # E2: 如 20 收盘跌破MA20次日开盘提前卖出
        "breakout_no_pullback": None, # E3: True=信号日最低价曾破前高(20日)则放弃
        "vol_price_confirm": None,    # E4: True=阴线不入选(放量须配阳线)
        "max_atr_pct": None,          # ATR14/收盘价% 超过则放弃(波动率过滤)
        "trailing_activate_pct": None, # 移动止损: 浮盈达此值后启用 (如0.06)
        "trailing_retrace_pct": None,  # 移动止损: 最高收盘回撤此值次日离场 (如0.05)   # I3 假设2: 设值后排除信号日前60日涨幅超标的标的
    },
    "hybrid": {
        "lookback": 120, "warmup": 60, "prescreen_top": 60,
        "pct_lo": 1.0, "pct_hi": 8.0, "fallback_lo": 0.5,
        "prescreen_sort": "amount",
        "score_fn": hybrid_score_df, "score_key": "score",
        "threshold": 15.0, "picked_top": 25,
        "max_prior_gain_pct": None,
        "require_index_above_ma": None,
        "require_index_ma_rising": None,  # 额外要求MA_N较N日前上升(趋势强化,过滤震荡)
        "require_index_gain_20d": None,   # 强趋势过滤: 指数近20日涨幅不足此值(如0.03)暂停开仓
        "stop_loss_pct": None,        # E1: 如 -0.07 浮亏7%次日开盘提前卖出
        "exit_below_ma": None,        # E2: 如 20 收盘跌破MA20次日开盘提前卖出
        "breakout_no_pullback": None, # E3: True=信号日最低价曾破前高(20日)则放弃
        "vol_price_confirm": None,    # E4: True=阴线不入选(放量须配阳线)
        "max_atr_pct": None,          # ATR14/收盘价% 超过则放弃(波动率过滤)
        "trailing_activate_pct": None, # 移动止损: 浮盈达此值后启用 (如0.06)
        "trailing_retrace_pct": None,  # 移动止损: 最高收盘回撤此值次日离场 (如0.05)
    },
    "dragon": {
        "lookback": 60, "warmup": 20, "prescreen_top": 10,
        "pct_lo": 9.5, "pct_hi": float("inf"), "fallback_lo": None,
        "prescreen_sort": "pct_chg",    # 涨停板池按涨幅降序
        "score_fn": dragon_leader_score_df, "score_key": "leader_score",
        "threshold": None, "picked_top": 25,
        "max_prior_gain_pct": None,
        "require_index_above_ma": None,
        "require_index_ma_rising": None,  # 额外要求MA_N较N日前上升(趋势强化,过滤震荡)
        "require_index_gain_20d": None,   # 强趋势过滤: 指数近20日涨幅不足此值(如0.03)暂停开仓
        "stop_loss_pct": None,        # E1: 如 -0.07 浮亏7%次日开盘提前卖出
        "exit_below_ma": None,        # E2: 如 20 收盘跌破MA20次日开盘提前卖出
        "breakout_no_pullback": None, # E3: True=信号日最低价曾破前高(20日)则放弃
        "vol_price_confirm": None,    # E4: True=阴线不入选(放量须配阳线)
        "max_atr_pct": None,          # ATR14/收盘价% 超过则放弃(波动率过滤)
        "trailing_activate_pct": None, # 移动止损: 浮盈达此值后启用 (如0.06)
        "trailing_retrace_pct": None,  # 移动止损: 最高收盘回撤此值次日离场 (如0.05)
    },
}


def _short(ts_code):
    return ts_code.split(".")[0]


def _benchmark_block(benchmark, equity_dates, equity_vals, initial_cash):
    """同窗口指数买入持有基准 (2026-11-01 G1 返修): 与策略净值同日起点归一,
    输出基准收益/策略收益/超额pp + 覆盖率; 基准不可用显式标注, 不补0"""
    if not benchmark or not benchmark.get("closes"):
        note = ((benchmark or {}).get("note")
                or "指数基准数据不可用, 不补0; 策略结果仍为本窗口真实计算")
        return {"status": "unavailable", "note": note}
    closes = benchmark["closes"]
    base = None
    last = None
    matched = 0
    valid = []
    for d in equity_dates:
        c = closes.get(d)
        if c is not None:
            last = c
            matched += 1
        if last is None:
            continue            # 窗口内尚无任何基准值 → 该日不计入
        if base is None:
            base = last         # 首个可得基准收盘 = 买入持有起点
        valid.append((d, last))
    if base is None:
        return {"status": "unavailable",
                "note": "窗口内无指数基准交易日, 不补0; 策略结果仍为本窗口真实计算"}
    total_return = valid[-1][1] / base - 1
    strategy_return = equity_vals[-1] / initial_cash - 1
    coverage = matched / len(equity_dates) if equity_dates else 0.0
    return {
        "status": "ok",
        "name": benchmark.get("name", "指数基准"),
        "code": benchmark.get("code"),
        "total_return_pct": round(total_return * 100, 2),
        "strategy_return_pct": round(strategy_return * 100, 2),
        "excess_return_pp": round((strategy_return - total_return) * 100, 2),
        "coverage": round(coverage, 3),
        "equity_curve": [{"date": d, "equity": round(v / base * initial_cash, 2)}
                         for d, v in valid],
        "note": ("指数买入持有同窗口对照, 与策略同日起点归一; 基准不计交易费用(策略已计费), "
                 "为简化对照口径; coverage<1 表示部分交易日无基准值(按最近可得值携带), "
                 "覆盖率不足时超额结论仅供参考"),
    }


def _scan_day(spec, frames, cols, idx_maps, dates, t, st_codes):
    """第 t 个市场日 (信号日 T) 的截面信号 — 仅用 ≤T 数据 (防未来函数)。

    返回 [(ts_code, score)] 按评分降序, 长度 ≤ picked_top, 与线上 picked 同口径。
    """
    day = dates[t]
    rows = []
    for c, m in idx_maps.items():
        if c.endswith(".BJ"):
            continue
        if _short(c) in st_codes:
            continue
        i = m.get(day)
        if i is None:
            continue
        pct = cols[c][3][i]
        if not np.isfinite(pct):
            continue
        rows.append((c, i, pct, cols[c][2][i] * cols[c][1][i]))  # vol×close 近似成交额

    lo, hi = spec["pct_lo"], spec["pct_hi"]
    cand = [r for r in rows if lo <= r[2] < hi]
    if spec["fallback_lo"] is not None and len(cand) < 10:
        cand = [r for r in rows if spec["fallback_lo"] <= r[2] < hi]
    key_idx = 3 if spec["prescreen_sort"] == "amount" else 2
    cand.sort(key=lambda r: r[key_idx], reverse=True)
    cand = cand[:spec["prescreen_top"]]

    lookback = spec["lookback"]
    score_fn, score_key = spec["score_fn"], spec["score_key"]
    threshold = spec["threshold"]
    scored = []
    for c, i, _pct, _amt in cand:
        if spec.get("max_prior_gain_pct") is not None and i >= 60:
            prev = cols[c][1][i - 60]
            if prev > 0 and (cols[c][1][i] / prev - 1) * 100 > spec["max_prior_gain_pct"]:
                continue  # I3 假设2: 排除信号日前60日涨幅超标的标的 (追高过滤)
        if spec.get("vol_price_confirm") and cols[c][1][i] <= cols[c][0][i]:
            continue  # E4: 量价同向确认 — 阴线不入选 (放量须配阳线)
        if spec.get("max_atr_pct") is not None and i >= 14:
            # ATR14/收盘价 波动率过滤: 波动过大标的放弃 (量化方法调研首推指标族)
            f0 = frames[c]
            hi = f0["high"].iloc[i - 13:i + 1].to_numpy(dtype=float)
            lo = f0["low"].iloc[i - 13:i + 1].to_numpy(dtype=float)
            pc = f0["close"].iloc[i - 14:i].to_numpy(dtype=float)
            tr = np.maximum(hi - lo, np.maximum(np.abs(hi - pc), np.abs(lo - pc)))
            price = cols[c][1][i]
            if price > 0 and tr.mean() / price * 100 > spec["max_atr_pct"]:
                continue
        if spec.get("breakout_no_pullback") and i >= 20:
            prev_high = frames[c]["high"].iloc[i - 20:i].max()
            if frames[c]["low"].iloc[i] <= prev_high:
                continue  # E3: 回踩确认 — 信号日最低价曾跌破前高(20日)则放弃
        f = frames[c]
        df_slice = f.iloc[max(0, i + 1 - lookback):i + 1]  # 截至 T 的尾部切片
        try:
            r = score_fn(df_slice, code=_short(c))
        except Exception:
            continue
        if not r:
            continue
        s = r.get(score_key)
        if s is None:
            continue
        if threshold is None or s >= threshold:
            scored.append((c, float(s)))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:spec["picked_top"]]


def run_backtest(strategy, frames, name_map=None, st_codes=None,
                 hold=5, top_n=5, initial_cash=INITIAL_CASH, benchmark=None,
                 eval_start=None, eval_end=None, include_trades=False,
                 spec_override=None):
    """纯函数回测引擎 (离线可测): 输入全市场K线面板, 输出指标与成交明细。

    frames: {ts_code: DataFrame[date,open,high,low,close,volume,pct_chg] 升序}
    name_map: {短码: 名称} (交易明细展示用)
    st_codes: 短码集合, 当前名录口径的 ST 剔除 (历史戴帽无法回溯, 见 notes);
      为空集时不做任何名称级 ST 剔除, notes 会如实声明 (不冒充已筛)
    benchmark: 可选 {name, code, closes:{YYYY-MM-DD:close}} 指数日线序列,
      同窗口买入持有对照; 缺省/无数据 → benchmark.status=unavailable 显式标注
    eval_start/eval_end: 可选评估窗口 (YYYY-MM-DD) — walk-forward 用;
      eval_end 会先截断全部K线(防未来数据), eval_start 之前的日期不参与信号/净值
    include_trades: True 时结果附 trades_all 全量成交 (AI 归因用), 默认仅末10笔样本
    spec_override: 可选策略参数覆盖 (I3 单变量假设验证, 如 {"threshold":35.0});
      仅覆盖 _SIGNAL_SPEC 中已有键, 防止拼写错误悄悄新增无效参数
    """
    if strategy not in _SIGNAL_SPEC:
        raise ValueError(f"未知策略: {strategy}")
    spec = dict(_SIGNAL_SPEC[strategy])
    if spec_override:
        unknown = set(spec_override) - set(spec)
        if unknown:
            raise ValueError(f"未知策略参数: {sorted(unknown)}")
        spec.update(spec_override)
    hold = int(min(max(hold or 5, 1), 20))
    top_n = int(min(max(top_n or 5, 1), 10))
    initial_cash = float(initial_cash)
    name_map = name_map or {}
    st_codes = set(st_codes or ())

    frames = {c: f.reset_index(drop=True) for c, f in frames.items()
              if f is not None and len(f) >= spec["warmup"]}
    if eval_end:
        # walk-forward 防未来数据: 先截断窗口之后的全部K线再计算
        frames = {c: f[f["date"] <= eval_end].reset_index(drop=True)
                  for c, f in frames.items()}
        frames = {c: f for c, f in frames.items() if len(f) >= spec["warmup"]}
    if not frames:
        raise ValueError("面板为空或无足够历史")

    dates = sorted({d for f in frames.values() for d in f["date"].tolist()})
    idx_maps = {c: {d: i for i, d in enumerate(f["date"].tolist())}
                for c, f in frames.items()}
    cols = {c: (f["open"].to_numpy(dtype=float), f["close"].to_numpy(dtype=float),
                f["volume"].to_numpy(dtype=float), f["pct_chg"].to_numpy(dtype=float))
            for c, f in frames.items()}

    per_trade_cash = initial_cash / top_n
    cash = initial_cash
    positions = {}   # code -> dict(qty, invest, entry_date, entry_idx, exit_plan_idx|None)
    trades = []      # 已完成交易 (metrics dict 契约)
    open_legs = []   # 窗口尽头仍持仓的入场腿 (计入换手)
    last_close = {}  # 持仓标的最近已知收盘 (停牌沿用)
    equity_dates, equity_vals = [], []
    signals_total = 0
    scans = {}       # 信号日缓存: t -> [(code, score)]

    first_t = spec["warmup"] - 1     # 首个可评分信号日 (0-based)
    if eval_start:
        # 窗口内第一个 ≥eval_start 的市场日 (不小于暖机约束)
        later = [i for i, d in enumerate(dates) if d >= eval_start]
        if not later:
            raise ValueError(f"评估起点超出面板日期范围: {eval_start}")
        first_t = max(first_t, later[0])
    last_t = len(dates) - 1

    # 市场状态闸门 (I3 结构假设): 启用后, 指数收盘低于其 MA_N 的信号日暂停新开仓
    # (持仓按原计划退出, 不受影响); 指数历史不足的日子不拦截
    def _regime_ok(t):
        if not benchmark or not benchmark.get("closes"):
            return True
        closes = benchmark["closes"]
        upto = sorted(d for d in closes if d <= dates[t])
        n = spec.get("require_index_above_ma") or 20
        if len(upto) < n:
            return True
        ma = sum(closes[d] for d in upto[-n:]) / n
        if spec.get("require_index_above_ma") and closes[upto[-1]] < ma:
            return False
        # 趋势强化: 额外要求 MA_N 斜率向上 (过滤刚站上均线但仍震荡的阶段)
        rising = spec.get("require_index_ma_rising")
        if rising and len(upto) >= n + rising:
            ma_prev = sum(closes[d] for d in upto[-n - rising:-rising]) / n
            if ma <= ma_prev:
                return False
        # 强趋势过滤 (A路线): 指数近20日涨幅不足阈值时暂停开仓 (弱/平趋势不做)
        gain_n = spec.get("require_index_gain_20d")
        if gain_n is not None and len(upto) > 20:
            gain = closes[upto[-1]] / closes[upto[-21]] - 1
            if gain < gain_n:
                return False
        return True

    for d in range(first_t, last_t + 1):
        day = dates[d]
        # ── 1) 开盘: 到期持仓卖出 ──
        for c in list(positions):
            p = positions[c]
            # E1 固定止损 / E2 趋势破坏退出: 仅用 ≤d-1 收盘数据, 次日开盘成交
            if p["exit_idx"] != d and d > 0:
                prev_day = dates[d - 1]
                m_prev = idx_maps[c].get(prev_day)
                if m_prev is not None:
                    prev_close = cols[c][1][m_prev]
                    if (p["exit_idx"] != d and spec.get("trailing_retrace_pct") is not None):
                        # 移动止损: 浮盈达 activate 后, 从最高收盘回撤 retrace 次日离场 (让利润奔跑)
                        p["max_close"] = max(p.get("max_close", 0.0), prev_close)
                        entry_px = p["invest"] / p["qty"] / (1 + FEE_BUY)
                        activate = spec.get("trailing_activate_pct", 0.0)
                        if (p["max_close"] >= entry_px * (1 + activate)
                                and prev_close <= p["max_close"] * (1 - spec["trailing_retrace_pct"])):
                            p["exit_idx"] = d
                    if spec.get("stop_loss_pct") is not None:
                        entry_px = p["invest"] / p["qty"] / (1 + FEE_BUY)
                        if prev_close <= entry_px * (1 + spec["stop_loss_pct"]):
                            p["exit_idx"] = d
                    if (p["exit_idx"] != d and spec.get("exit_below_ma")
                            and m_prev + 1 >= spec["exit_below_ma"]):
                        n = spec["exit_below_ma"]
                        ma = cols[c][1][m_prev - n + 1:m_prev + 1].mean()
                        if prev_close < ma:
                            p["exit_idx"] = d
            if p["exit_idx"] is None or p["exit_idx"] != d:
                continue
            m = idx_maps[c]
            if day in m:  # 正常: 当日开盘价成交
                exit_open = cols[c][0][m[day]]
            else:         # 停牌: 顺延至复牌首日开盘价
                later_idx = None
                for dd in range(d + 1, last_t + 1):
                    if dates[dd] in m:
                        later_idx = dd
                        break
                if later_idx is None:
                    p["exit_idx"] = None      # 窗口内无复牌 → 留仓至结尾
                    continue
                p["exit_idx"] = later_idx
                continue                      # 顺延到复牌日再卖
            proceeds = p["qty"] * exit_open * (1 - FEE_SELL)
            cash += proceeds
            pnl = proceeds - p["invest"]
            entry_px = p["invest"] / p["qty"] / (1 + FEE_BUY)
            trades.append({
                "symbol": _short(c), "name": name_map.get(_short(c), ""),
                "pnl": pnl, "holding_bars": d - p["entry_idx"],
                "signal_date": p["signal_date"],
                "entry_time": p["entry_date"], "exit_time": day,
                "entry_margin": p["invest"], "exit_margin": proceeds,
                "entry_price": round(float(entry_px), 4),
                "exit_price": round(float(exit_open), 4),
                "pnl_pct": round(pnl / p["invest"] * 100, 2) if p["invest"] else 0.0,
            })
            del positions[c]

        # ── 2) 开盘: 昨日 (T=d-1) 信号买入 (市场状态闸门: 弱市暂停新开仓) ──
        t = d - 1
        if t >= first_t and _regime_ok(t):
            if t not in scans:
                scans[t] = _scan_day(spec, frames, cols, idx_maps, dates, t, st_codes)
            signals = scans[t][:top_n]
            signals_total += len(signals)
            for c, _s in signals:
                if c in positions:
                    continue                       # 已持仓不加仓
                if cash < per_trade_cash:
                    continue                       # 现金不足定额, 跳过
                m = idx_maps[c]
                i = m.get(day)
                if i is None:
                    continue                       # T+1 停牌 → 信号作废
                entry_open = cols[c][0][i]
                if not np.isfinite(entry_open) or entry_open <= 0:
                    continue
                qty = per_trade_cash / (entry_open * (1 + FEE_BUY))
                cash -= qty * entry_open * (1 + FEE_BUY)
                exit_idx = d + hold                # T+1+H 开市日卖出
                positions[c] = {
                    "qty": qty, "invest": per_trade_cash,
                    "entry_date": day, "entry_idx": d,
                    "signal_date": dates[t],
                    "exit_idx": exit_idx if exit_idx <= last_t else None,
                }

        # ── 3) 收盘计值 ──
        mv = 0.0
        for c, p in positions.items():
            i = idx_maps[c].get(day)
            if i is not None:
                last_close[c] = cols[c][1][i]
            px = last_close.get(c)
            if px is not None and np.isfinite(px):
                mv += p["qty"] * px
        equity_dates.append(day)
        equity_vals.append(cash + mv)

    # 窗口尽头仍持仓 → 浮动计入净值, 但不计入已完成交易; 其入场腿计入换手
    for c, p in positions.items():
        open_legs.append({
            "symbol": _short(c), "entry_time": p["entry_date"], "exit_time": None,
            "entry_margin": p["invest"], "exit_margin": None,
        })

    equity = pd.Series(equity_vals, index=pd.Index(equity_dates, name="date"),
                       dtype=float)
    turnover = calc_trade_turnover_series(trades + open_legs, equity)
    metrics = calc_metrics(equity, trades, initial_cash,
                           bars_per_year=calc_bars_per_year("1D", "tushare"),
                           turnover_series=turnover)

    return {
        "strategy": strategy,
        "engine": "local_backtest (signals=线上扫描同路径, metrics=Vibe-Trading/MIT移植)",
        "params": {
            "hold_days": hold, "top_n": top_n, "initial_cash": initial_cash,
            "per_trade_cash": round(per_trade_cash, 2),
            "fee_buy": FEE_BUY, "fee_sell": FEE_SELL,
        },
        "window": {
            "start": equity_dates[0] if equity_dates else None,
            "end": equity_dates[-1] if equity_dates else None,
            "market_days": len(equity_dates),
            "signal_days": max(0, last_t - first_t),
            "panel_days": PANEL_DAYS, "warmup_days": spec["warmup"],
        },
        "metrics": metrics,
        "benchmark": _benchmark_block(benchmark, equity_dates, equity_vals, initial_cash),
        "signals_total": signals_total,
        "trades_completed": len(trades),
        "trades_open": len(open_legs),
        **({"trades_all": trades} if include_trades else {}),
        "equity_curve": [{"date": d, "equity": round(v, 2)}
                         for d, v in zip(equity_dates, equity_vals)],
        "trades_sample": [{
            "code": t["symbol"], "name": t["name"], "signal_date": t["signal_date"],
            "entry_date": t["entry_time"], "entry_price": t["entry_price"],
            "exit_date": t["exit_time"], "exit_price": t["exit_price"],
            "pnl": round(t["pnl"], 2), "pnl_pct": t["pnl_pct"],
        } for t in trades[-10:]],
        "notes": [
            f"防未来函数: 信号日T仅用≤T数据; 买入=T+1开盘价, 卖出=T+1+{hold}日开盘价",
            "预筛选成交额用 vol×close 近似 (面板无amount列); " + (
                "ST剔除为当前名录口径回填历史, 历史戴帽未回溯, 存在时点偏差 — 不宣称整体无未来信息"
                if st_codes else
                "未按名称剔除 ST (st_codes 为空); 若因名录缺失所致, 响应 st_filter 标 degraded_no_namelist"),
            f"每笔定额等权(初始资金/{top_n}), 现金不足跳过, 允许零碎股(仅用于指标)",
            "手续费口径假设: 买入万2.5 / 卖出万2.5+印花税万5; 停牌顺延复牌首日开盘价",
            f"面板限最近{PANEL_DAYS}个已发布开市日 (暖机{spec['warmup']}日), 年化=252",
            "候选池=当前名录, 已退市股票不在池中 (幸存者偏差未消除); 单窗口样本内结果, "
            "无样本外验证 — 回测仅证明计算口径自洽, 不构成投资有效性证据",
        ] + ([f"市场状态闸门: 指数收盘低于MA{spec['require_index_above_ma']}的信号日暂停新开仓 "
              "(I3 结构假设启用); 指数历史不足日不拦截"] if spec.get("require_index_above_ma") else [])
          + ([f"趋势强化: 另需MA{spec['require_index_above_ma']}较{spec['require_index_ma_rising']}日前上升"] if spec.get("require_index_ma_rising") else [])
          + ([f"强趋势过滤: 指数20日涨幅<{spec['require_index_gain_20d']:.0%}暂停开仓"] if spec.get("require_index_gain_20d") is not None else [])
          + ([f"提前退出: 浮亏达{spec['stop_loss_pct']:.0%}次日开盘止损" ] if spec.get("stop_loss_pct") else [])
          + ([f"提前退出: 收盘跌破MA{spec['exit_below_ma']}次日开盘离场"] if spec.get("exit_below_ma") else [])
          + (["入场确认: 信号日最低价不破前高(20日)才入选(回踩过滤)"] if spec.get("breakout_no_pullback") else [])
          + (["入场确认: 阴线不入选(量价同向)"] if spec.get("vol_price_confirm") else [])
          + ([f"移动止损: 浮盈{spec['trailing_activate_pct']:.0%}后最高收盘回撤{spec['trailing_retrace_pct']:.0%}离场"]
             if spec.get("trailing_retrace_pct") is not None else [])
          + ([f"波动率过滤: ATR14/收盘价>{spec['max_atr_pct']}%放弃"] if spec.get("max_atr_pct") else []),
        "disclaimer": "回测为历史数据研究视图, 不构成投资建议; 本地仅手动记账, 无交易能力",
    }


# ─── 本地服务装配 ────────────────────────────────────────

def run_walk_forward(strategy, frames, n_windows=6, hold=5, top_n=5,
                     initial_cash=INITIAL_CASH, benchmark=None,
                     name_map=None, st_codes=None, include_trades=False,
                     spec_override=None):
    """walk-forward 多窗口评估 (2026-11-08 I1): 可信号日期区间均分为 n 个连续窗口,
    每窗口独立回测 (eval_start/eval_end 截断, 窗口内无未来数据);
    汇总窗口明细+正超额窗口数+中位超额, 供迭代门槛判定与 AI 归因"""
    import statistics
    spec = _SIGNAL_SPEC[strategy]
    all_dates = sorted({d for f in frames.values() for d in f["date"].tolist()})
    usable = all_dates[spec["warmup"] - 1:]
    if len(usable) < n_windows * 2:
        raise ValueError(f"可用交易日 {len(usable)} 不足以划分 {n_windows} 个窗口")
    bounds = [round(i * len(usable) / n_windows) for i in range(n_windows + 1)]
    windows = []
    for w in range(n_windows):
        ws, we = usable[bounds[w]], usable[bounds[w + 1] - 1]
        r = run_backtest(strategy, frames, name_map=name_map, st_codes=st_codes,
                         hold=hold, top_n=top_n, initial_cash=initial_cash,
                         benchmark=benchmark, eval_start=ws, eval_end=we,
                         include_trades=include_trades, spec_override=spec_override)
        b = r["benchmark"]
        entry = {
            "window": w + 1, "start": ws, "end": we,
            "strategy_return_pct": b.get("strategy_return_pct"),
            "benchmark_return_pct": b.get("total_return_pct"),
            "excess_pp": b.get("excess_return_pp"),
            "benchmark_status": b.get("status"),
            "trades_completed": r["trades_completed"],
            "trades_open": r["trades_open"],
            "signals_total": r["signals_total"],
            "win_rate": r["metrics"].get("win_rate"),
            "max_drawdown": r["metrics"].get("max_drawdown"),
        }
        if include_trades:
            entry["trades_all"] = r.get("trades_all", [])
            entry["equity_curve"] = r["equity_curve"]
        windows.append(entry)
    valid = [w for w in windows if w["excess_pp"] is not None]
    positive = sum(1 for w in valid if w["excess_pp"] > 0)
    summary = {
        "strategy": strategy, "n_windows": len(valid),
        "positive_excess_windows": positive,
        "median_excess_pp": (round(statistics.median(w["excess_pp"] for w in valid), 2)
                             if valid else None),
        "mean_excess_pp": (round(statistics.mean(w["excess_pp"] for w in valid), 2)
                           if valid else None),
        "active_windows": sum(1 for w in windows if w["trades_completed"] > 0),
        "total_trades": sum(w["trades_completed"] for w in windows),
        "total_signals": sum(w["signals_total"] for w in windows),
        "gate_note": ("门槛参考 (I3 启用条件): 中位超额>0 且正超额窗口过半 "
                      "(单窗口运气不算数); 本轮为基线测量, 不构成投资建议"),
    }
    return {"summary": summary, "windows": windows}


# ─── 本地服务装配 ────────────────────────────────────────

def _run_local(strategy, hold, top_n):
    """取真实面板 + 名录, 跑回测。面板异常向上抛 (视图层转 502, 不补位)。

    ST 名录口径 (2026-10-27 验收返修): 名录获取失败/为空不再静默当作已过滤 —
    结果仍可计算, 但响应显式带 st_filter=degraded_no_namelist + st_note 声明
    (与三策略扫描/翻倍同一降级协议), 绝不让貌似已筛 ST 的结果无标注流出。
    """
    import local_tushare_data as _real

    frames = _real._cached(f"kline_panel:{PANEL_DAYS}", 600,
                           lambda: _real._kline_panel_frames(PANEL_DAYS))
    try:
        basic = _real.real_get_stock_basic()
    except Exception:
        basic = None
    st_ok = isinstance(basic, dict) and len(basic) > 0
    name_map, st_codes = {}, set()
    if st_ok:
        for code, info in basic.items():
            short = str(code).split(".")[0]
            name = str((info or {}).get("name", ""))
            name_map[short] = name
            if "ST" in name.upper():
                st_codes.add(short)
    try:
        bench = _real.real_index_series("000300.SH", PANEL_DAYS, name="沪深300")
    except Exception as e:
        bench = {"note": f"指数基准获取失败(不补0): {str(e)[:80]}"}
    result = run_backtest(strategy, frames, name_map=name_map, st_codes=st_codes,
                          hold=hold, top_n=top_n, benchmark=bench)
    result["st_filter"] = "applied" if st_ok else "degraded_no_namelist"
    result["st_note"] = (None if st_ok else
                         "ST 名录缺失, 回测候选池未按名称剔除 ST (结果可能含 ST 标的); "
                         "ST 名单为当前名录回填历史, 存在时点偏差 — 不宣称整体无未来信息")
    return result


def install_backtest(app):
    """注册三策略回测只读端点: GET /api/strategy/<name>/backtest?hold=5&top_n=5
    (白名单放行在 local_run.py _DEMO_EXACT; 结果按数据日6h缓存)。"""
    from flask import request, jsonify
    import local_tushare_data as _real

    def _make(name):
        def _view():
            try:
                hold = int(request.args.get("hold", 5))
                top_n = int(request.args.get("top_n", 5))
            except (TypeError, ValueError):
                return jsonify(error="bad_request",
                               message="hold/top_n 须为整数"), 400
            if not (1 <= hold <= 20) or not (1 <= top_n <= 10):
                return jsonify(error="bad_request",
                               message="hold 限 1-20, top_n 限 1-10"), 400
            key = f"strategy_backtest:{name}:{hold}:{top_n}"
            try:
                result = _real.real_day_cached(
                    key, lambda: _run_local(name, hold, top_n))()
            except Exception as e:
                return jsonify(error="backtest_unavailable",
                               message=f"回测计算失败(数据源不可用或面板未就绪): {e}",
                               strategy=name), 502
            return jsonify(result)
        return _view

    for _name in _STRATEGIES:
        app.add_url_rule(f"/api/strategy/{_name}/backtest",
                         f"strategy_backtest_{_name}", _make(_name),
                         methods=["GET"])
