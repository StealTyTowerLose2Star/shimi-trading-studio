"""
拾米交易工作室 - 哨兵 · API端点冒烟测试
运行: python3 test_api.py
"""
import sys, os, unittest, requests, json, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

BASE = "http://127.0.0.1:7890"


def api(path, method="GET", data=None):
    """简化的API调用"""
    kwargs = {"timeout": 10}
    if data:
        kwargs["json"] = data
    fn = getattr(requests, method.lower())
    return fn(f"{BASE}{path}", **kwargs)


class TestHealthEndpoints(unittest.TestCase):
    """健康检查端点"""

    def test_health(self):
        r = api("/api/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "ok")

    def test_health_deps(self):
        r = api("/api/health/deps")
        self.assertEqual(r.status_code, 200)
        self.assertIn("overall", r.json())

    def test_monitor(self):
        r = api("/api/monitor")
        self.assertEqual(r.status_code, 200)
        self.assertIn("cpu", r.json())


class TestMarketEndpoints(unittest.TestCase):
    """市场数据端点"""

    def test_indices(self):
        r = api("/api/indices")
        self.assertEqual(r.status_code, 200)
        self.assertGreater(len(r.json()), 0)

    def test_sectors(self):
        r = api("/api/sectors")
        self.assertEqual(r.status_code, 200)

    def test_sentiment(self):
        r = api("/api/sentiment")
        self.assertEqual(r.status_code, 200)

    def test_dashboard(self):
        r = api("/api/dashboard")
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertIn("indices", d)


class TestStrategyEndpoints(unittest.TestCase):
    """策略端点"""

    def test_strategy_trend(self):
        r = api("/api/strategy/trend")
        self.assertEqual(r.status_code, 200)

    def test_strategy_hybrid(self):
        r = api("/api/strategy/hybrid")
        self.assertEqual(r.status_code, 200)

    def test_strategy_dragon(self):
        r = api("/api/strategy/dragon")
        self.assertEqual(r.status_code, 200)


class TestAlertEndpoints(unittest.TestCase):
    """预警端点"""

    def test_alert_types(self):
        r = api("/api/alert/types")
        self.assertEqual(r.status_code, 200)
        self.assertIn("price_break", r.json()["types"])

    def test_alert_crud(self):
        # Create
        r = api("/api/alert", "POST", {"type": "price_break", "params": {"code": "000001", "threshold": 999, "direction": "above"}})
        self.assertIn(r.status_code, [200, 201])
        aid = r.json().get("id")
        # List
        r = api("/api/alert")
        self.assertEqual(r.status_code, 200)
        # Delete
        if aid:
            api(f"/api/alert/{aid}", "DELETE")


class TestCacheEndpoints(unittest.TestCase):
    """缓存端点"""

    def test_cache_summary(self):
        r = api("/api/a-stock/cache/summary")
        self.assertEqual(r.status_code, 200)

    def test_cache_refresh(self):
        r = api("/api/a-stock/cache/refresh", "POST")
        self.assertIn(r.status_code, [200, 500, 504])  # 504 if timeout


class TestUSEndpoints(unittest.TestCase):
    """美股端点"""

    def test_us_indices(self):
        r = api("/api/us/indices")
        self.assertEqual(r.status_code, 200)

    def test_us_dashboard(self):
        r = api("/api/us/dashboard")
        self.assertEqual(r.status_code, 200)

    def test_us_hot(self):
        r = api("/api/us/hot")
        self.assertEqual(r.status_code, 200)


class TestErrorHandling(unittest.TestCase):
    """错误处理"""

    def test_404_json(self):
        r = api("/api/nonexistent_endpoint_12345")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["error"], "not_found")

    def test_request_id_header(self):
        r = api("/api/health")
        self.assertIn("X-Request-ID", r.headers)


if __name__ == "__main__":
    print("🧪 拾米交易工作室 · API冒烟测试")
    print("=" * 50)
    suite = unittest.TestLoader().loadTestsFromModule(__import__(__name__))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    print("=" * 50)
    print(f"通过: {result.testsRun - len(result.failures) - len(result.errors)}/{result.testsRun}")
    sys.exit(0 if result.wasSuccessful() else 1)
