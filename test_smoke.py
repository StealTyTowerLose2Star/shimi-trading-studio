"""
拾米交易工作室 - 哨兵 · 自动化测试框架
职责: 核心模块的冒烟测试

运行: python3 test_smoke.py
"""
import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


class TestImports(unittest.TestCase):
    """核心模块导入测试"""

    def test_api_blueprints(self):
        """所有API蓝图可导入"""
        modules = [
            "api.market", "api.strategy", "api.advice", "api.trade",
            "api.review", "api.margin", "api.alert", "api.a_stock_cache",
            "api.monitor", "api.doubler",
        ]
        for mod in modules:
            try:
                __import__(mod)
            except Exception as e:
                self.fail(f"{mod} 导入失败: {e}")

    def test_services(self):
        """所有服务模块可导入"""
        from services.strategy import run_trend_scan, run_hybrid_scan, run_dragon_scan
        from services.advice import generate_advice
        from services.alert import ALERT_TYPES, create_alert, check_alerts
        self.assertIn("price_break", ALERT_TYPES)

    def test_haitao(self):
        """美股模块可导入"""
        from haitao.us_fetcher import get_quotes, get_history
        from haitao.us_gold_scanner import gold_score
        self.assertTrue(callable(gold_score))

    def test_data_layer(self):
        """数据层可导入"""
        from data.fetcher import get_latest_date, get_stock_basic
        date = get_latest_date()
        self.assertIsNotNone(date)

    def test_config(self):
        """配置正确加载"""
        import config
        self.assertTrue(hasattr(config, "TUSHARE_TOKEN"))
        self.assertTrue(hasattr(config, "SERVER_PORT"))


class TestCache(unittest.TestCase):
    """缓存层测试"""

    def test_cache_roundtrip(self):
        from cache import cache_set, cache_or_fetch, cache_delete
        cache_set("__test__", {"value": 42}, 60)
        result = cache_or_fetch("__test__", lambda: None, 0)
        self.assertEqual(result, {"value": 42})
        cache_delete("__test__")

    def test_cache_miss(self):
        from cache import cache_or_fetch, cache_delete
        cache_delete("__test_miss__")
        result = cache_or_fetch("__test_miss__", lambda: "fallback", 0)
        self.assertEqual(result, "fallback")


class TestMiddleware(unittest.TestCase):
    """中间件测试"""

    def test_middleware_import(self):
        from middleware import register_middleware
        self.assertTrue(callable(register_middleware))


class TestLogger(unittest.TestCase):
    """日志模块测试"""

    def test_logger(self):
        from logger import get_logger, startup_log
        log = get_logger("test")
        log.info("smoke test")
        self.assertTrue(True)


class TestMonitor(unittest.TestCase):
    """监控模块测试"""

    def test_monitor_status(self):
        from monitor import get_monitor_status
        result = get_monitor_status()
        self.assertIn("cpu", result)
        self.assertIn("memory", result)

    def test_health_deps(self):
        from monitor import check_external_deps
        result = check_external_deps()
        self.assertIn("overall", result)


if __name__ == "__main__":
    print("🧪 拾米交易工作室 · 冒烟测试")
    print("=" * 50)
    # 仅运行快速测试，跳过外部API依赖的测试
    suite = unittest.TestLoader().loadTestsFromModule(__import__(__name__))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    print("=" * 50)
    print(f"通过: {result.testsRun - len(result.failures) - len(result.errors)}/{result.testsRun}")
    if result.wasSuccessful():
        print("✅ 全部通过")
    else:
        print("❌ 存在失败")
        sys.exit(1)
