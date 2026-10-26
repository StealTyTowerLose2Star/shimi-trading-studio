"""[离线契约测试] local_real_data 的东财响应解析 (不触网, 罐头JSON驱动)"""
import unittest
from unittest.mock import patch

import local_real_data as R

ULIST = {"data": {"diff": [
    {"f2": 3713.03, "f3": -0.6, "f12": "000001", "f14": "上证指数"},
    {"f2": 1309.69, "f3": -1.67, "f12": "600519", "f14": "贵州茅台"},
    {"f2": 343.99, "f3": 1.32, "f12": "AAPL", "f14": "苹果"},
]}}
BOARDS = {"data": {"diff": [
    {"f3": 3.26, "f12": "BK0604", "f14": "电视广播", "f104": 13, "f105": 0},
    {"f3": -1.5, "f12": "BK0475", "f14": "银行", "f104": 5, "f105": 30},
]}}
SNAPSHOT = {"data": {"diff": [
    {"f2": 11.41, "f3": -0.26, "f5": 814000, "f8": 0.42, "f12": "000001", "f14": "平安银行"},
    {"f2": 1309.69, "f3": 9.99, "f5": 50000, "f8": 1.2, "f12": "600519", "f14": "贵州茅台"},
]}}
KLINE = {"data": {"klines": [
    "2026-10-20,1300.00,1310.00,1315.00,1295.00,12345,16200000",
    "2026-10-21,1312.00,1309.69,1318.00,1305.00,11000,15000000",
]}}


def fake_http_json(url, timeout=10):
    if "ulist" in url:
        return ULIST
    if "m:90+t:2" in url:
        return BOARDS
    if "clist" in url:
        return SNAPSHOT
    if "kline" in url:
        return KLINE
    raise AssertionError("unexpected url: " + url)


class RealDataContractTests(unittest.TestCase):
    def setUp(self):
        R._cache.clear()

    def test_secid_convert(self):
        self.assertEqual(R._secid_cn("600519"), "1.600519")
        self.assertEqual(R._secid_cn("600519.SH"), "1.600519")
        self.assertEqual(R._secid_cn("000001.SZ"), "0.000001")
        self.assertEqual(R._secid_cn("300750"), "0.300750")
        self.assertEqual(R._secid_cn("1.000001"), "1.000001")

    def test_indices_contract(self):
        with patch.object(R, "_http_json", fake_http_json):
            rows = R.real_fetch_indices()
        self.assertEqual(rows[0]["name"], "上证指数")
        self.assertEqual(rows[0]["price"], 3713.03)
        self.assertEqual(rows[0]["change"], -0.6)
        self.assertIn("code", rows[0])

    def test_sectors_and_flow_contract(self):
        with patch.object(R, "_http_json", fake_http_json):
            sec = R.real_fetch_sectors()
            flow = R.real_fetch_sector_flow()
        self.assertEqual(sec[0], {"name": "电视广播", "change": 3.26, "up_count": 13, "down_count": 0})
        self.assertEqual(flow[0]["name"], "电视广播")
        self.assertEqual(flow[0]["up_ratio"], 100.0)
        self.assertIn("strength", flow[0])

    def test_hot_limit_sentiment_contract(self):
        with patch.object(R, "_http_json", fake_http_json):
            hot = R.real_fetch_hot_stocks()
            R._cache.clear()
            limit = R.real_fetch_limit_up()
            R._cache.clear()
            senti = R.real_fetch_sentiment()
        self.assertEqual(hot[0]["code"], "000001")
        self.assertEqual(hot[0]["price"], 11.41)
        self.assertEqual(hot[0]["turnover"], 0.42)
        self.assertEqual(limit[0]["code"], "600519")   # 9.99 >= 9.5 涨停线
        self.assertEqual(limit[0]["board_count"], 1)
        self.assertEqual(senti["limit_up"], 1)
        self.assertEqual(senti["up"], 1)
        self.assertIn("note", senti)

    def test_kline_contract(self):
        with patch.object(R, "_http_json", fake_http_json):
            df = R.real_get_kline("600519", days=2)
        self.assertEqual(list(df.columns), ["date", "open", "high", "low", "close", "volume"])
        self.assertEqual(df.iloc[-1]["close"], 1309.69)
        self.assertEqual(df.iloc[-1]["date"], "2026-10-21")
        with patch.object(R, "_http_json", fake_http_json):
            self.assertEqual(R.real_get_latest_date(), "2026-10-21")

    def test_us_quote_contract(self):
        with patch.object(R, "_http_json", fake_http_json):
            q = R.real_us_quote("aapl")
        self.assertEqual(q["symbol"], "AAPL")
        self.assertEqual(q["price"], 343.99)
        self.assertEqual(q["currency"], "USD")
        self.assertIn("error", R.real_us_quote("../../etc"))
        with patch.object(R, "_http_json", fake_http_json):
            self.assertIn("error", R.real_us_quote("ZZZZZ"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
