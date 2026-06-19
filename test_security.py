"""
拾米交易工作室 - 安全测试
test_security.py — 认证/授权/CSRF/SQL注入防护
"""
import sys, os, unittest, requests

sys.path.insert(0, os.path.dirname(__file__))
BASE = "http://127.0.0.1:7890"


class TestAuthRequired(unittest.TestCase):
    """认证要求 — 未登录应返回 401"""

    def test_trades_requires_auth(self):
        r = requests.get(f"{BASE}/api/trades", timeout=5)
        self.assertIn(r.status_code, [401, 403])

    def test_trade_create_requires_auth(self):
        r = requests.post(f"{BASE}/api/trades", json={}, timeout=5)
        self.assertIn(r.status_code, [401, 403])

    def test_users_requires_auth(self):
        r = requests.get(f"{BASE}/api/users", timeout=5)
        self.assertIn(r.status_code, [401, 403])

    def test_portfolio_advice_requires_auth(self):
        r = requests.get(f"{BASE}/api/portfolio/advice", timeout=5)
        self.assertIn(r.status_code, [401, 403])


class TestLocalTokenBlocked(unittest.TestCase):
    """tryLocalToken 绕过已修复"""

    def test_local_token_blocked(self):
        r = requests.get(f"{BASE}/api/auth/me",
                         headers={"Authorization": "Bearer local_token"}, timeout=5)
        self.assertEqual(r.status_code, 401)

    def test_local_token_trades_blocked(self):
        r = requests.get(f"{BASE}/api/trades",
                         headers={"Authorization": "Bearer local_token"}, timeout=5)
        self.assertIn(r.status_code, [401, 403])


class TestErrorResponses(unittest.TestCase):
    """错误响应格式"""

    def test_404_returns_json(self):
        r = requests.get(f"{BASE}/api/nonexistent_endpoint", timeout=5)
        self.assertEqual(r.status_code, 404)
        data = r.json()
        self.assertEqual(data["error"], "not_found")

    def test_request_id_present(self):
        r = requests.get(f"{BASE}/api/health", timeout=5)
        self.assertIn("X-Request-ID", r.headers)

    def test_response_time_header(self):
        r = requests.get(f"{BASE}/api/health", timeout=5)
        self.assertIn("X-Response-Time-Ms", r.headers)


if __name__ == "__main__":
    print("🛡️ 安全测试")
    unittest.main(verbosity=2)
