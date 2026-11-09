# -*- coding: utf-8 -*-
"""walk-forward 多窗口离线测试 (2026-11-08 I1)
关键契约: 窗口划分正确; 窗口之后的数据突变不影响该窗口结果 (防未来函数);
汇总数学正确; include_trades 附全量成交。全程合成数据不触网。
"""
import copy
import os
import sys
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import local_backtest as lb
from test_backtest import _mk_dates, _mk_frames


class TestWalkForward(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dates = _mk_dates(90)
        cls.frames = _mk_frames(cls.dates)
        cls.bench = {"name": "沪深300", "code": "000300.SH",
                     "closes": {d: 100.0 for d in cls.dates}}
        cls.wf = lb.run_walk_forward("trend", cls.frames, n_windows=3,
                                     hold=5, top_n=2, initial_cash=100000.0,
                                     benchmark=cls.bench, include_trades=True)

    def test_window_split_contiguous(self):
        ws = self.wf["windows"]
        self.assertEqual(len(ws), 3)
        for i, w in enumerate(ws):
            self.assertEqual(w["window"], i + 1)
            self.assertLessEqual(w["start"], w["end"])
            if i:
                self.assertGreater(w["start"], ws[i - 1]["start"])
                self.assertGreaterEqual(w["start"], ws[i - 1]["end"])

    def test_summary_math(self):
        s = self.wf["summary"]
        valid = [w for w in self.wf["windows"] if w["excess_pp"] is not None]
        self.assertEqual(s["n_windows"], len(valid))
        self.assertEqual(s["positive_excess_windows"],
                         sum(1 for w in valid if w["excess_pp"] > 0))
        import statistics
        self.assertAlmostEqual(s["median_excess_pp"],
                               round(statistics.median(w["excess_pp"] for w in valid), 2), places=2)
        self.assertIn("gate_note", s)

    def test_no_future_leakage_into_window(self):
        mutated = copy.deepcopy(self.frames)
        first_end = self.wf["windows"][0]["end"]
        for code, f in mutated.items():
            mask = f["date"] > first_end
            f.loc[mask, ["open", "high", "low", "close"]] *= 5.0   # 窗口后价格突变
            f.loc[mask, "volume"] *= 100.0
        wf2 = lb.run_walk_forward("trend", mutated, n_windows=3, hold=5, top_n=2,
                                  initial_cash=100000.0, benchmark=self.bench,
                                  include_trades=True)
        self.assertEqual(self.wf["windows"][0], wf2["windows"][0])   # 窗口1结果对窗口后数据突变不变

    def test_include_trades_full_list(self):
        for w in self.wf["windows"]:
            self.assertIn("trades_all", w)
            self.assertIsInstance(w["trades_all"], list)
            self.assertIn("equity_curve", w)

    def test_insufficient_days_rejected(self):
        with self.assertRaises(ValueError):
            lb.run_walk_forward("trend", self.frames, n_windows=30)

    def test_spec_override_single_variable(self):
        # I3 单变量假设: threshold 提高 → 信号不增; 未知参数显式拒绝
        wf2 = lb.run_walk_forward("trend", self.frames, n_windows=3,
                                  spec_override={"threshold": 99999.0})
        total = sum(w["trades_completed"] for w in wf2["windows"])
        base = sum(w["trades_completed"] for w in self.wf["windows"])
        self.assertLessEqual(total, base)
        with self.assertRaises(ValueError):
            lb.run_walk_forward("trend", self.frames, n_windows=3,
                                spec_override={"not_a_param": 1})

    def test_prior_gain_filter(self):
        # I3 假设2: 合成稳定上涨趋势(+1%/日, 60日涨幅约81%)
        # 有60日历史的信号日全部过滤; 仅首个无60日历史的信号日(dates[59])可过;
        # max=100 → 放行; 默认 None 不改变基线行为
        wf50 = lb.run_walk_forward("trend", self.frames, n_windows=3,
                                   hold=5, top_n=2, initial_cash=100000.0,
                                   spec_override={"max_prior_gain_pct": 50.0},
                                   include_trades=True)
        sig_days = {t["signal_date"] for w in wf50["windows"] for t in w["trades_all"]}
        self.assertTrue(sig_days and sig_days <= {self.dates[59]}, sig_days)
        wf100 = lb.run_walk_forward("trend", self.frames, n_windows=3,
                                    hold=5, top_n=2, initial_cash=100000.0,
                                    spec_override={"max_prior_gain_pct": 100.0})
        base = sum(w["trades_completed"] for w in self.wf["windows"])
        self.assertEqual(sum(w["trades_completed"] for w in wf100["windows"]), base)

    def test_stop_loss_early_exit(self):
        # E1: 买入后暴跌15% → 提前止损(持有<5日); 稳定上涨不受影响
        frames = copy.deepcopy(self.frames)
        frames["000001.SZ"].loc[frames["000001.SZ"].index >= 62,
                                ["open", "high", "low", "close"]] *= 0.85
        wf = lb.run_walk_forward("trend", frames, n_windows=3, hold=5, top_n=2,
                                 initial_cash=100000.0,
                                 spec_override={"stop_loss_pct": -0.07}, include_trades=True)
        early = [t for w in wf["windows"] for t in w["trades_all"]
                 if t["symbol"] == "000001" and t["holding_bars"] < 5]
        self.assertTrue(early)
        wf_base = lb.run_walk_forward("trend", self.frames, n_windows=3, hold=5, top_n=2,
                                      initial_cash=100000.0,
                                      spec_override={"stop_loss_pct": -0.07})
        base = sum(w["trades_completed"] for w in self.wf["windows"])
        self.assertEqual(sum(w["trades_completed"] for w in wf_base["windows"]), base)

    def test_exit_below_ma(self):
        # E2: 后期持续阴跌破MA20 → 提前离场(持有<5日)
        frames = copy.deepcopy(self.frames)
        f = frames["000001.SZ"]
        f.loc[f.index >= 62, ["open", "high", "low", "close"]] *= 0.90
        wf = lb.run_walk_forward("trend", frames, n_windows=3, hold=5, top_n=2,
                                 initial_cash=100000.0,
                                 spec_override={"exit_below_ma": 20}, include_trades=True)
        early = [t for w in wf["windows"] for t in w["trades_all"]
                 if t["symbol"] == "000001" and t["holding_bars"] < 5]
        self.assertTrue(early)

    def test_vol_price_confirm(self):
        # E4: 阴线不入选 — 000001改全阴线(open>close)则其交易消失; 全阳线不受影响
        frames = copy.deepcopy(self.frames)
        f = frames["000001.SZ"]
        f["open"] = f["close"] * 1.02
        wf = lb.run_walk_forward("trend", frames, n_windows=3, hold=5, top_n=2,
                                 initial_cash=100000.0,
                                 spec_override={"vol_price_confirm": True}, include_trades=True)
        symbols = {t["symbol"] for w in wf["windows"] for t in w["trades_all"]}
        self.assertNotIn("000001", symbols)
        wf2 = lb.run_walk_forward("trend", self.frames, n_windows=3, hold=5, top_n=2,
                                  initial_cash=100000.0,
                                  spec_override={"vol_price_confirm": True})
        base = sum(w["trades_completed"] for w in self.wf["windows"])
        self.assertEqual(sum(w["trades_completed"] for w in wf2["windows"]), base)

    def test_breakout_no_pullback(self):
        # E3: 合成数据当日最低恒低于前高 → 全部过滤(0交易); 默认关闭不受影响
        wf = lb.run_walk_forward("trend", self.frames, n_windows=3, hold=5, top_n=2,
                                 initial_cash=100000.0,
                                 spec_override={"breakout_no_pullback": True})
        self.assertEqual(sum(w["trades_completed"] for w in wf["windows"]), 0)

    def test_market_regime_gate(self):        # I3 结构假设: 基准持续深跌(有MA20历史后) → 全部暂停开仓;
        # 常数基准 → 闸门不拦截, 与基线一致
        closes = {d: (100.0 - i) for i, d in enumerate(self.dates)}  # 持续下跌, 收盘恒低于MA20
        wf = lb.run_walk_forward("trend", self.frames, n_windows=3, hold=5, top_n=2,
                                 initial_cash=100000.0,
                                 benchmark={"name": "x", "code": "x", "closes": closes},
                                 spec_override={"require_index_above_ma": 20})
        self.assertEqual(sum(w["trades_completed"] for w in wf["windows"]), 0)
        wf2 = lb.run_walk_forward("trend", self.frames, n_windows=3, hold=5, top_n=2,
                                  initial_cash=100000.0, benchmark=self.bench,
                                  spec_override={"require_index_above_ma": 20})
        base = sum(w["trades_completed"] for w in self.wf["windows"])
        self.assertEqual(sum(w["trades_completed"] for w in wf2["windows"]), base)


if __name__ == "__main__":
    unittest.main(verbosity=2)
