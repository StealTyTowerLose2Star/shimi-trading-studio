# -*- coding: utf-8 -*-
"""交易日志离线测试 — 手动记账 CRUD + 移动加权平均成本 + real-only 行情失败契约 (2026-10-26)

范围红线: 仅手动录入/修改/删除流水; 无券商API/自动下单/买卖建议/webhook。
成本口径: 移动加权平均 (买入费用摊入成本, 卖出费用抵减已实现盈亏); 清仓后成本价重置。
行情: price_getter 返回 None 时 quote_status=unavailable 且浮盈为 None, 绝不用 0 冒充。

全程临时 SQLite + mock price_getter, 无网络、无凭据要求。
"""
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import local_trade_journal as tj


class TradeJournalBase(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

        def _get_db():
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            return conn

        patcher = patch.object(tj, "get_db", side_effect=_get_db)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(lambda: os.path.exists(self.db_path) and os.remove(self.db_path))
        tj._ensure_table()
        self.uid = 1

    def _buy(self, qty, price, fee=0, date="2026-10-20", code="600519"):
        r = tj.add_entry(self.uid, {"date": date, "code": code, "side": "buy",
                                    "price": price, "qty": qty, "fee": fee})
        self.assertNotIn("error", r, r)
        return r

    def _sell(self, qty, price, fee=0, date="2026-10-21", code="600519"):
        return tj.add_entry(self.uid, {"date": date, "code": code, "side": "sell",
                                       "price": price, "qty": qty, "fee": fee})


class TestCrud(TradeJournalBase):
    def test_add_list_update_delete(self):
        r = self._buy(100, 1318.49, fee=5.0)
        self.assertEqual(r["side"], "buy")
        entries = tj.list_entries(self.uid)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["qty"], 100)
        # 修改
        u = tj.update_entry(r["id"], self.uid, {"price": 1320.0, "note": "修正价"})
        self.assertNotIn("error", u, u)
        self.assertEqual(tj.list_entries(self.uid)[0]["price"], 1320.0)
        # 删除
        d = tj.delete_entry(r["id"], self.uid)
        self.assertEqual(d, {"deleted": r["id"]})
        self.assertEqual(tj.list_entries(self.uid), [])
        # 删不存在
        self.assertIn("error", tj.delete_entry(999, self.uid))

    def test_validation_rejects_bad_input(self):
        for bad in [
            {"date": "2026/10/20", "code": "600519", "side": "buy", "price": 10, "qty": 100},
            {"date": "2026-10-20", "code": "60051", "side": "buy", "price": 10, "qty": 100},
            {"date": "2026-10-20", "code": "600519", "side": "hold", "price": 10, "qty": 100},
            {"date": "2026-10-20", "code": "600519", "side": "buy", "price": 0, "qty": 100},
            {"date": "2026-10-20", "code": "600519", "side": "buy", "price": 10, "qty": 0},
            {"date": "2026-10-20", "code": "600519", "side": "buy", "price": 10, "qty": 100, "fee": -1},
            {"date": "2026-02-30", "code": "600519", "side": "buy", "price": 10, "qty": 100},
        ]:
            r = tj.add_entry(self.uid, bad)
            self.assertIn("error", r, bad)
        self.assertEqual(tj.list_entries(self.uid), [])


class TestMovingAverageCost(TradeJournalBase):
    def test_partial_sell_keeps_cost_basis(self):
        """买100@10(fee5) + 买100@12(fee5) → 均价11.05; 卖100@13(fee5) → 已实现190,
        余100股成本价11.05不变; 清仓卖100@14 → 已实现再+295 合计485"""
        self._buy(100, 10.0, fee=5.0, date="2026-10-20")
        self._buy(100, 12.0, fee=5.0, date="2026-10-21")
        pos = tj.compute_positions(
            sorted(tj.list_entries(self.uid), key=lambda e: (e["date"], e["id"])))
        self.assertAlmostEqual(pos["600519"]["avg_cost"], 11.05, places=4)
        r = self._sell(100, 13.0, fee=5.0, date="2026-10-22")
        self.assertNotIn("error", r, r)
        pos = tj.compute_positions(
            sorted(tj.list_entries(self.uid), key=lambda e: (e["date"], e["id"])))
        p = pos["600519"]
        self.assertEqual(p["qty"], 100)
        self.assertAlmostEqual(p["avg_cost"], 11.05, places=4)   # 部分卖出成本价不变
        self.assertAlmostEqual(p["realized"], 190.0, places=2)   # (13-11.05)*100-5
        r = self._sell(100, 14.0, date="2026-10-23")
        self.assertNotIn("error", r, r)
        pos = tj.compute_positions(
            sorted(tj.list_entries(self.uid), key=lambda e: (e["date"], e["id"])))
        p = pos["600519"]
        self.assertEqual(p["qty"], 0)
        self.assertEqual(p["avg_cost"], 0.0)                      # 清仓重置
        self.assertAlmostEqual(p["realized"], 190.0 + 295.0, places=2)

    def test_oversell_rejected(self):
        self._buy(100, 10.0)
        r = self._sell(200, 11.0)
        self.assertIn("error", r)
        self.assertIn("超出", r["error"])
        # 无持仓直接卖
        r = tj.add_entry(self.uid, {"date": "2026-10-20", "code": "000001",
                                    "side": "sell", "price": 10, "qty": 100})
        self.assertIn("error", r)

    def test_update_breaking_consistency_rejected(self):
        a = self._buy(100, 10.0, date="2026-10-20")
        self._buy(100, 12.0, date="2026-10-21")
        self._sell(150, 13.0, date="2026-10-22")   # 余50
        # 把第一笔改成 10 股 → 卖出150时持仓只有110 → 拒绝
        r = tj.update_entry(a["id"], self.uid, {"qty": 10})
        self.assertIn("error", r)
        # 改成 60 股 → 60+100=160 ≥ 150 合法 → 放行; 再改回 100
        r = tj.update_entry(a["id"], self.uid, {"qty": 60})
        self.assertNotIn("error", r, r)
        r = tj.update_entry(a["id"], self.uid, {"qty": 100})
        self.assertNotIn("error", r, r)
        entries = tj.list_entries(self.uid)
        self.assertEqual(len(entries), 3)
        self.assertEqual([e["qty"] for e in sorted(entries, key=lambda e: e["id"])],
                         [100, 100, 150])

    def test_delete_buy_breaking_consistency_rejected(self):
        a = self._buy(100, 10.0, date="2026-10-20")
        self._buy(100, 12.0, date="2026-10-21")
        self._sell(150, 13.0, date="2026-10-22")
        r = tj.delete_entry(a["id"], self.uid)
        self.assertIn("error", r)
        self.assertEqual(len(tj.list_entries(self.uid)), 3)   # 未删除
        # 删卖出记录则合法
        sell_id = [e for e in tj.list_entries(self.uid) if e["side"] == "sell"][0]["id"]
        r = tj.delete_entry(sell_id, self.uid)
        self.assertEqual(r, {"deleted": sell_id})


class TestPortfolioView(TradeJournalBase):
    def setUp(self):
        super().setUp()
        self._buy(100, 10.0, fee=5.0, date="2026-10-20")   # 600519 均价10.05
        self._buy(100, 20.0, date="2026-10-20", code="000001")

    def test_real_price_floating_pnl(self):
        view = tj.portfolio_view(self.uid, lambda code: (15.0, "2026-10-24"))
        by_code = {p["code"]: p for p in view["positions"]}
        m = by_code["600519"]
        self.assertEqual(m["quote_status"], "ok")
        self.assertEqual(m["last_price"], 15.0)
        self.assertEqual(m["price_date"], "2026-10-24")
        self.assertAlmostEqual(m["avg_cost"], 10.05, places=4)
        self.assertAlmostEqual(m["unrealized_pnl"], (15.0 - 10.05) * 100, places=2)
        self.assertAlmostEqual(m["unrealized_pct"], (15.0 / 10.05 - 1) * 100, places=2)
        z = by_code["000001"]
        self.assertAlmostEqual(z["unrealized_pnl"], (15.0 - 20.0) * 100, places=2)
        s = view["summary"]
        self.assertEqual(s["positions_count"], 2)
        self.assertEqual(s["quote_available"], "2/2")
        self.assertAlmostEqual(s["total_unrealized_pnl"],
                               (15 - 10.05) * 100 + (15 - 20) * 100, places=2)
        self.assertIn("EOD", s["price_kind"])

    def test_unavailable_quote_explicitly_null(self):
        view = tj.portfolio_view(self.uid, lambda code: None)
        for p in view["positions"]:
            if p["qty"] > 0:
                self.assertEqual(p["quote_status"], "unavailable")
                self.assertIsNone(p["last_price"])
                self.assertIsNone(p["unrealized_pnl"])
                self.assertIsNone(p["market_value"])
        s = view["summary"]
        self.assertEqual(s["quote_available"], "0/2")
        self.assertIsNone(s["total_unrealized_pnl"])   # 绝不用 0 冒充
        self.assertIsNone(s["total_market_value"])

    def test_quote_exception_treated_as_unavailable(self):
        def boom(code):
            raise RuntimeError("market down")
        view = tj.portfolio_view(self.uid, boom)
        self.assertEqual(view["summary"]["quote_available"], "0/2")

    def test_realized_carried_after_full_exit(self):
        self._sell(100, 12.0, date="2026-10-22")   # 600519 清仓
        view = tj.portfolio_view(self.uid, lambda code: (15.0, "2026-10-24"))
        by_code = {p["code"]: p for p in view["positions"]}
        m = by_code["600519"]
        self.assertEqual(m["qty"], 0)
        self.assertIsNone(m["avg_cost"])
        self.assertAlmostEqual(m["realized_pnl"], (12.0 - 10.05) * 100, places=2)
        self.assertAlmostEqual(view["summary"]["total_realized_pnl"],
                               (12.0 - 10.05) * 100, places=2)


class TestCopywritingRedLine(unittest.TestCase):
    def test_no_advice_wording_in_module(self):
        """文案红线: 模块源码与响应模板不得含买卖建议导向字样"""
        import inspect
        src = inspect.getsource(tj)
        for word in ["建议买", "建议卖", "推荐买", "推荐卖", "买入建议", "卖出建议"]:
            self.assertNotIn(word, src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
