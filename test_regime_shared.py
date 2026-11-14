# -*- coding: utf-8 -*-
"""index_regime_ok 统一门槛离线对照 (2026-11-12 Codex I4 返修)
覆盖: 平盘/下跌/刚站上MA但斜率下降/涨幅不足/历史不足/观察与回测一致性
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import local_backtest as lb
from test_backtest import _mk_dates, _mk_frames


def _closes(kind, dates):
    if kind == "flat":
        return {d: 100.0 for d in dates}
    if kind == "down":
        return {d: 100.0 - i for i, d in enumerate(dates)}
    if kind == "up":
        return {d: 100.0 + i for i, d in enumerate(dates)}
    if kind == "whipsaw":
        return {d: 100.0 + (10 if i % 2 == 0 else -10) for i, d in enumerate(dates)}
    raise ValueError(kind)


SPEC = {"require_index_above_ma": 20, "require_index_ma_rising": 5,
        "require_index_gain_20d": 0.03}


class TestSharedRegime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dates = _mk_dates(90)
        cls.bench = {"name": "x", "code": "x", "closes": _closes("up", cls.dates)}

    def test_flat_blocked(self):
        closes = _closes("flat", self.dates)
        self.assertFalse(lb.index_regime_ok({"closes": closes}, self.dates[-1], SPEC))

    def test_declining_blocked(self):
        closes = _closes("down", self.dates)
        self.assertFalse(lb.index_regime_ok({"closes": closes}, self.dates[-1], SPEC))

    def test_above_ma_but_falling_slope_blocked(self):
        # 后段持续下跌: 刚站上MA但斜率下降 → 拦截
        closes = {d: (100.0 + i if i < 70 else 170.0 - (i - 69) * 2)
                  for i, d in enumerate(self.dates)}
        self.assertFalse(lb.index_regime_ok({"closes": closes}, self.dates[-1], SPEC))

    def test_gain_insufficient_blocked(self):
        # 指数缓涨(累计<3%) → 拦截
        closes = {d: 100.0 + i * 0.01 for i, d in enumerate(self.dates)}
        self.assertFalse(lb.index_regime_ok({"closes": closes}, self.dates[-1], SPEC))

    def test_history_insufficient_pass_with_note(self):
        closes = {self.dates[0]: 100.0}
        # 历史不足20日: 放行 (非"满足全部条件", 由调用方标注)
        self.assertFalse(lb.index_regime_ok({"closes": closes}, self.dates[0], SPEC))

    def test_strong_uptrend_allowed(self):
        self.assertTrue(lb.index_regime_ok(self.bench, self.dates[-1], SPEC))

    def test_observe_matches_backtest_regime(self):
        import iteration.candidate_daily as cd
        spec = dict(lb._SIGNAL_SPEC["trend"])
        spec.update(cd.CANDIDATE)
        day = self.dates[-1]
        self.assertEqual(cd._regime_ok(self.bench, day, spec),
                         lb.index_regime_ok(self.bench, day, spec))

    def test_observe_candidate_empty_when_regime_off(self):
        import iteration.candidate_daily as cd
        frames = _mk_frames(self.dates)
        flat_bench = {"closes": _closes("flat", self.dates)}
        with patch.object(cd, "_load_real_inputs",
                          return_value=(frames, {}, set(), flat_bench)):
            rec = cd.main()
        self.assertFalse(rec["regime_ok"])
        self.assertEqual(rec["candidate_picks"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
