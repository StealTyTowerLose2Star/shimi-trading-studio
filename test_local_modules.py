"""本地模块级契约测试 (real-only 政策, 2026-10-24 重写)

背景: 本人 2026-10-23 起要求产品只展示真实数据 — mock 不再是可启动模式
(test_real_only_policy.py 已固化: 非 real 直接拒绝启动)。本文件断言
"无凭据/无用途声明时真实模式在各模块边界的显式失败契约":
  - dashboard: 200 聚合但全部数据键 None + _errors 记录 + X-Shimi-Degraded 头
  - 行情端点: 502 + 可读 error (purpose_unconfirmed / TUSHARE_TOKEN), 绝不 200 包错误
  - 策略 mock 端点在 real 模式 403; 交易/越权路径 403; 静态敏感文件 404
  - /api/local/modules 元数据: real_data_only / data_backend / trading_enabled=False
  - lookup/K线: 源失败 502 且两次失败内容一致 (确定性失败, 不抖动)

为确保任何机器上确定性 fail-closed, setUpClass 显式清除 TUSHARE_TOKEN /
SHIMI_MARKET_USE 后再导入 local_run (不触网: 闸门在发起连接前拒绝)。
"""
import importlib
import os
from pathlib import Path
import tempfile
import unittest
import unittest.mock


class LocalModuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        os.environ['SHIMI_RUNTIME_DIR'] = cls.temp.name
        os.environ.pop('TUSHARE_TOKEN', None)
        os.environ.pop('SHIMI_MARKET_USE', None)
        cls.app = importlib.import_module('local_run').app
        cls.client = cls.app.test_client()
        # 事件总览 2026-10-26 启用: 免key源(巨潮/东财公告/财联社/东财724)在出站白名单内,
        # 后台扫描会真实出站 — 测试守住不触网原则, 打桩扫描触发器(仅断言路由级契约)
        import api.market_events as _me
        cls._bg_patch = unittest.mock.patch.object(_me, '_start_bg_scan', lambda: None)
        cls._bg_patch.start()

    @classmethod
    def tearDownClass(cls):
        cls._bg_patch.stop()
        cls.temp.cleanup()

    def test_dashboard_contracts(self):
        response = self.client.get('/api/dashboard')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get('X-Shimi-Degraded'), 'true')
        d = response.json
        for key in ['indices', 'sectors', 'sector_flow', 'hot_stocks',
                    'limit_up', 'sentiment',
                    'strategy_trend', 'strategy_hybrid', 'strategy_dragon']:
            self.assertIsNone(d[key], key)
        keys = [e['key'] for e in (d['_errors'] or [])]
        for key in ['indices', 'sectors', 'sector_flow', 'hot_stocks',
                    'limit_up', 'sentiment']:
            self.assertIn(key, keys)

    def test_read_refresh_and_module_status(self):
        for path in ['/api/indices', '/api/sectors', '/api/sector-flow',
                     '/api/hot-stocks', '/api/sentiment']:
            r = self.client.get(path)
            self.assertEqual(r.status_code, 502, path)
            self.assertTrue(r.json.get('error'), path)
            self.assertEqual(r.headers['X-Shimi-Data-Mode'], 'real-readonly')
        # 策略端点 2026-10-25 起启用真实扫描; 无凭据/无用途声明时必须显式 502
        # (error+data_status=unavailable), 绝不 200 包错误或回退模拟
        for path in ['/api/strategy/trend', '/api/strategy/hybrid', '/api/strategy/dragon',
                     '/api/strategy/trend/refresh', '/api/strategy/hybrid/refresh',
                     '/api/strategy/dragon/refresh']:
            r = self.client.get(path)
            self.assertEqual(r.status_code, 502, path)
            self.assertTrue(r.json.get('error'), path)
            self.assertEqual(r.json.get('data_status'), 'unavailable', path)
        data = self.client.get('/api/local/modules').json
        self.assertTrue(data['real_market_enabled'])
        self.assertEqual(data['data_backend'], 'tushare-eod')
        self.assertEqual(data['source_verification'], 'not_verified')
        self.assertTrue(data['real_data_only'])
        self.assertFalse(data['trading_enabled'])
        self.assertEqual(len(data['modules']), 11)

    def test_blocked_is_not_success_and_keys_are_not_exposed(self):
        # 仍禁用: 美股看板(yfinance)、券商交易写库(单数 /api/trade 上游遗留面)
        self.assertEqual(self.client.get('/api/us/dashboard').status_code, 403)
        self.assertEqual(self.client.post('/api/trade', json={}).status_code, 403)
        # 交易日志 2026-10-26 启用(仅手动记账): 不再 403; 未登录一律 401, 绝不 200 包错误
        self.assertEqual(self.client.get('/api/trades').status_code, 401)
        self.assertEqual(self.client.post('/api/trades', json={}).status_code, 401)
        self.assertEqual(self.client.get('/api/trades/portfolio').status_code, 401)
        # 事件总览 2026-10-26 启用 (免key源): 不再 403; 200 + cache/scanning 占位,
        # 后台扫描已打桩不触网; 响应绝不 200 包 error 字段
        r = self.client.get('/api/market/events')
        self.assertEqual(r.status_code, 200)
        self.assertIn(r.json.get('source'), ('cache', 'scanning'))
        self.assertIsNone(r.json.get('error'))
        # 2026-10-25 启用但无凭据时必须显式失败 (502), 不 200 包错误
        for path in ['/api/advice', '/api/doubler/recommend', '/api/doubler/plan/10k',
                     '/api/doubler/history/refresh', '/api/stock/600519/radar']:
            r = self.client.get(path)
            self.assertEqual(r.status_code, 502, path)
            self.assertTrue(r.json.get('error'), path)
        # 无code两融查询返回提示(不触网); POST 策略刷新仍 403 (仅GET白名单)
        r = self.client.get('/api/margin')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json.get('data'), [])
        self.assertEqual(self.client.post('/api/strategy/trend/refresh').status_code, 403)
        self.assertEqual(self.client.get('/static/.local_token').status_code, 404)

    def test_search_and_kline_fail_closed_deterministic(self):
        r = self.client.get('/api/stock/lookup?q=600')
        self.assertEqual(r.status_code, 502)
        a = self.client.get('/api/stock/600519/kline?days=10')
        b = self.client.get('/api/stock/600519/kline?days=10')
        self.assertEqual(a.status_code, 502)
        self.assertEqual(a.json.get('data_status'), 'unavailable')
        self.assertEqual(a.json, b.json)  # 确定性失败, 不抖动


if __name__ == '__main__':
    unittest.main()
