# -*- coding: utf-8 -*-
"""操作建议 /api/advice 空数据返修离线测试 (2026-11-01)

事故根因: cache_or_fetch 上游失败时返回 {"error": ...} 字典而不抛异常;
generate_advice 用 .get(...,0/"未知") 把失败静默吞成 全0市场情绪+空推荐+空板块,
再被 real_day_cached 6h 日缓存固化 — 用户全天看到"无数据"。

修复契约 (real-only: 拿不到显式不可用, 绝不拿空当 0):
- 市场情绪失败 / 三策略扫描全失败 → generate_advice 抛 RuntimeError
  (API 层映射 502 + data_status=unavailable, 任何层不缓存失败结果)
- 部分扫描或板块源失败 → 真实部分结果 + sources_status 逐项 + degraded=true + note;
  降级结果经 real_day_cached(cacheable=...) 不写 6h 日缓存, 下轮重试
- 扫描全部完成但无共识标的 → recommendations=[] + note 显式说明 (真实空, 非数据缺失)

全程打桩, 无网络、无凭据、无 DB 写。
"""
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import services.advice as adv

SENTIMENT_OK = {"phase": "震荡市➡️", "sentiment_score": 45, "up": 2000,
                "down": 2500, "limit_up": 60, "limit_down": 30}
SECTORS_OK = [{"name": n} for n in ("半导体", "软件服务", "银行", "证券", "医药", "白酒")]


def _picked(code, score=50, stage="鱼身", price=10.0):
    return {"code": code, "name": "测试" + code, "trend_score": score,
            "score": score, "leader_score": score, "grade": "A",
            "stage": stage, "price": price}


def _scan(*codes):
    return {"picked": [_picked(c) for c in codes], "total_scanned": 100,
            "st_filter": "applied"}


_UNSET = object()   # None 也是合法测试输入 (情绪源返回 None 路径), 不能用 None 当缺省哨兵


def _mapping(sentiment=_UNSET, sectors=_UNSET, trend=_UNSET, hybrid=_UNSET, dragon=_UNSET):
    return {
        "sentiment": SENTIMENT_OK if sentiment is _UNSET else sentiment,
        "sectors": SECTORS_OK if sectors is _UNSET else sectors,
        "strategy_trend": _scan("999999", "111111") if trend is _UNSET else trend,
        "strategy_hybrid": _scan("999999", "222222") if hybrid is _UNSET else hybrid,
        "strategy_dragon": _scan("333333") if dragon is _UNSET else dragon,
    }


class AdviceBase(unittest.TestCase):
    def _run(self, mapping):
        def cof(key, fn, ttl=60):
            return mapping[key]
        fake_daily_store = MagicMock()
        fake_daily_store.get_store.side_effect = RuntimeError("offline")
        with patch.object(adv, "cache_or_fetch", cof), \
             patch.object(adv, "get_kline", return_value=None), \
             patch.object(adv, "ma_convergence_score", return_value=None), \
             patch.object(adv, "macd_analysis", return_value=None), \
             patch.object(adv, "detect_patterns", return_value=None), \
             patch.dict(sys.modules, {"data.daily_store": fake_daily_store,
                                      "db": MagicMock()}):
            return adv.generate_advice()


class TestHappyPath(AdviceBase):
    def test_all_sources_ok_real_values(self):
        out = self._run(_mapping())
        m = out["market"]
        # 市场情绪为真实值, 不是全 0/未知
        self.assertEqual(m["phase"], "震荡市➡️")
        self.assertEqual(m["sentiment_score"], 45)
        self.assertEqual(m["up"], 2000)
        self.assertEqual(m["down"], 2500)
        self.assertEqual(m["limit_up"], 60)
        self.assertNotEqual(m["action"], "谨慎")   # 真实 phase 命中档位表
        self.assertEqual(out["top_sectors"], ["半导体", "软件服务", "银行", "证券", "医药"])
        self.assertFalse(out["degraded"])
        self.assertIsNone(out["note"])
        self.assertEqual(out["sources_status"],
                         {"sentiment": "ok", "sectors": "ok", "scan_trend": "ok",
                          "scan_hybrid": "ok", "scan_dragon": "ok"})
        # 999999 趋势+混合共识 → 入推荐
        codes = [r["code"] for r in out["recommendations"]]
        self.assertIn("999999", codes)
        rec = next(r for r in out["recommendations"] if r["code"] == "999999")
        self.assertGreaterEqual(rec["consensus"], 2)
        self.assertIn("趋势", rec["strategies"])
        self.assertIn("混合", rec["strategies"])


class TestHardFailuresRaise(AdviceBase):
    """硬失败 → 抛异常 (API 层 502), 绝不出全 0 冒充"""

    def test_sentiment_failure_raises(self):
        for bad in ({"error": "指数历史不足60条，不能用0分代替缺失"}, None, {}):
            with self.assertRaises(RuntimeError) as ctx:
                self._run(_mapping(sentiment=bad))
            self.assertIn("市场情绪不可用", str(ctx.exception))

    def test_all_scans_failed_raises(self):
        err = {"error": "tushare daily 请求失败: IncompleteRead"}
        with self.assertRaises(RuntimeError) as ctx:
            self._run(_mapping(trend=err, hybrid=err, dragon=err))
        self.assertIn("三策略扫描均不可用", str(ctx.exception))


class TestPartialDegradation(AdviceBase):
    def test_one_scan_unavailable_marked_degraded(self):
        out = self._run(_mapping(trend={"error": "IncompleteRead"}))
        self.assertTrue(out["degraded"])
        self.assertEqual(out["sources_status"]["scan_trend"], "unavailable")
        self.assertEqual(out["sources_status"]["scan_hybrid"], "ok")
        self.assertIn("trend", out["note"])
        # 市场块仍是真实值, 不受扫描降级影响
        self.assertEqual(out["market"]["sentiment_score"], 45)
        self.assertEqual(out["market"]["up"], 2000)
        # 剩余两扫描无共识 (hybrid 999999/222222 vs dragon 333333) → 推荐空但显式
        self.assertIsInstance(out["recommendations"], list)

    def test_sectors_failure_marked_but_market_real(self):
        out = self._run(_mapping(sectors={"error": "sectors down"}))
        self.assertTrue(out["degraded"])
        self.assertEqual(out["sources_status"]["sectors"], "unavailable")
        self.assertEqual(out["top_sectors"], [])
        self.assertIn("板块", out["note"])
        self.assertEqual(out["market"]["up"], 2000)
        codes = [r["code"] for r in out["recommendations"]]
        self.assertIn("999999", codes)   # 共识推荐不受板块失败影响


class TestEmptyConsensus(AdviceBase):
    def test_no_consensus_explicit_note(self):
        """三扫描全部真实完成但互不相交 → 空推荐 + 显式原因, 非空白非降级"""
        out = self._run(_mapping(trend=_scan("111111"), hybrid=_scan("222222"),
                                 dragon=_scan("333333")))
        self.assertFalse(out["degraded"])
        self.assertEqual(out["recommendations"], [])
        self.assertIsNotNone(out["note"])
        self.assertIn("无满足共识", out["note"])
        self.assertIn("真实空结果", out["note"])


class TestDayCacheSkipsDegraded(unittest.TestCase):
    """real_day_cached cacheable 谓词: degraded 结果不写 6h 日缓存"""

    def test_degraded_not_cached_ok_cached(self):
        import local_tushare_data as real
        calls = {"n": 0}

        def fn_degraded():
            calls["n"] += 1
            return {"degraded": True, "n": calls["n"]}

        def fn_ok():
            calls["n"] += 1
            return {"degraded": False, "n": calls["n"]}

        no_degraded = lambda r: not (isinstance(r, dict) and r.get("degraded"))
        with patch.object(real, "_data_date", return_value="20261030"):
            try:
                w = real.real_day_cached("advice_test_deg", fn_degraded,
                                         cacheable=no_degraded)
                r1, r2 = w(), w()
                self.assertNotEqual(r1["n"], r2["n"])   # 每次重算 (未缓存)

                w2 = real.real_day_cached("advice_test_ok", fn_ok,
                                          cacheable=no_degraded)
                r3, r4 = w2(), w2()
                self.assertEqual(r3["n"], r4["n"])      # 正常结果缓存复用
            finally:
                for k in ("daycache:advice_test_deg:20261030",
                          "daycache:advice_test_ok:20261030"):
                    real._cache.pop(k, None)

    def test_default_cacheable_none_unchanged(self):
        """不传 cacheable 时行为与旧版一致 (全缓存)"""
        import local_tushare_data as real
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            return {"degraded": True, "n": calls["n"]}

        with patch.object(real, "_data_date", return_value="20261030"):
            try:
                w = real.real_day_cached("advice_test_legacy", fn)
                r1, r2 = w(), w()
                self.assertEqual(r1["n"], r2["n"])      # 旧行为: 一律缓存
            finally:
                real._cache.pop("daycache:advice_test_legacy:20261030", None)


class TestApiLayer502(unittest.TestCase):
    def test_error_dict_maps_to_502_unavailable(self):
        """generate_advice 抛出 → 外层 cache_or_fetch 错误字典 → 502 显式失败"""
        import api.advice as aa
        from flask import Flask
        app = Flask(__name__)
        app.register_blueprint(aa.bp)
        with patch.object(aa, "cache_or_fetch",
                          return_value={"error": "市场情绪不可用 (不做全0冒充): x"}):
            resp = app.test_client().get("/api/advice")
        self.assertEqual(resp.status_code, 502)
        d = resp.get_json()
        self.assertEqual(d["data_status"], "unavailable")
        self.assertIn("市场情绪不可用", d["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
