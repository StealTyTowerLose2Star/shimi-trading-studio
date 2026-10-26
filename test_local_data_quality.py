"""Synthetic source-boundary tests; all network/process I/O blocked before import."""
import platform
import socket
import subprocess
import unittest
from unittest.mock import patch

platform.machine = lambda: "AMD64"
platform.win32_ver = lambda: ("10", "10.0", "", "")
FORBIDDEN = []
def deny(*args, **kwargs):
    FORBIDDEN.append("external-call")
    raise AssertionError("offline quality tests forbid network/process calls")
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

import local_real_data as real


def board(**changes):
    value = {"f14": "fictional", "f3": -1, "f104": 2, "f105": 3}
    value.update(changes)
    return {"data": {"diff": [value]}}


def candle(**changes):
    value = {"date": "2026-10-20", "open": "10", "close": "11",
             "high": "12", "low": "9", "volume": "0"}
    value.update(changes)
    return ",".join(value[name] for name in ("date", "open", "close", "high", "low", "volume"))


class QualityTests(unittest.TestCase):
    def setUp(self):
        real._cache.clear()

    def tearDown(self):
        self.assertEqual(FORBIDDEN, [], "forbidden I/O was attempted")

    def test_numeric_defaults_are_unknown(self):
        for value in (None, "", "-", "bad", "NaN", "Infinity", True, False):
            with self.subTest(value=value):
                self.assertIsNone(real._num(value))
        self.assertEqual(real._num(-1), -1)
        self.assertEqual(real._num(0), 0)

    def test_invalid_envelopes_fail_without_cache(self):
        calls = (("boards", real.real_fetch_sectors),
                 ("snapshot", real._snapshot),
                 ("kline:600000:2", lambda: real.real_get_kline("600000", days=2)))
        for payload in (None, [], {}, {"data": None}, {"data": {}},
                        {"data": {"diff": {}, "klines": {}}},
                        {"rc": 403, "data": {"diff": [], "klines": []}}):
            for cache_key, call in calls:
                with self.subTest(payload=payload, cache_key=cache_key):
                    with patch.object(real, "_http_json", return_value=payload):
                        with self.assertRaises(real.MarketDataUnavailable):
                            call()
                    self.assertNotIn(cache_key, real._cache)

    def test_board_required_fields_never_become_zero(self):
        for field in ("f3", "f104", "f105"):
            values = (None, "", "-", "NaN", "Infinity", True, False)
            if field != "f3":
                values += (-1, 1.5)
            for value in values:
                with self.subTest(field=field, value=value):
                    with patch.object(real, "_http_json", return_value=board(**{field: value})):
                        with self.assertRaises(real.MarketDataUnavailable):
                            real.real_fetch_sectors()
                    self.assertNotIn("boards", real._cache)

    def test_board_zero_denominator_is_unknown(self):
        with patch.object(real, "_http_json", return_value=board(f3=0, f104=0, f105=0)):
            result = real.real_fetch_sector_flow()[0]
        self.assertEqual(result["change"], 0)
        self.assertEqual(result["stock_count"], 0)
        for field in ("up_ratio", "strength", "hot"):
            self.assertIsNone(result[field])
        self.assertEqual(result["status"], "unknown")

    def test_board_failure_then_recovery(self):
        with patch.object(real, "_http_json", return_value=board(f3=None)):
            with self.assertRaises(real.MarketDataUnavailable):
                real.real_fetch_sectors()
        with patch.object(real, "_http_json", return_value=board(f3=-1, f104=0)):
            result = real.real_fetch_sectors()[0]
        self.assertEqual(result["change"], -1)
        self.assertEqual(result["up_count"], 0)

    def test_kline_bad_prices_and_volume_fail(self):
        for field in ("open", "close", "high", "low", "volume"):
            values = ("", "-", "NaN", "Infinity", "bad", "-1")
            values += ("1.5",) if field == "volume" else ("0", "-2")
            for value in values:
                with self.subTest(field=field, value=value):
                    payload = {"data": {"klines": [candle(**{field: value})]}}
                    with patch.object(real, "_http_json", return_value=payload):
                        with self.assertRaises(real.MarketDataUnavailable):
                            real.real_get_kline("600000", days=2)
                    self.assertNotIn("kline:600000:2", real._cache)

    def test_kline_dates_ohlc_and_partial_rows_fail(self):
        cases = ([candle(date="2026-02-30")], [candle(date="2026-2-01")],
                 [candle(), candle()], [candle(date="2026-10-21"), candle()],
                 [candle(high="10")], [candle(low="11")], ["2026-10-20,10"],
                 [None], [candle(), "invalid"], [])
        for lines in cases:
            with self.subTest(lines=lines):
                with patch.object(real, "_http_json", return_value={"data": {"klines": lines}}):
                    with self.assertRaises(real.MarketDataUnavailable):
                        real.real_get_kline("600000", days=2)
                self.assertNotIn("kline:600000:2", real._cache)

    def test_kline_recovers_with_real_zero_volume(self):
        with patch.object(real, "_http_json", return_value={"data": {"klines": [candle(close="-")]}}):
            with self.assertRaises(real.MarketDataUnavailable):
                real.real_get_kline("600000", days=2)
        with patch.object(real, "_http_json", return_value={"data": {"klines": [candle()]}}):
            result = real.real_get_kline("600000", days=2)
        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]["volume"], 0)
        self.assertEqual(result.iloc[0]["date"], "2026-10-20")

    def test_indices_empty_fail_but_unknown_us_symbol_stays_explicit(self):
        with patch.object(real, "_http_json", return_value={"data": {"diff": []}}):
            with self.assertRaises(real.MarketDataUnavailable):
                real.real_fetch_indices()
            self.assertEqual(real.real_us_quote("ZZZZZ")["error"], "not_found")


if __name__ == "__main__":
    unittest.main(verbosity=2)
