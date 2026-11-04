# -*- coding: utf-8 -*-
"""C6 概念源 real 适配置换离线测试 (2026-11-05 翻倍 D7/D8/D10 缺源修复)

根因: data/concept_heat_tracker 走 tushare SDK 直连, 而 real 模式 SDK 已禁用
→ concept=unavailable → D7/D8/D10 不计入总分。修复 = 数据源函数级注入
local_tushare_data 适配 (共享快照/名录缓存)。本组测试全程合成数据, 不触网。
"""
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import local_tushare_data as real


def _snap(codes_pcts):
    return pd.DataFrame({
        "ts_code": [c for c, _ in codes_pcts],
        "pct_chg": [p for _, p in codes_pcts],
        "close": [10.0] * len(codes_pcts),
    })


class TestConceptAdapters(unittest.TestCase):
    def test_hot_stocks_threshold(self):
        snap = _snap([("000001.SZ", 6.0), ("000002.SZ", 2.0), ("600000.SH", 9.8)])
        with patch.object(real, "_daily_snapshot", return_value=snap):
            hot = real.real_concept_hot_stocks("2026-11-04")
        self.assertEqual(len(hot), 2)
        self.assertTrue((hot["pct_chg"] >= 5.0).all())

    def test_hot_stocks_failure_returns_none(self):
        with patch.object(real, "_daily_snapshot", return_value=None):
            self.assertIsNone(real.real_concept_hot_stocks("20261104"))
        with patch.object(real, "_daily_snapshot", side_effect=RuntimeError("x")):
            self.assertIsNone(real.real_concept_hot_stocks("20261104"))
        # 数据日解析失败 (无凭据) 同样显式 None, 不向外抛
        with patch.object(real, "_data_date", side_effect=RuntimeError("no creds")):
            self.assertIsNone(real.real_concept_hot_stocks())

    def test_industry_map_shape(self):
        basic = {"000001.SZ": {"name": "平安银行", "industry": "银行"},
                 "000002.SZ": {"name": "万科A", "industry": ""}}
        with patch.object(real, "real_get_stock_basic", return_value=basic):
            m = real.real_concept_industry_map()
        self.assertEqual(m["000001.SZ"], {"name": "平安银行", "industry": "银行"})
        self.assertEqual(m["000002.SZ"]["industry"], "未知")


class TestConceptEngineWithAdapters(unittest.TestCase):
    """按 local_run 的注入方式替换后, 聚类引擎走本地适配产出完整结构,
    且全程不 import tushare SDK (sys.modules 置 None, 一旦 import 即报错)"""

    def test_clusters_and_scores_without_sdk(self):
        import data.concept_heat_tracker as cht
        # 3 个目标行业各 10 只大涨股; 另 76 只股票散布在独有行业 → 目标行业高超配
        targets = {"半导体": [], "软件": [], "医药": []}
        codes = []
        n = 0
        for ind in targets:
            for _ in range(10):
                n += 1
                code = f"{n:06d}.SZ"
                targets[ind].append(code)
                codes.append((code, 8.0))
        basic = {}
        for ind, cs in targets.items():
            for c in cs:
                basic[c] = {"name": "测试" + c, "industry": ind}
        for i in range(76):
            basic[f"9{i:05d}.SH"] = {"name": f"其他{i}", "industry": f"其他行业{i}"}

        with patch.object(cht, "_get_hot_stocks", real.real_concept_hot_stocks), \
             patch.object(cht, "_get_industry_map", real.real_concept_industry_map), \
             patch.object(real, "_daily_snapshot", return_value=_snap(codes)), \
             patch.object(real, "real_get_stock_basic", return_value=basic), \
             patch.dict(sys.modules, {"tushare": None}):
            result = cht.detect_cross_industry_clusters("20261104")

        self.assertEqual(result.get("source_status"), "ok", result.get("source_note"))
        self.assertTrue(result["clusters"])
        scores = result["stock_scores"]
        self.assertTrue(scores)
        for code, s in scores.items():
            self.assertNotIn(".", code)          # 短码, 与翻倍候选合并键一致
            self.assertIn("d7", s)
            self.assertGreater(s["d7"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
