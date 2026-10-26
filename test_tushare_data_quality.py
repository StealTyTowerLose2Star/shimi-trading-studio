"""Active EOD adapter tests, synthetic API responses; no credentials or network."""
import platform
import socket
import subprocess
import time
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

platform.machine = lambda: "AMD64"
platform.win32_ver = lambda: ("10", "10.0", "", "")
BLOCKED = []
def deny(*args, **kwargs):
    BLOCKED.append("external")
    raise AssertionError("no network/processes permitted")
class NoProcess(subprocess.Popen):
    def __init__(self, *args, **kwargs):
        deny(*args, **kwargs)
socket.create_connection = deny
socket.socket.connect = deny
socket.socket.connect_ex = deny
socket.getaddrinfo = deny
subprocess.Popen = NoProcess
for name in ("run", "call", "check_call", "check_output"):
    setattr(subprocess, name, deny)

import pandas as pd
import local_tushare_data as real

FIELDS = ["ts_code", "trade_date", "close", "pct_chg", "amount"]
VALID = ["600000.SH", "20261022", 10, 0, 0]


class EODQualityTests(unittest.TestCase):
    def setUp(self):
        real._cache.clear()

    def tearDown(self):
        self.assertEqual(BLOCKED, [])

    def test_plain_http_cannot_read_or_send_token(self):
        with patch.object(real, "API_URL", "http://api.tushare.pro"), \
             patch.object(real.os.environ, "get", side_effect=AssertionError("must not read credentials")):
            with self.assertRaisesRegex(real.TushareError, "transport_not_verified"):
                real._api("synthetic-only")

    def test_commercial_or_unknown_purpose_cannot_read_token(self):
        for purpose, reason in [('commercial','commercial_permission_not_approved'),
                                ('unconfirmed','purpose_unconfirmed'),
                                ('','purpose_unconfirmed')]:
            def value(key, default=None):
                if key == 'SHIMI_MARKET_USE':
                    return purpose
                raise AssertionError('must not read credentials')
            # Synthetic HTTPS URL merely reaches the policy gate in this test;
            # not a claim that this endpoint/protocol has been provider-verified.
            with self.subTest(purpose=purpose), \
                 patch.object(real, 'API_URL', 'https://synthetic.invalid'), \
                 patch.object(real.os.environ, 'get', side_effect=value):
                with self.assertRaisesRegex(real.TushareError, reason):
                    real._api('offline-only')

    def test_rows_reject_truncation_and_duplicate_schema(self):
        for fields, rows in ((["a", "b"], [[1]]), (["a"], [[1, 2]]),
                             (["a", "a"], [[1, 2]]), (None, []), (["a"], [None])):
            with self.subTest(fields=fields, rows=rows):
                with self.assertRaises(real.MarketDataUnavailable):
                    real._rows(fields, rows)

    def test_source_numbers_do_not_make_missing_zero(self):
        for value in (None, "", "-", float("nan"), float("inf"), True, False):
            with self.subTest(value=value):
                self.assertIsNone(real._number(value, "test", optional=True))
                with self.assertRaises(real.MarketDataUnavailable):
                    real._number(value, "test")
        self.assertEqual(real._number(0, "zero"), 0)
        self.assertEqual(real._number(-1, "change"), -1)

    def test_bad_dates_are_not_data_dates(self):
        for value in ("", None, "20260230", "2026-10-22", "2026102x"):
            with self.subTest(value=value):
                with self.assertRaises(real.MarketDataUnavailable):
                    real._fmt_date(value)
        self.assertEqual(real._fmt_date("20261022"), "2026-10-22")

    def test_daily_required_value_and_date_fail_without_cache(self):
        for index, value in ((2, None), (2, 0), (2, True), (3, None), (3, float("nan")),
                             (1, "20261021")):
            row = VALID.copy()
            row[index] = value
            with self.subTest(index=index, value=value):
                with patch.object(real, "_api", return_value=(FIELDS, [row])):
                    with self.assertRaises(real.MarketDataUnavailable):
                        real._daily_snapshot("20261022")
                self.assertNotIn("daily:20261022", real._cache)

    def test_daily_duplicate_denominator_fails_and_recovery_keeps_zero(self):
        with patch.object(real, "_api", return_value=(FIELDS, [VALID, VALID])):
            with self.assertRaises(real.MarketDataUnavailable):
                real._daily_snapshot("20261022")
        with patch.object(real, "_api", return_value=(FIELDS, [VALID])):
            value = real._daily_snapshot("20261022")
        self.assertEqual(value.iloc[0]["pct_chg"], 0)
        self.assertEqual(value.iloc[0]["amount"], 0)

    def test_hot_optional_values_unknown_vs_real_zero(self):
        daily = pd.DataFrame([dict(zip(FIELDS, VALID)),
                              dict(ts_code="600001.SH", trade_date="20261022", close=11,
                                   pct_chg=-1, amount=None)])
        with patch.object(real, "_data_date", return_value="20261022"), \
             patch.object(real, "_daily_snapshot", return_value=daily), \
             patch.object(real, "_daily_basic_snapshot", return_value=None), \
             patch.object(real, "real_get_stock_basic", return_value={}):
            result = real.real_fetch_hot_stocks()
        self.assertIsNone(result[0]["turnover"])
        self.assertEqual(result[0]["volume"], 0)
        self.assertIsNone(result[1]["volume"])
        self.assertEqual(result[1]["change"], -1)
        self.assertEqual(result[0]["trade_date"], "2026-10-22")

    def test_latest_date_is_observed_data_date_not_open_calendar(self):
        with patch.object(real, "_data_date", return_value="20261022"), \
             patch.object(real, "_open_days", return_value=["20261023"]):
            self.assertEqual(real.real_get_latest_date(), "2026-10-22")
        with patch.object(real, "_data_date", side_effect=real.MarketDataUnavailable("missing")):
            self.assertIsNone(real.real_get_latest_date())

    def test_short_history_does_not_score_zero(self):
        with patch.object(real, "_data_date", return_value="20261022"), \
             patch.object(real, "_index_hist", return_value=pd.DataFrame({"close": [10]})):
            with self.assertRaises(real.MarketDataUnavailable):
                real.real_fetch_sentiment()

    def test_flat_stocks_not_counted_as_decliners(self):
        frame = pd.DataFrame([dict(zip(FIELDS, VALID))])
        with patch.object(real, "_data_date", return_value="20261022"), \
             patch.object(real, "_daily_snapshot", return_value=frame), \
             patch.object(real, "real_get_stock_basic", return_value={"600000.SH": {"industry": "fictional"}}):
            result = real.real_fetch_sectors()[0]
        self.assertEqual((result["up_count"], result["down_count"], result["flat_count"]), (0, 0, 1))

    def test_limit_count_stops_at_first_nonqualifying_day_without_dropping_stock(self):
        today = pd.DataFrame([dict(ts_code="600000.SH", close=10, pct_chg=10)])
        previous = pd.DataFrame([dict(ts_code="600000.SH", close=9, pct_chg=0)])
        with patch.object(real, "_data_date", return_value="20261022"), \
             patch.object(real, "_open_days", return_value=["20261022", "20261021"]), \
             patch.object(real, "_daily_snapshot", side_effect=lambda day: today if day == "20261022" else previous), \
             patch.object(real, "real_get_stock_basic", return_value={}):
            result = real.real_fetch_limit_up()[0]
        self.assertEqual(result["board_count"], 1)
        self.assertFalse(result["board_count_lower_bound"])

    def test_limit_missing_history_is_unknown(self):
        today = pd.DataFrame([dict(ts_code="600000.SH", close=10, pct_chg=10)])
        with patch.object(real, "_data_date", return_value="20261022"), \
             patch.object(real, "_open_days", return_value=["20261022", "20261021"]), \
             patch.object(real, "_daily_snapshot", side_effect=lambda day: today if day == "20261022" else None), \
             patch.object(real, "real_get_stock_basic", return_value={}):
            self.assertIsNone(real.real_fetch_limit_up()[0]["board_count"])

    def test_kline_missing_values_fail_and_real_zero_volume_is_kept(self):
        fields = ["trade_date", "open", "high", "low", "close", "vol"]
        good = ["20261022", 10, 12, 9, 11, 0]
        for index, bad in ((1, None), (2, float("nan")), (3, 0), (4, True), (5, None), (5, -1)):
            row = good.copy()
            row[index] = bad
            with self.subTest(index=index):
                with patch.object(real, "_api", return_value=(fields, [row])):
                    with self.assertRaises(real.MarketDataUnavailable):
                        real.real_get_kline("600000", days=2)
                self.assertNotIn("kline:600000.SH:2", real._cache)
        with patch.object(real, "_api", return_value=(fields, [good])):
            result = real.real_get_kline("600000", days=2)
        self.assertEqual(result.iloc[0]["volume"], 0)
        self.assertEqual(result.iloc[0]["close"], 11)

    def test_complete_synthetic_history_and_snapshot_produce_finite_output(self):
        history = pd.DataFrame({"trade_date": pd.date_range(end="2026-10-22", periods=80).strftime("%Y%m%d"),
                                "close": [10 + i / 100 for i in range(80)], "amount": [100.0] * 80})
        for window in (5, 10, 20, 60):
            history[f"ma{window}"] = history["close"].rolling(window).mean()
        daily = pd.DataFrame({"pct_chg": [-1.0, 0.0, 1.0]})
        with patch.object(real, "_data_date", return_value="20261022"), \
             patch.object(real, "_index_hist", return_value=history), \
             patch.object(real, "_daily_snapshot", return_value=daily):
            result = real.real_fetch_sentiment()
        self.assertEqual(result["sample_size"], 3)
        self.assertEqual(result["date"], "2026-10-22")
        self.assertTrue(0 <= result["total_score"] <= 100)

    # ─── 板块时点契约 (20261023-1604): retrieved_at_utc / trade_date / source ───

    def test_retrieved_at_utc_recorded_only_after_valid_api_success(self):
        with patch.object(real, "_api", return_value=(FIELDS, [VALID])):
            df = real._daily_snapshot("20261022")
        stamp = df.attrs.get("retrieved_at_utc")
        self.assertIsNotNone(stamp)
        parsed = datetime.fromisoformat(stamp)
        self.assertEqual(parsed.utcoffset(), timezone.utc.utcoffset(None))
        # 失败: 不产生成功时点, 不落成功缓存
        with patch.object(real, "_api", side_effect=real.TushareError("down")):
            with self.assertRaises(real.TushareError):
                real._daily_snapshot("20261023")
        self.assertNotIn("daily:20261023", real._cache)
        # 空结果(盘中未发布): 返回None且不含任何取数时点
        with patch.object(real, "_api", return_value=(FIELDS, [])):
            self.assertIsNone(real._daily_snapshot("20261024"))
        self.assertIsNone(real._cache["daily:20261024"][2])

    def test_cache_hit_keeps_original_retrieved_time_despite_clock_advance(self):
        calls = []
        def fake_api(*a, **kw):
            calls.append(a)
            return (FIELDS, [VALID])
        with patch.object(real, "_api", side_effect=fake_api):
            first = real._daily_snapshot("20261022")
        stamp = first.attrs["retrieved_at_utc"]
        later = time.time() + 3600  # 时钟推进1h, 仍在6h TTL内
        with patch.object(real.time, "time", return_value=later), \
             patch.object(real, "_api", side_effect=AssertionError("缓存命中不得重新取数")):
            second = real._daily_snapshot("20261022")
        self.assertEqual(second.attrs["retrieved_at_utc"], stamp)
        self.assertEqual(len(calls), 1)

    def _sector_frame(self, with_stamp=True, trade_date="20261022"):
        row = dict(zip(FIELDS, VALID))
        row["trade_date"] = trade_date
        df = pd.DataFrame([row])
        if with_stamp:
            df.attrs["retrieved_at_utc"] = "2026-10-22T09:30:00+00:00"
        return df

    def test_sectors_carry_source_data_kind_and_real_data_date(self):
        with patch.object(real, "_data_date", return_value="20261020"), \
             patch.object(real, "_daily_snapshot", return_value=self._sector_frame(trade_date="20261020")), \
             patch.object(real, "real_get_stock_basic",
                          return_value={"600000.SH": {"industry": "虚构行业"}}):
            row = real.real_fetch_sectors()[0]
        # 既有数组/数值字段契约保持
        for key in ("name", "change", "up_count", "down_count", "flat_count"):
            self.assertIn(key, row)
        # 时点/来源契约: trade_date=实际数据日(非今日20261023)
        self.assertEqual(row["trade_date"], "2026-10-20")
        self.assertNotEqual(row["trade_date"], time.strftime("%Y-%m-%d"))
        self.assertEqual(row["source"], "tushare-daily-eod")
        self.assertIn("日线", row["data_kind"])
        self.assertEqual(row["retrieved_at_utc"], "2026-10-22T09:30:00+00:00")
        self.assertEqual(row["source_verification"], "not_verified")

    def test_sector_flow_metadata_and_none_when_no_retrieval_evidence(self):
        with patch.object(real, "_data_date", return_value="20261022"), \
             patch.object(real, "_daily_snapshot", return_value=self._sector_frame(with_stamp=False)), \
             patch.object(real, "real_get_stock_basic",
                          return_value={"600000.SH": {"industry": "虚构行业"}}):
            row = real.real_fetch_sector_flow()[0]
        for key in ("name", "change", "up_ratio", "stock_count", "strength", "hot"):
            self.assertIn(key, row)
        self.assertEqual(row["trade_date"], "2026-10-22")
        self.assertEqual(row["source"], "tushare-daily-eod")
        self.assertIn("非实时资金流", row["data_kind"])
        self.assertIsNone(row["retrieved_at_utc"])  # 无可核取数时点 → None, 不猜
        self.assertEqual(row["source_verification"], "not_verified")


if __name__ == "__main__":
    unittest.main(verbosity=2)
