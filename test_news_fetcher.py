# -*- coding: utf-8 -*-
"""news_fetcher 离线测试 — 财联社/东财7×24 快讯移植契约 (2026-10-26)

覆盖:
  - 财联社 sign 本地签名确定性 (md5(sha1(排序query)), 零 key)
  - 两源响应解析结构 ({title, content, time, source})
  - 失败契约: 网络异常 → 返回 [] (不抛, 聚合器按 sources_status 显式呈现)
  - market_events.fetch_flash_news: 事件分类只保留可识别类型, 噪音丢弃

全程 mock requests, 无网络、无凭据要求。
"""
import sys
import os
import json
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import data.news_fetcher as nf


def _resp(payload):
    m = MagicMock()
    m.json.return_value = payload
    m.text = json.dumps(payload, ensure_ascii=False)
    return m


CLS_PAYLOAD = {
    "errno": 0,
    "data": {"roll_data": [
        {"ctime": 1761465600, "title": "央行宣布降准0.5个百分点", "content": "中国人民银行决定下调金融机构存款准备金率", "brief": ""},
        {"ctime": 1761465900, "title": "", "content": "", "brief": "两市融资余额增加120亿元"},
    ]},
}

EM724_PAYLOAD = {
    "data": {"fastNewsList": [
        {"title": "工信部发布新基建专项规划", "summary": "涉及数据中心与人工智能基础设施" * 20, "showTime": "2026-10-26 12:30:00"},
        {"title": "国际金价创新高", "summary": "现货黄金突破2800美元", "showTime": "2026-10-26 12:31:00"},
    ]},
}


class TestClsTelegraph(unittest.TestCase):
    def test_sign_is_deterministic_and_local(self):
        """sign = md5(sha1(按key字典序拼接的query串)), 同一参数重算必一致"""
        import hashlib
        params = {"appName": "CailianpressWeb", "os": "web", "sv": "7.7.5",
                  "last_time": "", "refresh_type": "1", "rn": "50"}
        qs = "&".join(f"{k}={params[k]}" for k in sorted(params))
        expect = hashlib.md5(hashlib.sha1(qs.encode()).hexdigest().encode()).hexdigest()
        seen = {}

        def fake_get(url, **kw):
            seen["url"] = url
            return _resp(CLS_PAYLOAD)

        with patch.object(nf.requests, "get", side_effect=fake_get):
            nf.cls_telegraph(50)
        self.assertIn(f"sign={expect}", seen["url"])
        self.assertTrue(seen["url"].startswith("https://www.cls.cn/v1/roll/get_roll_list?"))

    def test_parse_rows(self):
        with patch.object(nf.requests, "get", return_value=_resp(CLS_PAYLOAD)):
            rows = nf.cls_telegraph(10)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["source"], "cls")
        self.assertIn("降准", rows[0]["title"])
        self.assertRegex(rows[0]["time"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
        # brief 兜底: title 空时取 brief
        self.assertEqual(rows[1]["title"], "两市融资余额增加120亿元")

    def test_failure_returns_empty_list(self):
        with patch.object(nf.requests, "get", side_effect=OSError("dns blocked")):
            self.assertEqual(nf.cls_telegraph(10), [])


class TestEastmoney724(unittest.TestCase):
    def test_parse_rows(self):
        with patch.object(nf.requests, "get", return_value=_resp(EM724_PAYLOAD)):
            rows = nf.eastmoney_global_news(10)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["source"], "eastmoney724")
        self.assertEqual(rows[0]["time"], "2026-10-26 12:30:00")
        self.assertLessEqual(len(rows[0]["content"]), 200)  # summary 截断200

    def test_failure_returns_empty_list(self):
        with patch.object(nf.requests, "get", side_effect=OSError("dns blocked")):
            self.assertEqual(nf.eastmoney_global_news(10), [])


class TestFlashNewsClassification(unittest.TestCase):
    def test_fetch_flash_news_keeps_only_classified(self):
        import data.market_events as me
        fake_news = [
            {"title": "央行宣布降准0.5个百分点", "content": "释放长期流动性",
             "time": "2026-10-26 12:30:00", "source": "cls"},
            {"title": "某明星今日举办演唱会", "content": "娱乐新闻与财经无关",
             "time": "2026-10-26 12:31:00", "source": "cls"},
            {"title": "国际金价创新高 黄金股异动", "content": "现货黄金突破2800美元",
             "time": "2026-10-26 12:32:00", "source": "eastmoney724"},
        ]
        with patch("data.news_fetcher.fetch_market_news", return_value=fake_news):
            events = me.fetch_flash_news()
        self.assertEqual(len(events), 2)  # 娱乐噪音被丢弃
        types = {e["type"] for e in events}
        self.assertIn("policy", types)       # 降准 → 政策
        self.assertTrue({"commodity", "sector"} & types)  # 黄金 → 商品或行业
        for e in events:
            self.assertEqual(e["market"], "A")
            self.assertEqual(e["date"], "2026-10-26")
            self.assertEqual(e["time"], "12:30" if "降准" in e["title"] else e["time"])

    def test_fetch_flash_news_source_failure_safe(self):
        import data.market_events as me
        with patch("data.news_fetcher.fetch_market_news", side_effect=RuntimeError("boom")):
            self.assertEqual(me.fetch_flash_news(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
