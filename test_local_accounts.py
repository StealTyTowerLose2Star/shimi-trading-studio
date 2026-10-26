import hashlib
import importlib
import os
from pathlib import Path
import tempfile
import time
import unittest
from contextlib import closing


class LocalAccountTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        os.environ['SHIMI_RUNTIME_DIR'] = cls.temp.name
        cls.app = importlib.import_module('local_run').app
        cls.db = importlib.import_module('db.users_db')

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.client = self.app.test_client()
        with closing(self.db.get_users_db()) as conn:
            conn.execute('DELETE FROM tokens')
            conn.execute('DELETE FROM users')
            conn.commit()

    def post(self, route, data, **kwargs):
        return self.client.post('/api/auth/' + route, json=data, base_url='http://127.0.0.1:17890', **kwargs)

    def me(self, token):
        return self.client.get('/api/auth/me', headers={'Authorization': 'Bearer ' + token}, base_url='http://127.0.0.1:17890')

    def test_register_login_refresh_logout(self):
        self.assertEqual(self.post('register', dict(username='测试用户', password='test-password', display_name='测试昵称')).status_code, 201)
        login = self.post('login', dict(username='测试用户', password='test-password'))
        self.assertEqual(login.status_code, 200)
        self.assertNotIn('_mock', login.json)
        token = login.json['token']
        self.assertEqual(self.me(token).json['user']['display_name'], '测试昵称')
        self.assertEqual(self.me(token).headers['Cache-Control'], 'no-store')
        self.assertEqual(self.post('logout', {}, headers={'Authorization': 'Bearer ' + token}).status_code, 200)
        self.assertEqual(self.me(token).status_code, 401)

    def test_validation_duplicates_and_wrong_password(self):
        for data in ({}, [], {'username': [], 'password': 123}, {'username': 'x', 'password': 'x'}):
            self.assertEqual(self.post('register', data).status_code, 400)
        data = dict(username='same-name', password='test-password')
        self.assertEqual(self.post('register', data).status_code, 201)
        self.assertEqual(self.post('register', data).status_code, 400)
        self.assertEqual(self.post('login', dict(username='same-name', password='wrong')).status_code, 401)
        self.assertEqual(self.post('login', []).status_code, 400)

    def test_hash_and_legacy_upgrade(self):
        self.post('register', dict(username='modern', password='test-password'))
        conn = self.db.get_users_db()
        try:
            stored = conn.execute("SELECT password FROM users WHERE username='modern'").fetchone()[0]
            self.assertTrue(stored.startswith('pbkdf2:sha256:'))
            conn.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)', ('legacy', hashlib.sha256(b'old-password').hexdigest(), 'legacy'))
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(self.post('login', dict(username='legacy', password='old-password')).status_code, 200)
        conn = self.db.get_users_db()
        try:
            self.assertTrue(conn.execute("SELECT password FROM users WHERE username='legacy'").fetchone()[0].startswith('pbkdf2:'))
        finally:
            conn.close()

    def test_expired_session_and_no_local_bypass(self):
        self.post('register', dict(username='expire', password='test-password'))
        token = self.post('login', dict(username='expire', password='test-password')).json['token']
        conn = self.db.get_users_db()
        try:
            conn.execute('UPDATE tokens SET expires_at=?', (time.time() - 10,))
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(self.me(token).status_code, 401)
        self.assertEqual(self.me('local_token').status_code, 401)

    def test_cross_site_and_content_type(self):
        self.assertEqual(self.post('register', dict(username='cross-site', password='test-password'), headers={'Origin': 'http://evil.invalid'}).status_code, 403)
        self.assertEqual(self.client.post('/api/auth/login', data='username=x', base_url='http://127.0.0.1:17890').status_code, 415)
        self.assertEqual(self.client.get('/api/auth/me', base_url='http://evil.invalid:17890').status_code, 403)

    def test_other_features_remain_closed(self):
        # 交易日志 2026-10-26 启用(仅手动记账): /api/trades 移出封闭清单, 未登录须 401
        self.assertEqual(self.client.get('/api/trades').status_code, 401)
        for path in ['/api/health/deps', '/api/sse/stream', '/api/auth/reset-code']:
            self.assertEqual(self.client.get(path).status_code, 403)
        self.assertEqual(self.client.get('/static/.local_token').status_code, 404)


if __name__ == '__main__':
    unittest.main()
