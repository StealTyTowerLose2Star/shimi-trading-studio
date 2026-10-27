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
    },
    "hybrid": {
        "lookback": 120, "warmup": 60, "prescreen_top": 60,
        "pct_lo": 1.0, "pct_hi": 8.0, "fallback_lo": 0.5,
        "prescreen_sort": "amount",
        "score_fn": hybrid_score_df, "score_key": "score",
        "threshold": 15.0, "picked_top": 25,
    },
    "dragon": {
        "lookback": 60, "warmup": 20, "prescreen_top": 10,
        "pct_lo": 9.5, "pct_hi": float("inf"), "fallback_lo": None,
        "prescreen_sort": "pct_chg",    # 涨停板池按涨幅降序
        "score_fn": dragon_leader_score_df, "score_key": "leader_score",
        "threshold": None, "picked_top": 25,
    },
}


def _short(ts_code):
    return ts_code.split(".")[0]


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
                 hold=5, top_n=5, initial_cash=INITIAL_CASH):
    """纯函数回测引擎 (离线可测): 输入全市场K线面板, 输出指标与成交明细。

    frames: {ts_code: DataFrame[date,open,high,low,close,volume,pct_chg] 升序}
    name_map: {短码: 名称} (交易明细展示用)
    st_codes: 短码集合, 当前名录口径的 ST 剔除 (历史戴帽无法回溯, 见 notes);
      为空集时不做任何名称级 ST 剔除, notes 会如实声明 (不冒充已筛)
    """
    if strategy not in _SIGNAL_SPEC:
        raise ValueError(f"未知策略: {strategy}")
    spec = _SIGNAL_SPEC[strategy]
    hold = int(min(max(hold or 5, 1), 20))
    top_n = int(min(max(top_n or 5, 1), 10))
    initial_cash = float(initial_cash)
    name_map = name_map or {}
    st_codes = set(st_codes or ())

    frames = {c: f.reset_index(drop=True) for c, f in frames.items()
              if f is not None and len(f) >= spec["warmup"]}
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
    last_t = len(dates) - 1

    for d in range(first_t, last_t + 1):
        day = dates[d]
        # ── 1) 开盘: 到期持仓卖出 ──
        for c in list(positions):
            p = positions[c]
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

        # ── 2) 开盘: 昨日 (T=d-1) 信号买入 ──
        t = d - 1
        if t >= first_t:
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
        "signals_total": signals_total,
        "trades_completed": len(trades),
        "trades_open": len(open_legs),
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
        ],
        "disclaimer": "回测为历史数据研究视图, 不构成投资建议; 本地仅手动记账, 无交易能力",
    }


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
    result = run_backtest(strategy, frames, name_map=name_map, st_codes=st_codes,
                          hold=hold, top_n=top_n)
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
