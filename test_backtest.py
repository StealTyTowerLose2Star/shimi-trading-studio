# -*- coding: utf-8 -*-
"""回测离线测试 — Vibe-Trading 指标移植正确性 + 防未来函数 + 引擎簿记 (2026-10-27)

- 指标: services/backtest_metrics (HKUDS/Vibe-Trading MIT 移植) 手算小例子核对
  total_return / max_drawdown / sharpe / 胜率 / 盈亏比 / 换手率。
- 防未来函数: 信号日 T 突变 T 之后的数据, T 时刻信号与已成交交易必须不变;
  成交价恒为 T+1 开盘价, 卖出恒为 T+1+H 开盘价 (停牌顺延)。
- 引擎: local_backtest.run_backtest 纯函数, 合成K线面板, 无网络无凭据。

全程合成数据, 无网络、无凭据要求。
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd

from services import backtest_metrics as bm
import local_backtest as lb


def _mk_dates(n=90):
    return [d.strftime("%Y-%m-%d") for d in pd.date_range("2026-05-01", periods=n)]


def _mk_frame(ts_code, dates, base=10.0, daily=0.01, skip_dates=()):
    """合成稳定上涨趋势 (趋势策略必出信号): close=base*(1+daily)^i, open=昨收"""
    closes, opens, dates_out = [], [], []
    prev = None
    for i, d in enumerate(dates):
        if d in skip_dates:
            continue
        c = base * (1 + daily) ** i
        o = prev if prev is not None else base
        closes.append(c)
        opens.append(o)
        dates_out.append(d)
        prev = c
    n = len(dates_out)
    close = np.array(closes)
    open_ = np.array(opens)
    df = pd.DataFrame({
        "date": dates_out,
        "open": open_,
        "high": close * 1.005,
        "low": open_ * 0.995,
        "close": close,
        "volume": np.full(n, 10000.0),
        "pct_chg": np.concatenate([[np.nan], close[1:] / close[:-1] - 1]) * 100,
    })
    return df


def _mk_frames(dates, skip_b=()):
    return {
        "000001.SZ": _mk_frame("000001.SZ", dates),
        "000002.SZ": _mk_frame("000002.SZ", dates, skip_dates=skip_b),
        "600000.SH": _mk_frame("600000.SH", dates, daily=0.0),   # 平盘, 预筛除外
        "000003.SZ": _mk_frame("000003.SZ", dates),              # ST 剔除测试
        "430001.BJ": _mk_frame("430001.BJ", dates),              # 北证排除测试
    }


class TestMetricsHandCalc(unittest.TestCase):
    """手算小例子核对移植指标 (公式与上游逐字一致)"""

    def test_bars_per_year(self):
        self.assertEqual(bm.calc_bars_per_year("1D", "tushare"), 252)
        self.assertEqual(bm.calc_bars_per_year("1d", "TUSHARE"), 252)
        self.assertEqual(bm.calc_bars_per_year("1W", "tushare"), 52)
        self.assertEqual(bm.calc_bars_per_year("1M", "tushare"), 12)
        self.assertEqual(bm.calc_bars_per_year("1D", "unknown_source"), 252)

    def test_calc_metrics_hand(self):
        eq = pd.Series([100.0, 110.0, 99.0, 121.0],
                       index=["d0", "d1", "d2", "d3"])
        trades = [
            {"pnl": 10.0, "holding_bars": 5, "symbol": "A"},
            {"pnl": -5.0, "holding_bars": 5, "symbol": "B"},
            {"pnl": 20.0, "holding_bars": 5, "symbol": "A"},
            {"pnl": -10.0, "holding_bars": 5, "symbol": "C"},
        ]
        m = bm.calc_metrics(eq, trades, 100.0, bars_per_year=252)
        self.assertAlmostEqual(m["total_return"], 0.21, places=9)
        self.assertAlmostEqual(m["max_drawdown"], -0.1, places=9)
        self.assertAlmostEqual(m["annual_return"], 164238.770664, places=3)
        self.assertAlmostEqual(m["sharpe"], 6.396021, places=5)
        self.assertAlmostEqual(m["win_rate"], 0.5, places=9)
        self.assertAlmostEqual(m["profit_factor"], 2.0, places=9)
        self.assertAlmostEqual(m["profit_loss_ratio"], 2.0, places=9)
        self.assertEqual(m["max_consecutive_loss"], 1)
        self.assertEqual(m["trade_count"], 4)
        self.assertAlmostEqual(m["avg_holding_days"], 5.0, places=9)
        self.assertAlmostEqual(m["final_value"], 121.0, places=9)

    def test_trade_turnover(self):
        eq = pd.Series([100.0, 110.0, 99.0, 121.0],
                       index=["d0", "d1", "d2", "d3"])
        trades = [{
            "pnl": 10.0, "holding_bars": 1, "symbol": "X",
            "entry_time": "d0", "entry_margin": 100.0,
            "exit_time": "d1", "exit_margin": 110.0,
        }]
        ts = bm.calc_trade_turnover_series(trades, eq)
        self.assertAlmostEqual(ts.loc["d0"], 0.5, places=9)   # 100/(2*100)
        self.assertAlmostEqual(ts.loc["d1"], 0.5, places=9)   # 110/(2*110)
        self.assertAlmostEqual(ts.loc["d2"], 0.0, places=9)
        m = bm.calc_metrics(eq, trades, 100.0, turnover_series=ts)
        self.assertAlmostEqual(m["avg_turnover"], 0.25, places=9)
        self.assertAlmostEqual(m["total_turnover"], 1.0, places=9)

    def test_position_turnover_series(self):
        pos = pd.DataFrame({"A": [0.0, 100.0, 100.0, 0.0],
                            "B": [0.0, 0.0, 50.0, 0.0]})
        ts = bm.calc_turnover_series(pos)
        self.assertAlmostEqual(ts.iloc[0], 0.0, places=9)
        self.assertAlmostEqual(ts.iloc[1], 50.0, places=9)    # 0.5*100
        self.assertAlmostEqual(ts.iloc[2], 25.0, places=9)    # 0.5*50
        self.assertAlmostEqual(ts.iloc[3], 75.0, places=9)    # 0.5*(100+50)

    def test_empty_metrics(self):
        m = bm.calc_metrics(pd.Series(dtype=float), [], 100.0)
        self.assertEqual(m["trade_count"], 0)
        self.assertEqual(m["final_value"], 100.0)


class TestBarReturns(unittest.TestCase):
    """非正前价的符号安全 (上游 issue #872 口径)"""

    def test_normal_series(self):
        r = bm.bar_returns(pd.Series([100.0, 110.0, 121.0]))
        self.assertAlmostEqual(r.iloc[0], 0.0, places=12)
        self.assertAlmostEqual(r.iloc[1], 0.1, places=12)
        self.assertAlmostEqual(r.iloc[2], 0.1, places=12)

    def test_zero_and_gap(self):
        r = bm.bar_returns(pd.Series([100.0, 0.0, 50.0, 50.0, 25.0]))
        vals = r.tolist()
        self.assertTrue(all(np.isfinite(vals)), vals)  # 绝不出现 inf/nan
        self.assertAlmostEqual(vals[1], -1.0, places=12)  # 0/100-1 (前价为正, 有定义)
        self.assertAlmostEqual(vals[2], 0.0, places=12)   # 前价=0, 无定义报 0.0
        self.assertAlmostEqual(vals[3], 0.0, places=12)
        self.assertAlmostEqual(vals[4], -0.5, places=12)


class TestEngineSemantics(unittest.TestCase):
    """合成面板上的引擎簿记: T+1成交 / 手续费 / 跳过规则 / 停牌顺延"""

    @classmethod
    def setUpClass(cls):
        cls.dates = _mk_dates(90)
        cls.frames = _mk_frames(cls.dates, skip_b={cls.dates[75]})
        cls.result = lb.run_backtest(
            "trend", cls.frames,
            name_map={"000001": "平安", "000002": "万科", "000003": "ST测试"},
            st_codes={"000003"}, hold=5, top_n=2, initial_cash=100000.0)

    def test_signals_and_trades_exist(self):
        r = self.result
        self.assertGreater(r["signals_total"], 0)
        self.assertGreater(r["trades_completed"], 0)
        self.assertEqual(r["metrics"]["trade_count"], r["trades_completed"])

    def test_no_st_no_bj_no_flat(self):
        codes = set()
        # 通过 scan 直接确认候选池
        spec = lb._SIGNAL_SPEC["trend"]
        idx_maps = {c: {d: i for i, d in enumerate(f["date"].tolist())}
                    for c, f in self.frames.items()}
        cols = {c: (f["open"].to_numpy(float), f["close"].to_numpy(float),
                    f["volume"].to_numpy(float), f["pct_chg"].to_numpy(float))
                for c, f in self.frames.items()}
        sigs = lb._scan_day(spec, self.frames, cols, idx_maps, self.dates, 70,
                            {"000003"})
        codes = {c for c, _ in sigs}
        self.assertIn("000001.SZ", codes)
        self.assertNotIn("000003.SZ", codes)   # ST 剔除
        self.assertNotIn("430001.BJ", codes)   # 北证排除
        self.assertNotIn("600000.SH", codes)   # 平盘 pct_chg=0 被预筛除外

    def test_execution_dates_and_prices(self):
        """成交价恒为信号日次日(T+1)开盘价; 卖出恒为 T+1+H 开盘价"""
        dates = self.dates
        r = lb.run_backtest("trend", self.frames,
                            st_codes={"000003"}, hold=5, top_n=2,
                            initial_cash=100000.0)
        # 直接重跑取 trades_sample 不含全部交易, 用内部口径重建校验样本
        for t in r["trades_sample"]:
            sig_i = dates.index(t["signal_date"])
            self.assertEqual(t["entry_date"], dates[sig_i + 1])      # T+1
            f = self.frames["000001.SZ" if t["code"] == "000001"
                            else "000002.SZ"]
            f = f.reset_index(drop=True)
            ei = f.index[f["date"] == t["entry_date"]][0]
            self.assertAlmostEqual(t["entry_price"], float(f["open"].iloc[ei]),
                                   places=3)
            xi = f.index[f["date"] == t["exit_date"]][0]
            # 退出日 = 入场后第5个市场日 (停牌顺延除外)
            self.assertGreaterEqual(dates.index(t["exit_date"]),
                                    dates.index(t["entry_date"]) + 5)
            self.assertAlmostEqual(t["exit_price"], float(f["open"].iloc[xi]),
                                   places=3)
            # 费用口径: pnl = invest*(exit*(1-0.00075)/(entry*(1+0.00025)) - 1)
            invest = 100000.0 / 2
            expect = invest * (float(f["open"].iloc[xi]) * (1 - 0.00075)
                               / (float(f["open"].iloc[ei]) * (1 + 0.00025)) - 1)
            self.assertAlmostEqual(t["pnl"], expect, places=2)

    def test_suspension_skip_and_postpone(self):
        """000002 在 dates[75] 停牌: 该日无入场; dates[70] 入场者顺延至 76 卖出"""
        r = lb.run_backtest("trend", self.frames, st_codes={"000003"},
                            hold=5, top_n=2, initial_cash=100000.0)
        for t in r["trades_sample"]:
            if t["code"] != "000002":
                continue
            self.assertNotEqual(t["entry_date"], self.dates[75])  # T+1停牌信号作废
            if t["entry_date"] == self.dates[70]:
                self.assertEqual(t["exit_date"], self.dates[76])  # 顺延复牌首日

    def test_equity_curve_shape(self):
        r = self.result
        curve = r["equity_curve"]
        spec_warm = lb._SIGNAL_SPEC["trend"]["warmup"]
        self.assertEqual(len(curve), len(self.dates) - (spec_warm - 1))
        self.assertAlmostEqual(curve[0]["equity"], 100000.0, places=2)  # 首日无持仓
        self.assertAlmostEqual(r["metrics"]["final_value"], curve[-1]["equity"],
                               places=2)
        self.assertAlmostEqual(r["metrics"]["total_return"],
                               curve[-1]["equity"] / 100000.0 - 1, places=6)
        self.assertGreater(r["metrics"]["total_turnover"], 0.0)

    def test_window_notes(self):
        w = self.result["window"]
        self.assertEqual(w["panel_days"], 150)
        self.assertEqual(w["warmup_days"], 60)
        self.assertEqual(w["start"], self.dates[59])


class TestAntiLookahead(unittest.TestCase):
    """防未来函数: 突变信号日之后的数据, 既有信号与成交不得改变"""

    def test_engine_invariance_to_future_mutation(self):
        """突变 cutoff 之后的数据: cutoff 之前(含)的净值曲线必须逐日不变 —
        净值由 ≤当日的信号/成交/收盘计值决定, 前缀不变即引擎无未来函数"""
        dates = _mk_dates(90)
        frames = _mk_frames(dates)
        r1 = lb.run_backtest("trend", frames, hold=5, top_n=2,
                             initial_cash=100000.0)
        cutoff = dates[70]
        frames2 = {}
        for c, f in frames.items():
            f2 = f.copy()
            mask = f2["date"] > cutoff
            f2.loc[mask, "open"] = f2.loc[mask, "open"] * 3.0
            f2.loc[mask, "close"] = f2.loc[mask, "close"] * 3.0
            f2.loc[mask, "high"] = f2.loc[mask, "high"] * 3.0
            f2.loc[mask, "low"] = f2.loc[mask, "low"] * 3.0
            f2.loc[mask, "volume"] = f2.loc[mask, "volume"] * 10.0
            f2.loc[mask, "pct_chg"] = 5.0
            frames2[c] = f2
        r2 = lb.run_backtest("trend", frames2, hold=5, top_n=2,
                             initial_cash=100000.0)
        pre1 = [(p["date"], p["equity"]) for p in r1["equity_curve"]
                if p["date"] <= cutoff]
        pre2 = [(p["date"], p["equity"]) for p in r2["equity_curve"]
                if p["date"] <= cutoff]
        self.assertTrue(pre1)
        self.assertEqual(pre1, pre2)

    def test_scan_day_reads_only_past(self):
        """_scan_day(t) 在 t 之后数据任意突变下结果不变"""
        dates = _mk_dates(90)
        frames = _mk_frames(dates)
        spec = lb._SIGNAL_SPEC["trend"]

        def _maps(fs):
            im = {c: {d: i for i, d in enumerate(f["date"].tolist())}
                  for c, f in fs.items()}
            co = {c: (f["open"].to_numpy(float), f["close"].to_numpy(float),
                      f["volume"].to_numpy(float), f["pct_chg"].to_numpy(float))
                  for c, f in fs.items()}
            return im, co

        im1, co1 = _maps(frames)
        s1 = lb._scan_day(spec, frames, co1, im1, dates, 65, set())
        frames3 = {c: f.copy() for c, f in frames.items()}
        for c, f in frames3.items():
            mask = f["date"] > dates[65]
            f.loc[mask, "close"] = 999.0
            f.loc[mask, "open"] = 999.0
            f.loc[mask, "high"] = 1000.0
            f.loc[mask, "low"] = 998.0
            f.loc[mask, "volume"] = 999999.0
            f.loc[mask, "pct_chg"] = 9.0
        im3, co3 = _maps(frames3)
        s3 = lb._scan_day(spec, frames3, co3, im3, dates, 65, set())
        self.assertEqual(s1, s3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
