# -*- coding: utf-8 -*-
"""源状态与缺源评分返修离线测试 (2026-10-27 验收返修)

三项修复, 每项覆盖四路径 (缺源/真实空/正常/异常):
  1. 事件源四态: disabled(凭据缺失/永禁) / unavailable(请求失败) /
     empty(真实空) / ok — sources_status 不得把不可用误记 ok
  2. D7/D8 催化剂缺源: 显式 unknown(null) 不计入总分 + 总分标注缺维度不完整;
     正常有源时数值与旧公式逐分一致
  3. ST 名录缺失: 扫描结果显式标注 degraded_no_namelist, 不得静默当作已过滤

全程 mock, 无网络、无凭据、不写仓库文件。
"""
import os
import sys
import unittest
from unittest.mock import patch, MagicMock, mock_open

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import data.market_events as me
import data.news_fetcher as nf
import services.catalyst_engine as ce


# ────────────────────────────────────────────────────────────
# 1. 事件源四态
# ────────────────────────────────────────────────────────────
class TestSafeFetchFourStates(unittest.TestCase):
    def test_exception_is_unavailable_not_ok(self):
        events, st = me._safe_fetch("eastmoney", lambda: (_ for _ in ()).throw(OSError("dns blocked")))
        self.assertEqual(events, [])
        self.assertEqual(st, "unavailable")

    def test_real_empty_vs_ok_passthrough(self):
        self.assertEqual(me._safe_fetch("eastmoney", lambda: ([], "empty")), ([], "empty"))
        ev = [{"title": "x"}]
        self.assertEqual(me._safe_fetch("eastmoney", lambda: (ev, "ok")), (ev, "ok"))

    def test_timeout_is_unavailable(self):
        import time
        events, st = me._safe_fetch("eastmoney", lambda: time.sleep(2), timeout=0.05)
        self.assertEqual(events, [])
        self.assertEqual(st, "unavailable")


class TestEventSourceDetailed(unittest.TestCase):
    """逐源四态: disabled / unavailable / empty / ok"""

    def test_finnhub_disabled_without_key(self):
        with patch.dict(os.environ, {"FINNHUB_KEY": ""}):
            events, st = me.fetch_us_events_detailed()
        self.assertEqual((events, st), ([], "disabled"))

    def test_finnhub_unavailable_on_request_failure(self):
        with patch.dict(os.environ, {"FINNHUB_KEY": "k"}), \
             patch("requests.get", side_effect=OSError("blocked")):
            events, st = me.fetch_us_events_detailed()
        self.assertEqual((events, st), ([], "unavailable"))

    def test_finnhub_ok_and_empty(self):
        payload_ok = [{"headline": "Fed cuts rates regulation", "summary": "fed reserve",
                       "related": "AAPL", "datetime": 1761465600}]
        with patch.dict(os.environ, {"FINNHUB_KEY": "k"}):
            with patch("requests.get", return_value=MagicMock(status_code=200, json=lambda: payload_ok)):
                events, st = me.fetch_us_events_detailed()
            self.assertEqual(st, "ok")
            self.assertTrue(events and events[0]["source"] == "finnhub")
            with patch("requests.get", return_value=MagicMock(status_code=200, json=lambda: [])):
                events, st = me.fetch_us_events_detailed()
            self.assertEqual((events, st), ([], "empty"))

    def test_yfinance_disabled_when_lib_missing_or_whitelisted(self):
        # venv 未安装 yfinance → disabled; real 模式白名单外 → disabled
        events, st = me.fetch_yfinance_earnings_detailed()
        self.assertEqual((events, st), ([], "disabled"))
        # 本机 venv 未装 yfinance → 原因应指向库未安装或白名单永禁
        reason = me._yfinance_disabled_reason() or ""
        self.assertTrue("未安装" in reason or "白名单" in reason, reason)

    def test_yfinance_three_states_when_enabled(self):
        with patch.object(me, "_yfinance_disabled_reason", return_value=None):
            with patch.object(me, "_fetch_yfinance_earnings_core", side_effect=RuntimeError("boom")):
                self.assertEqual(me.fetch_yfinance_earnings_detailed(), ([], "unavailable"))
            with patch.object(me, "_fetch_yfinance_earnings_core", return_value=[]):
                self.assertEqual(me.fetch_yfinance_earnings_detailed(), ([], "empty"))
            with patch.object(me, "_fetch_yfinance_earnings_core", return_value=[{"title": "e"}]):
                self.assertEqual(me.fetch_yfinance_earnings_detailed(), ([{"title": "e"}], "ok"))

    def test_cninfo_four_states(self):
        with patch("data.cninfo_fetcher.fetch_monthly_catalysts", side_effect=OSError("down")):
            self.assertEqual(me.fetch_cn_events_detailed(), ([], "unavailable"))
        # 全部关键词失败 → unavailable, 不得以空冒充
        with patch("data.cninfo_fetcher.fetch_monthly_catalysts",
                   return_value={"events": [], "status": "unavailable", "queried": 40, "failed": 40}):
            self.assertEqual(me.fetch_cn_events_detailed(), ([], "unavailable"))
        with patch("data.cninfo_fetcher.fetch_monthly_catalysts",
                   return_value={"events": [], "status": "empty", "queried": 40, "failed": 0}):
            self.assertEqual(me.fetch_cn_events_detailed(), ([], "empty"))
        with patch("data.cninfo_fetcher.fetch_monthly_catalysts",
                   return_value={"events": [{"sec_code": "00001", "type": "C5_资产重组",
                                             "title": "重大资产重组", "date": "2026-10-20"}],
                                 "status": "ok", "queried": 40, "failed": 0}):
            events, st = me.fetch_cn_events_detailed()
            self.assertEqual(st, "ok")
            self.assertEqual(events[0]["code"], "000001")  # 5位补零

    def test_flash_news_four_states(self):
        # 两子源全失败 → unavailable
        with patch("data.news_fetcher.fetch_market_news_detailed",
                   return_value=([], {"cls": "unavailable", "eastmoney724": "unavailable"})):
            self.assertEqual(me.fetch_flash_news_detailed()[1], "unavailable")
        # 有响应但无可分类事件 → empty (真实空)
        with patch("data.news_fetcher.fetch_market_news_detailed",
                   return_value=([], {"cls": "ok", "eastmoney724": "empty"})):
            self.assertEqual(me.fetch_flash_news_detailed()[1], "empty")
        # 有可分类事件 → ok
        rows = [{"title": "央行宣布降准", "content": "释放流动性",
                 "time": "2026-10-26 12:30:00", "source": "cls"}]
        with patch("data.news_fetcher.fetch_market_news_detailed",
                   return_value=(rows, {"cls": "ok", "eastmoney724": "ok"})):
            events, st, sub = me.fetch_flash_news_detailed()
            self.assertEqual(st, "ok")
            self.assertEqual(events[0]["type"], "policy")
            self.assertEqual(sub["cls"], "ok")
        # 聚合层异常 → unavailable
        with patch("data.news_fetcher.fetch_market_news_detailed", side_effect=RuntimeError("x")):
            self.assertEqual(me.fetch_flash_news_detailed()[1], "unavailable")


class TestScanMarketEventsStatus(unittest.TestCase):
    """聚合层: sources_status 四态齐备, 不可用/禁用 ok=False, 禁用带原因"""

    def _run_scan(self, env_key=""):
        with patch.dict(os.environ, {"FINNHUB_KEY": env_key}), \
             patch.object(me, "_yfinance_disabled_reason", return_value="yf 禁用(测试)"), \
             patch.object(me, "fetch_cn_events_detailed", return_value=([{"title": "c"}], "ok")), \
             patch.object(me, "fetch_eastmoney_news_detailed", side_effect=OSError("down")), \
             patch.object(me, "fetch_flash_news_detailed",
                          return_value=([], "empty", {"cls": "ok", "eastmoney724": "empty"})), \
             patch.object(me, "fetch_us_events_detailed", return_value=([], "unavailable")), \
             patch.object(me, "_save_events", lambda e: None), \
             patch.object(me, "map_events_to_stocks", lambda u: []), \
             patch.object(me, "_push_event_alerts", lambda s: None):
            return me.scan_market_events()

    def test_four_states_present_and_honest(self):
        out = self._run_scan()
        ss = out["sources_status"]
        self.assertEqual(set(ss), {"cninfo", "eastmoney", "flash_news", "finnhub", "yfinance"})
        self.assertEqual(ss["cninfo"]["status"], "ok")
        self.assertTrue(ss["cninfo"]["ok"])
        self.assertEqual(ss["cninfo"]["count"], 1)
        # 请求失败: unavailable 且 ok=False (返修点: 此前误记 ok=True)
        self.assertEqual(ss["eastmoney"]["status"], "unavailable")
        self.assertFalse(ss["eastmoney"]["ok"])
        # 真实空: empty 且 ok=True (成功但无事件)
        self.assertEqual(ss["flash_news"]["status"], "empty")
        self.assertTrue(ss["flash_news"]["ok"])
        self.assertEqual(ss["flash_news"]["detail"]["cls"], "ok")
        # 凭据缺失: disabled 不发请求, ok=False 带原因
        self.assertEqual(ss["finnhub"]["status"], "disabled")
        self.assertFalse(ss["finnhub"]["ok"])
        self.assertIn("FINNHUB_KEY", ss["finnhub"]["reason"])
        # 永禁: disabled 带原因
        self.assertEqual(ss["yfinance"]["status"], "disabled")
        self.assertFalse(ss["yfinance"]["ok"])
        self.assertTrue(ss["yfinance"]["reason"])
        # 每条均带中文 label 供前端直显
        for entry in ss.values():
            self.assertIn(entry["label"], ("正常", "正常·空", "不可用", "禁用"))


# ────────────────────────────────────────────────────────────
# 2. D7/D8 催化剂缺源 (catalyst_engine 状态传播 + doubler 门控)
# ────────────────────────────────────────────────────────────
class TestCatalystSourceStatus(unittest.TestCase):
    def test_sub_source_status_mapping(self):
        self.assertEqual(ce._sub_source_status({"error": "x"}), "unavailable")
        self.assertEqual(ce._sub_source_status(None), "unavailable")
        self.assertEqual(ce._sub_source_status({"source_status": "empty"}), "empty")
        self.assertEqual(ce._sub_source_status({"stock_scores": {"000001": {}}}), "ok")
        self.assertEqual(ce._sub_source_status({"stock_scores": {}}), "empty")

    def test_monthly_cninfo_status(self):
        # 两个月全异常 → unavailable
        with patch("services.catalyst_engine.fetch_monthly_catalysts", side_effect=OSError("down")):
            out = ce.scan_monthly_catalyst_scores("202610", "2026-10-26")
        self.assertEqual(out["sources_status"]["cninfo"]["status"], "unavailable")
        # 全部关键词失败 (cninfo 层) → unavailable, 不得以空冒充
        with patch("services.catalyst_engine.fetch_monthly_catalysts",
                   return_value={"events": [], "status": "unavailable"}):
            out = ce.scan_monthly_catalyst_scores("202610", "2026-10-26")
        self.assertEqual(out["sources_status"]["cninfo"]["status"], "unavailable")
        # 真实空 → empty
        with patch("services.catalyst_engine.fetch_monthly_catalysts",
                   return_value={"events": [], "status": "empty"}):
            out = ce.scan_monthly_catalyst_scores("202610", "2026-10-26")
        self.assertEqual(out["sources_status"]["cninfo"]["status"], "empty")

    def test_scan_all_catalysts_aggregates_status(self):
        monthly = {"stock_scores": {}, "sources_status": {"cninfo": {"status": "ok", "months": {}}}}
        with patch.object(ce, "scan_monthly_catalyst_scores", return_value=monthly), \
             patch("data.policy_scanner.fetch_policy_catalyst_scores",
                   return_value={"stock_scores": {}, "source_status": "empty"}), \
             patch("data.commodity_tracker.fetch_commodity_catalyst_scores",
                   return_value={"error": "sectors down"}), \
             patch("data.concept_heat_tracker.fetch_concept_catalyst_scores",
                   side_effect=RuntimeError("boom")):
            out = ce.scan_all_catalysts("202610", "2026-10-26")
        ss = out["sources_status"]
        self.assertEqual(ss["cninfo"]["status"], "ok")
        self.assertEqual(ss["policy"]["status"], "empty")
        self.assertEqual(ss["commodity"]["status"], "unavailable")  # error 键
        self.assertEqual(ss["concept"]["status"], "unavailable")    # 抛异常


class TestDoublerCatalystGating(unittest.TestCase):
    """recommend_current_month 四路径: 缺源→unknown+不完整标注; 真实空→合法0分;
    正常→数值与旧公式一致; 异常→引擎异常标注"""

    def _daily_df(self):
        import pandas as pd
        rows = []
        for i, code in enumerate(["000001.SZ", "600519.SH", "300750.SZ"]):
            rows.append({"ts_code": code, "close": 10.0 + i, "pct_chg": 2.0 + i,
                         "vol": 1e6, "amount": 5e8})
        return pd.DataFrame(rows)

    def _basic(self):
        return {c: {"name": n, "industry": "软件服务"}
                for c, n in [("000001.SZ", "平安银行"), ("600519.SH", "贵州茅台"),
                             ("300750.SZ", "宁德时代")]}

    def _run_doubler(self, cat_result=None, cat_exc=None, basic=None):
        import data.fetcher as fetcher
        import services.doubler_scanner as ds
        import realtime_scorer

        pro_shim = MagicMock()
        pro_shim.daily_basic.side_effect = RuntimeError("no creds offline")

        patches = [
            patch.object(ds, "_get_pro", return_value=pro_shim),
            patch.object(fetcher, "get_daily", return_value=self._daily_df()),
            patch.object(fetcher, "get_latest_date", return_value="20261026"),
            patch.object(ds, "_early_stage_score_batch",
                         return_value={c: {"score": 1, "level": "early", "reason": "",
                                           "exclude": False, "mtm_gain": 3.0}
                                       for c in ("000001", "600519", "300750")}),
            patch.object(realtime_scorer, "get_kline_batch", return_value={}),
            patch.object(ds, "push_doubler_picks_to_alerts", lambda r: None),
            patch("os.path.exists", return_value=False),       # 无月度锁定
            patch("builtins.open", mock_open()),               # 不写 picks 文件
        ]
        if basic is None:
            patches.append(patch.object(fetcher, "get_stock_basic",
                                        side_effect=RuntimeError("namelist down")))
        else:
            patches.append(patch.object(fetcher, "get_stock_basic", return_value=basic))
        if cat_exc is not None:
            patches.append(patch("services.catalyst_engine.scan_all_catalysts",
                                 side_effect=cat_exc))
        else:
            patches.append(patch("services.catalyst_engine.scan_all_catalysts",
                                 return_value=cat_result))
        patches.append(patch("data.pre_surge_signals.fetch_pre_surge_signals",
                             return_value={"dragon_tiger": {}, "moneyflow": {}}))
        plan_mod = MagicMock()
        patches.append(patch.dict(sys.modules, {"services.plan_1w": plan_mod}))

        for p in patches:
            p.start()
        try:
            # 内部依赖均为函数内 import / 模块属性，patch 打在源模块上即生效，
            # 无需 reload(reload 还会把 _get_pro 恢复成真身)
            return ds.recommend_current_month()
        finally:
            for p in patches:
                p.stop()

    def test_missing_source_marks_incomplete_and_unknown(self):
        cat = {"stock_scores": {"000001": {"d7": 15.0, "d8": 7, "top_event_type": "C5_资产重组",
                                           "resonance": True}},
               "sources_status": {"cninfo": {"status": "unavailable"},
                                  "policy": {"status": "ok"},
                                  "commodity": {"status": "ok"},
                                  "concept": {"status": "ok"}}}
        out = self._run_doubler(cat_result=cat, basic=self._basic())
        self.assertFalse(out["score_complete"])
        self.assertIn("缺源", out["score_note"])
        self.assertIn("cninfo", out["score_note"])
        c0 = next(c for c in out["top30"] if c["code"] == "000001")
        self.assertIsNone(c0["catalyst"]["d7"])
        self.assertIsNone(c0["catalyst"]["d8"])
        self.assertIsNone(c0["catalyst"]["d10"])
        self.assertIsNone(c0["potential"])
        # 缺源时总分不含催化剂: score = base + d0(1) + d9
        self.assertEqual(c0["score"], c0["base_score"] + 1 + c0["score_breakdown"]["pre_surge_d9"])

    def test_real_empty_sources_are_complete_with_zero(self):
        cat = {"stock_scores": {},
               "sources_status": {k: {"status": "empty"}
                                  for k in ("cninfo", "policy", "commodity", "concept")}}
        out = self._run_doubler(cat_result=cat, basic=self._basic())
        self.assertTrue(out["score_complete"])
        self.assertIsNone(out["score_note"])
        c0 = out["top30"][0]
        self.assertEqual(c0["catalyst"]["d7"], 0)   # 真实无催化剂 = 合法 0 分
        self.assertIsNotNone(c0["potential"])

    def test_normal_scores_match_legacy_formula(self):
        cat = {"stock_scores": {"000001": {"d7": 15.0, "d8": 7, "top_event_type": "C5_资产重组",
                                           "resonance": True}},
               "sources_status": {k: {"status": "ok"}
                                  for k in ("cninfo", "policy", "commodity", "concept")}}
        out = self._run_doubler(cat_result=cat, basic=self._basic())
        self.assertTrue(out["score_complete"])
        c0 = next(c for c in out["top30"] if c["code"] == "000001")
        d9 = c0["score_breakdown"]["pre_surge_d9"]
        # 旧公式: base + d0 + d7 + d8 + d9 + d10 (共振 → d10=2)
        self.assertEqual(c0["score"], c0["base_score"] + 1 + 15.0 + 7 + d9 + 2)
        self.assertEqual(c0["catalyst_d7"], 15.0)

    def test_engine_exception_marks_incomplete(self):
        out = self._run_doubler(cat_exc=RuntimeError("engine boom"), basic=self._basic())
        self.assertFalse(out["score_complete"])
        self.assertIn("引擎异常", out["score_note"])
        for c in out["top30"]:
            self.assertIsNone(c["catalyst"]["d7"])

    def test_codex_counterexample_missing_three_source_keys(self):
        """Codex 独立反例 (2026-10-27 二轮): 仅 cninfo=ok、缺其余三个必需源键,
        bool(dict) 不得误判完整 — 四必需源逐一要求合法成功状态"""
        cat = {"stock_scores": {},
               "sources_status": {"cninfo": {"status": "ok"}}}
        out = self._run_doubler(cat_result=cat, basic=self._basic())
        self.assertFalse(out["score_complete"])
        self.assertIn("缺源", out["score_note"])
        for k in ("policy", "commodity", "concept"):
            self.assertIn(k, out["score_note"])
        for c in out["top30"]:
            self.assertIsNone(c["catalyst"]["d7"])
            self.assertIsNone(c["potential"])

    def test_disabled_and_illegal_status_marked_incomplete(self):
        """disabled / 非法状态 / None 条目一律不完整 (合法成功仅 ok/empty)"""
        variants = [
            {"cninfo": {"status": "ok"}, "policy": {"status": "disabled"},
             "commodity": {"status": "ok"}, "concept": {"status": "ok"}},
            {"cninfo": {"status": "ok"}, "policy": {"status": "weird"},
             "commodity": {"status": "empty"}, "concept": {"status": "ok"}},
            {"cninfo": {"status": "ok"}, "policy": None,
             "commodity": {"status": "ok"}, "concept": {"status": "empty"}},
        ]
        for ss in variants:
            out = self._run_doubler(cat_result={"stock_scores": {}, "sources_status": ss},
                                    basic=self._basic())
            self.assertFalse(out["score_complete"], ss)
            self.assertIn("policy", out["score_note"], ss)


# ────────────────────────────────────────────────────────────
# 3. ST 名录缺失显式降级
# ────────────────────────────────────────────────────────────
class TestStNamelistDegradation(unittest.TestCase):
    def _daily_df(self):
        import pandas as pd
        return pd.DataFrame([
            {"ts_code": "000001.SZ", "close": 10.0, "pct_chg": 2.0, "vol": 1e6, "amount": 5e8},
            {"ts_code": "400001.SZ", "close": 5.0, "pct_chg": 3.0, "vol": 1e6, "amount": 6e8},
        ])

    def _basic_with_st(self):
        return {"000001.SZ": {"name": "平安银行", "industry": "银行"},
                "400001.SZ": {"name": "*ST测试", "industry": "银行"}}

    def _run_trend(self, basic):
        import services.strategy as strat
        with patch.object(strat, "get_daily", return_value=self._daily_df()), \
             patch.object(strat, "get_stock_basic", return_value=basic), \
             patch.object(strat, "get_kline_batch", return_value={}), \
             patch.object(strat, "trend_detect", return_value=None):
            return strat.run_trend_scan()

    def test_namelist_missing_marks_degraded(self):
        for bad in (None, {}, "junk"):
            out = self._run_trend(bad)
            self.assertEqual(out["st_filter"], "degraded_no_namelist")
            self.assertIn("ST 过滤未生效", out["st_note"])

    def test_namelist_failure_exception_marks_degraded(self):
        import services.strategy as strat
        with patch.object(strat, "get_daily", return_value=self._daily_df()), \
             patch.object(strat, "get_stock_basic", side_effect=RuntimeError("down")), \
             patch.object(strat, "get_kline_batch", return_value={}), \
             patch.object(strat, "trend_detect", return_value=None):
            out = strat.run_trend_scan()
        self.assertEqual(out["st_filter"], "degraded_no_namelist")

    def test_namelist_ok_marks_applied_and_filters(self):
        out = self._run_trend(self._basic_with_st())
        self.assertEqual(out["st_filter"], "applied")
        self.assertIsNone(out["st_note"])
        # ST 标的被剔除: 预筛选候选不含 400001
        self.assertEqual(out["total_scanned"], 1)

    def test_hybrid_and_dragon_carry_fields(self):
        import services.strategy as strat
        with patch.object(strat, "get_daily", return_value=self._daily_df()), \
             patch.object(strat, "get_stock_basic", return_value=None), \
             patch.object(strat, "get_kline_batch", return_value={}), \
             patch.object(strat, "hybrid_score", return_value=None), \
             patch.object(strat, "dragon_leader_score", return_value=None):
            out_h = strat.run_hybrid_scan()
            out_d = strat.run_dragon_scan()
        self.assertEqual(out_h["st_filter"], "degraded_no_namelist")
        self.assertEqual(out_d["st_filter"], "degraded_no_namelist")

    def test_doubler_namelist_missing_annotated(self):
        cat = {"stock_scores": {},
               "sources_status": {k: {"status": "empty"}
                                  for k in ("cninfo", "policy", "commodity", "concept")}}
        out = TestDoublerCatalystGating()._run_doubler(cat_result=cat, basic=None)
        self.assertEqual(out["st_filter"], "degraded_no_namelist")
        self.assertIn("ST 过滤未生效", out["st_note"])
        # 名录正常时
        out2 = TestDoublerCatalystGating()._run_doubler(cat_result=cat, basic={
            "000001.SZ": {"name": "平安银行", "industry": "银行"},
            "600519.SH": {"name": "贵州茅台", "industry": "白酒"},
            "300750.SZ": {"name": "宁德时代", "industry": "电气设备"}})
        self.assertEqual(out2["st_filter"], "applied")
        self.assertIsNone(out2["st_note"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
