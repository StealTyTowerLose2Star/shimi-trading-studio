# -*- coding: utf-8 -*-
"""ST 过滤离线测试 — 选股/扫描模块候选池剔除名称含 ST 的证券 (2026-10-26)

覆盖:
  services/strategy.py      run_trend_scan / run_hybrid_scan / run_dragon_scan
  services/doubler_scanner.py scan_monthly_doublers (历史月扫同样剔除)
  主扫描候选循环的名称级过滤 (修复ts_code永不匹配的无效过滤)

全程 mock 数据源, 无网络、无凭据要求。
"""
import sys
import os
import unittest
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ─── mock 数据 ─────────────────────────────────
# 正常股 + ST股 + *ST股 + 北交所; ST股故意给高涨幅/涨停, 确保若漏过滤必进结果
DAILY_ROWS = [
    {"ts_code": "600519.SH", "pct_chg": 3.0, "amount": 9e5, "close": 1800.0, "vol": 1e4},
    {"ts_code": "000980.SZ", "pct_chg": 4.9, "amount": 9e6, "close": 5.0, "vol": 1e6},   # ST众泰
    {"ts_code": "600666.SH", "pct_chg": 4.8, "amount": 8e6, "close": 3.0, "vol": 1e6},   # *ST奥瑞德(虚构名)
    {"ts_code": "000001.SZ", "pct_chg": 2.5, "amount": 8e5, "close": 12.0, "vol": 1e5},  # 平安银行
    {"ts_code": "430047.BJ", "pct_chg": 6.0, "amount": 9e7, "close": 8.0, "vol": 1e5},   # 北交所
]
DAILY_LIMIT_ROWS = [  # dragon 用: 涨停场景
    {"ts_code": "600519.SH", "pct_chg": 10.0, "amount": 9e5, "close": 1800.0, "vol": 1e4},
    {"ts_code": "000980.SZ", "pct_chg": 10.0, "amount": 9e6, "close": 5.0, "vol": 1e6},  # ST涨停
    {"ts_code": "600666.SH", "pct_chg": 10.0, "amount": 8e6, "close": 3.0, "vol": 1e6},
]
BASIC = {
    "600519.SH": {"name": "贵州茅台", "industry": "白酒"},
    "000980.SZ": {"name": "ST众泰", "industry": "汽车"},
    "600666.SH": {"name": "*ST奥瑞", "industry": "电子"},
    "000001.SZ": {"name": "平安银行", "industry": "银行"},
    "430047.BJ": {"name": "诺思兰德", "industry": "医药"},
}


def _daily(rows=None):
    return pd.DataFrame(rows if rows is not None else DAILY_ROWS)


def _no_st(picked, where):
    names = [p.get("name", "") for p in picked]
    bad = [n for n in names if "ST" in n.upper()]
    return bad == [], f"{where} 结果含ST: {bad} (全部名称={names})"


class TestExcludeStHelper(unittest.TestCase):
    def test_helper_filters_st_and_keeps_others(self):
        from services.strategy import _exclude_st
        out = _exclude_st(_daily(), BASIC)
        codes = set(out["ts_code"])
        self.assertIn("600519.SH", codes)
        self.assertIn("000001.SZ", codes)
        self.assertIn("430047.BJ", codes)  # 北交所非ST, 本函数不剔
        self.assertNotIn("000980.SZ", codes)
        self.assertNotIn("600666.SH", codes)

    def test_helper_passthrough_on_bad_input(self):
        from services.strategy import _exclude_st
        self.assertIsNone(_exclude_st(None, BASIC))
        df = _daily()
        self.assertIs(_exclude_st(df, None), df)      # 名录缺失不过滤(显式保留)
        self.assertIs(_exclude_st(df, "junk"), df)


class TestStrategyScansNoSt(unittest.TestCase):
    def _run(self, fn_name, scorer_name, scorer_ret, rows=None):
        import services.strategy as st
        with patch.object(st, "get_daily", return_value=_daily(rows)), \
             patch.object(st, "get_stock_basic", return_value=BASIC), \
             patch.object(st, "get_kline_batch", return_value={}), \
             patch.object(st, scorer_name, return_value=scorer_ret):
            return getattr(st, fn_name)()

    def test_trend_scan_no_st(self):
        ret = {"total_score": 50, "price": 10.0, "stage": "up",
               "strength": 1, "trend_formed": True}
        r = self._run("run_trend_scan", "trend_detect", ret)
        self.assertGreater(len(r["picked"]), 0)
        ok, msg = _no_st(r["picked"], "trend")
        self.assertTrue(ok, msg)

    def test_hybrid_scan_no_st(self):
        ret = {"score": 20, "price": 10.0, "pct_chg": 2.0, "grade": "A",
               "position_pct": 0.5, "dimensions": {}}
        r = self._run("run_hybrid_scan", "hybrid_score", ret)
        self.assertGreater(len(r["picked"]), 0)
        ok, msg = _no_st(r["picked"], "hybrid")
        self.assertTrue(ok, msg)

    def test_dragon_scan_no_st_even_when_limit_up(self):
        ret = {"leader_score": 30, "price": 10.0, "pct_chg": 10.0,
               "grade": "A", "board_count": 1, "dimensions": {}}
        r = self._run("run_dragon_scan", "dragon_leader_score", ret,
                      rows=DAILY_LIMIT_ROWS)
        self.assertGreater(len(r["picked"]), 0)
        ok, msg = _no_st(r["picked"], "dragon")
        self.assertTrue(ok, msg)


class TestDoublerMonthlyNoSt(unittest.TestCase):
    def test_scan_monthly_doublers_excludes_st(self):
        import services.doubler_scanner as ds
        # 首日快照需 ≥500 行, 否则触发函数内置的 fallback 跳日逻辑; 重复行无妨(价格dict同码覆盖)
        first = pd.DataFrame(
            [{"ts_code": "600519.SH", "close": 1000.0}] * 300
            + [{"ts_code": "000980.SZ", "close": 2.0}] * 300)
        last = pd.DataFrame([
            {"ts_code": "600519.SH", "close": 2100.0},   # +110%
            {"ts_code": "000980.SZ", "close": 4.5},      # +125% ST也翻倍, 必须被剔
        ])
        bounds = {"202510": {"first": "20251001", "last": "20251024"}}
        with patch.object(ds, "_get_month_boundaries", return_value=bounds), \
             patch.object(ds, "_get_daily_safe",
                          side_effect=lambda d: first if d == "20251001" else last), \
             patch("data.fetcher.get_stock_basic", return_value=BASIC):
            r = ds.scan_monthly_doublers()
        codes = [d["code"] for d in r["doublers"]]
        names = [d["name"] for d in r["doublers"]]
        self.assertIn("600519", codes)
        self.assertNotIn("000980", codes)
        self.assertFalse(any("ST" in n.upper() for n in names), f"月扫含ST: {names}")


class TestDoublerMainScanNameFilter(unittest.TestCase):
    """主扫描候选循环: 确认名称级ST过滤生效 (修复ts_code无效检查)"""

    def test_main_scan_candidate_loop_excludes_st(self):
        import services.doubler_scanner as ds
        import inspect
        src = inspect.getsource(ds)
        # 源码契约: 候选循环按名录名称过滤, 而不是查 ts_code
        self.assertIn('"ST" in str(info.get("name", "")).upper()', src)
        self.assertNotIn('if "ST" in code or close <= 0', src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
