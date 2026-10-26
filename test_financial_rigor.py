"""financial_rigor 移植模块测试 (ai-berkshire, MIT)

纯函数确定性 + 雷达估值复算分析器端到端 (模拟 pro, 无网络)。
"""
import unittest

import pandas as pd

import services.financial_rigor as fr
import services.stock_radar as sr
import data.fetcher_core as fc


class TestPureFunctions(unittest.TestCase):
    def test_market_cap_exact(self):
        r = fr.market_cap_check(1335.0, 125620.0, 167702700.0)
        self.assertEqual(r["status"], "pass")
        self.assertEqual(r["deviation_pct"], 0.0)

    def test_market_cap_fail(self):
        r = fr.market_cap_check(100.0, 1000.0, 200000.0)
        self.assertEqual(r["status"], "fail")

    def test_valuation_metrics(self):
        m = fr.valuation_metrics(1335.0, eps=65.86, bvps=201.35)
        self.assertAlmostEqual(m["PE"], 20.27, places=2)
        self.assertAlmostEqual(m["PB"], 6.63, places=2)
        self.assertAlmostEqual(m["ROE"], 32.71, places=2)

    def test_cross_validate(self):
        cv = fr.cross_validate("PE", {"官方": 20.27, "复算": 20.25}, tolerance_pct=15.0)
        self.assertTrue(cv["all_consistent"])
        cv2 = fr.cross_validate("PE", {"官方": 20.0, "复算": 30.0}, tolerance_pct=15.0)
        self.assertFalse(cv2["all_consistent"])

    def test_three_scenario_deterministic(self):
        a = fr.three_scenario_valuation(1335.0, 65.86, 0.20, 0.15, 0.05,
                                        24.32, 20.27, 16.22, years=3)
        b = fr.three_scenario_valuation(1335.0, 65.86, 0.20, 0.15, 0.05,
                                        24.32, 20.27, 16.22, years=3)
        self.assertEqual(a, b)
        names = [s["name"] for s in a["scenarios"]]
        self.assertEqual(names, ["乐观", "中性", "悲观"])
        # 乐观目标价 > 中性 > 悲观
        prices = [s["target_price"] for s in a["scenarios"]]
        self.assertTrue(prices[0] > prices[1] > prices[2])

    def test_benford_small_sample(self):
        self.assertIsNone(fr.benford_check([1, 2, 3]))


class _FakePro:
    def daily_basic(self, ts_code=None, fields=""):
        return pd.DataFrame([{
            "ts_code": ts_code, "trade_date": "20261023", "close": 1335.0,
            "pe": 20.27, "pb": 6.64, "total_share": 125620.0,
            "total_mv": 167702700.0}])

    def fina_indicator(self, ts_code=None, fields=""):
        return pd.DataFrame([
            {"end_date": "20231231", "eps": 58.0, "bps": 170.0, "ocfps": 60.0,
             "roe": 30.0, "netprofit_yoy": 18.0},
            {"end_date": "20240930", "eps": 44.0, "bps": 185.0, "ocfps": 46.0,
             "roe": None, "netprofit_yoy": 15.0},
            {"end_date": "20241231", "eps": 65.86, "bps": 201.35, "ocfps": 70.0,
             "roe": 32.0, "netprofit_yoy": 15.4},
            {"end_date": "20250930", "eps": 48.5, "bps": 215.0, "ocfps": 50.0,
             "roe": None, "netprofit_yoy": 10.2}])


class _LossPro(_FakePro):
    def fina_indicator(self, ts_code=None, fields=""):
        df = super().fina_indicator(ts_code, fields)
        df["eps"] = [-5.0, -3.0, -8.0, -2.0]
        return df


class TestValuationCheckAnalyzer(unittest.TestCase):
    def setUp(self):
        self._orig = fc.get_ts

    def tearDown(self):
        fc.get_ts = self._orig

    def test_end_to_end_and_deterministic(self):
        fc.get_ts = lambda: _FakePro()
        a = sr.analyze_valuation_check("600519")
        b = sr.analyze_valuation_check("600519")
        self.assertEqual(a, b)
        self.assertEqual(a["market_cap_check"]["status"], "pass")
        self.assertTrue(a["pe_cross"]["all_consistent"])
        self.assertIsNone(a["inputs"]["roe_reported"])  # NaN 被过滤
        # TTM = 48.5 + 65.86 - 44.0 = 70.36 → PE 复算 ≈ 18.97
        pe_calc = [s for s in a["pe_cross"]["sources"] if s["source"] == "复算eps_ttm"][0]
        self.assertAlmostEqual(pe_calc["value"], 18.97, places=2)
        self.assertAlmostEqual(
            a["three_scenario"]["scenarios"][1]["growth"], 0.102, places=6)

    def test_loss_making_stock_no_scenario(self):
        fc.get_ts = lambda: _LossPro()
        c = sr.analyze_valuation_check("000001")
        self.assertEqual(c["three_scenario"], "数据不足")
        self.assertEqual(c["pe_cross"], "数据不足")

    def test_missing_data_explicit(self):
        class _EmptyPro:
            def daily_basic(self, ts_code=None, fields=""):
                return pd.DataFrame()

            def fina_indicator(self, ts_code=None, fields=""):
                return pd.DataFrame()
        fc.get_ts = lambda: _EmptyPro()
        c = sr.analyze_valuation_check("600519")
        self.assertEqual(c["market_cap_check"], "数据不足")
        self.assertEqual(c["three_scenario"], "数据不足")


if __name__ == "__main__":
    unittest.main()
