"""
拾米交易工作室 - 业务逻辑测试
test_services.py — 策略评分 + 操作建议 + 预警系统
"""
import sys, os, unittest
sys.path.insert(0, os.path.dirname(__file__))


class TestStrategyEngine(unittest.TestCase):
    """策略引擎测试"""

    def test_trend_scan_structure(self):
        from services.strategy import run_trend_scan
        result = run_trend_scan()
        self.assertIsInstance(result, dict)
        self.assertIn("picked", result)
        self.assertIn("total_scanned", result)
        # engine key only present when tushare data available
        if "engine" in result:
            self.assertIsInstance(result["engine"], str)

    def test_hybrid_scan_structure(self):
        from services.strategy import run_hybrid_scan
        result = run_hybrid_scan()
        self.assertIsInstance(result, dict)
        self.assertIn("picked", result)

    def test_dragon_scan_structure(self):
        from services.strategy import run_dragon_scan
        result = run_dragon_scan()
        self.assertIsInstance(result, dict)
        self.assertIn("picked", result)


class TestAdviceEngine(unittest.TestCase):
    """操作建议测试"""

    def test_generate_advice_structure(self):
        from services.advice import generate_advice
        result = generate_advice()
        self.assertIsInstance(result, dict)
        self.assertIn("market", result)
        market = result["market"]
        self.assertIn("phase", market)
        self.assertIn("position", market)
        self.assertIn("action", market)

    def test_atr_levels_calculation(self):
        from services.advice import calc_atr_based_levels
        t1, t2, t3, sl, trail_start, trail_step = calc_atr_based_levels(100, 5)
        # ATR=5, price=100
        # T1=100+2*5=110, T2=100+3.5*5=117.5, T3=100+6*5=130, SL=100-1.5*5=92.5
        self.assertAlmostEqual(t1, 110.0)
        self.assertAlmostEqual(t2, 117.5)
        self.assertAlmostEqual(t3, 130.0)
        self.assertAlmostEqual(sl, 92.5)
        self.assertEqual(trail_start, 110.0)
        self.assertAlmostEqual(trail_step, 2.5)


class TestAlertEngine(unittest.TestCase):
    """预警系统测试"""

    def test_alert_types(self):
        from services.alert import ALERT_TYPES
        self.assertIn("price_break", ALERT_TYPES)
        self.assertIn("pct_change", ALERT_TYPES)
        self.assertIn("volume_surge", ALERT_TYPES)
        self.assertIn("strategy_signal", ALERT_TYPES)

    def test_alert_crud(self):
        from services.alert import create_alert, list_alerts, get_alert, delete_alert
        # Create
        a = create_alert("price_break", {"code": "000001", "threshold": 999, "direction": "above"})
        self.assertIn("id", a)
        aid = a["id"]
        # Get
        self.assertIsNotNone(get_alert(aid))
        # List
        alerts = list_alerts()
        self.assertGreaterEqual(len(alerts), 1)
        # Delete
        self.assertTrue(delete_alert(aid))
        self.assertIsNone(get_alert(aid))


class TestMarketEvents(unittest.TestCase):
    """市场事件测试"""

    def test_event_classification(self):
        from data.market_events import _classify_event
        r1 = _classify_event("国务院发布新基建政策 支持5G建设")
        self.assertEqual(r1, "policy")
        r2 = _classify_event("美联储FOMC利率决议 降息25bp")
        self.assertEqual(r2, "macro")

    def test_direction_inference(self):
        from data.market_events import _infer_direction
        self.assertEqual(_infer_direction("policy", "利好政策出台"), "long")
        self.assertEqual(_infer_direction("macro", "市场暴跌 经济衰退"), "short")


class TestCache(unittest.TestCase):
    """缓存测试"""

    def test_cache_write_read_delete(self):
        from cache import cache_set, cache_or_fetch, cache_delete
        cache_set("__test_unit__", {"v": 99}, 60)
        result = cache_or_fetch("__test_unit__", lambda: None, 0)
        self.assertEqual(result, {"v": 99})
        cache_delete("__test_unit__")
        result2 = cache_or_fetch("__test_unit__", lambda: "miss", 0)
        self.assertEqual(result2, "miss")


if __name__ == "__main__":
    print("🧪 业务逻辑测试")
    unittest.main(verbosity=2)
