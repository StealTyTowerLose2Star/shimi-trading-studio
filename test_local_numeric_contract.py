# -*- coding: utf-8 -*-
"""local_real_data.py 缺失数值与真实零分离契约测试 (2026-10-23)

验证 _num/_price 及 indices/market_snapshot/sentiment 关键链:
  - None/空串/破折号/非法数字/NaN/Inf → default/None (显式未知), 不得变 0 假值
  - 合法 0 涨跌、0 成交量、-1% 涨跌必须保留 (-1 不再被全局当缺失)
  - 价格的 -1 (东财停牌标记) 仅在价格字段上下文判缺失
  - 指数涨跌幅缺失 → sentiment 抛 MarketDataUnavailable, 不以缺省0计算
  - 失败不进成功缓存, 桩恢复后可重试; 正常响应兼容

约束: import 前拦截 socket.create_connection/connect/connect_ex/getaddrinfo 与
subprocess.*; 全部虚构 payload; 不 import local_run/app, 不连库, 不读 env/凭据,
不启动服务; 触网/子进程计数必须为0; 任一断言失败 → 退出码 1。

运行: venv/Scripts/python test_local_numeric_contract.py   (在 shimi-local 目录)
"""
import os
import socket
import subprocess
import sys

# ─── 0. 触网/子进程护栏 (必须在任何目标 import 之前) ─────────────
VIOLATIONS = []


def _block(kind):
    def _blocked(*a, **kw):
        VIOLATIONS.append((kind, repr(a)[:120]))
        raise AssertionError(f"[GUARD] 离线测试禁止: {kind} {repr(a)[:120]}")
    return _blocked


socket.create_connection = _block("create_connection")
socket.socket.connect = _block("socket.connect")
socket.socket.connect_ex = _block("socket.connect_ex")
socket.getaddrinfo = _block("getaddrinfo")


class _BlockedPopen(subprocess.Popen):  # 必须是类: asyncio.windows_utils 会子类化它
    def __init__(self, *a, **kw):
        VIOLATIONS.append(("subprocess.Popen", repr(a)[:120]))
        raise AssertionError(f"[GUARD] 离线测试禁止子进程: {repr(a)[:120]}")


subprocess.Popen = _BlockedPopen
for _fn in ("run", "call", "check_call", "check_output"):
    setattr(subprocess, _fn, _block(f"subprocess.{_fn}"))

# ─── 1. 导入被测模块 ────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import local_real_data as real  # noqa: E402

real._pace = lambda *a, **kw: None  # 桩: 去节流

FAILURES = []


def run(name):
    def deco(fn):
        try:
            fn()
            print(f"[PASS] {name}")
        except Exception as e:
            FAILURES.append((name, e))
            print(f"[FAIL] {name}: {type(e).__name__}: {e}")
        finally:
            _teardown()
        return fn
    return deco


_ORIG = {k: getattr(real, k) for k in ("_http_json", "_snapshot", "real_fetch_indices")}


def _teardown():
    real._cache.clear()
    for k, v in _ORIG.items():
        setattr(real, k, v)


def _expect_raises(exc_type, fn, msg):
    try:
        fn()
    except exc_type:
        return
    except Exception as e:
        raise AssertionError(f"{msg}: 抛了 {type(e).__name__}({e}) 而非 {exc_type.__name__}")
    raise AssertionError(f"{msg}: 未抛 {exc_type.__name__} (静默通过=缺陷)")


def _page(items):
    return {"rc": 0, "data": {"total": len(items), "diff": items}}


# ─── 2. _num / _price 单元契约 ──────────────────────────────────
@run("_num: None/空串/破折号/非法数字/NaN/Inf → default")
def t_num_missing_invalid():
    for bad in (None, "", "  ", "-", "abc", "1,2", float("nan"), float("inf"),
                "-inf", "NaN", "Infinity", True, False):
        assert real._num(bad, 99.0) == 99.0, f"_num({bad!r}) 未回落 default"
        assert real._num(bad, None) is None, f"_num({bad!r}, None) 未得 None"


@run("_num: 合法0与-1保留 (-1%是合法涨跌幅, 不得全局抹掉)")
def t_num_legal_zero_neg():
    assert real._num(0) == 0.0 and real._num("0") == 0.0
    assert real._num(-1) == -1.0, "_num(-1) 被当缺失, 吞掉合法-1%"
    assert real._num(-1.0) == -1.0 and real._num("-1") == -1.0
    assert real._num("1.5") == 1.5 and real._num(-0.26) == -0.26


@run("_price: 缺失/非法/NaN/Inf/停牌-1 → None; 合法价保留")
def t_price_context():
    for bad in (None, "", "-", "abc", float("nan"), float("inf"), -1, -1.0, 0, -2, True, False):
        assert real._price(bad) is None, f"_price({bad!r}) 应为 None (显式未知)"
    assert real._price(10.5) == 10.5 and real._price("1309.69") == 1309.69


# ─── 3. indices 链 (ulist) ──────────────────────────────────────
@run("indices: -1%涨跌保留; 缺涨跌幅→None非0; 缺价/停牌/NaN价剔除")
def t_indices_numeric():
    payload = {"data": {"diff": [
        {"f2": 3713.03, "f3": -1, "f12": "000001", "f14": "虚构指数甲"},   # 合法-1%
        {"f2": 10456.7, "f3": None, "f12": "399001", "f14": "虚构指数乙"},  # 缺涨跌幅
        {"f2": "-", "f3": 0.5, "f12": "399006", "f14": "虚构停牌"},        # 缺价→剔除
        {"f2": -1, "f3": 0.5, "f12": "000688", "f14": "虚构停牌2"},        # 停牌-1→剔除
        {"f2": "nan", "f3": 0.5, "f12": "000002", "f14": "虚构坏价"},      # NaN价→剔除
    ]}}
    real._http_json = lambda url, timeout=10: payload
    quotes = real._ulist_quotes(["1.000001"])
    assert quotes["000001"]["change"] == -1.0, f"-1%被吞: {quotes.get('000001')}"
    assert quotes["399001"]["change"] is None, f"缺涨跌幅应None非0: {quotes.get('399001')}"
    for gone in ("399006", "000688", "000002"):
        assert gone not in quotes, f"{gone} 应被剔除: {quotes.get(gone)}"
    idx = real.real_fetch_indices()
    by_code = {r["code"]: r for r in idx}
    assert by_code["000001.SH"]["change"] == -1.0
    assert by_code["399001.SZ"]["change"] is None
    assert "399006.SZ" not in by_code and "000688.SH" not in by_code


# ─── 4. market_snapshot 链 ──────────────────────────────────────
@run("snapshot: 0/-1涨跌保留; 缺量/换→None; 坏价不入样")
def t_snapshot_numeric():
    def stub(url, timeout=10):
        if "m:0+" in url:
            return _page([
                {"f2": 10.0, "f3": 0, "f5": 0, "f8": 0, "f12": "600000", "f14": "虚构零"},
                {"f2": 20.0, "f3": -1, "f5": 5000, "f8": 1.5, "f12": "600001", "f14": "虚构负一"},
                {"f2": 40.0, "f3": 1.0, "f5": "-", "f8": None, "f12": "600003", "f14": "虚构缺量"},
                {"f2": "inf", "f3": 1.0, "f5": 100, "f8": 1.0, "f12": "600004", "f14": "虚构Inf价"},
            ])
        return _page([])
    real._http_json = stub
    rows = real._fetch_market_snapshot()
    by = {r["code"]: r for r in rows}
    assert set(by) == {"600000", "600001", "600003"}, f"样本集合错误: {sorted(by)}"
    z = by["600000"]
    assert z["change"] == 0.0 and z["volume"] == 0.0 and z["turnover"] == 0.0, \
        f"真实0被抹掉: {z}"
    assert by["600001"]["change"] == -1.0, f"-1%涨跌被吞: {by['600001']}"
    m = by["600003"]
    assert m["volume"] is None and m["turnover"] is None, f"缺量/换应None非0: {m}"


@run("snapshot: 必要涨跌字段缺失/非法使整次失败，不缩小分母、不写缓存")
def t_snapshot_missing_change_fails():
    for value in (None, "", "-", "abc", True, float("nan"), float("inf")):
        real._cache.clear()
        real._http_json = lambda url, timeout=10, value=value: _page([
            {"f2": 10, "f3": 2, "f12": "600000"},
            {"f2": 20, "f3": value, "f12": "600001"}])
        _expect_raises(real.MarketDataUnavailable, real._snapshot, "必要涨跌缺失")
        assert "snapshot" not in real._cache


@run("snapshot: 失败不进成功缓存, 桩恢复后重试可取")
def t_snapshot_retry_after_failure():
    real._http_json = lambda url, timeout=10: (_ for _ in ()).throw(
        socket.timeout("timed out"))
    _expect_raises(real.MarketDataUnavailable, real._snapshot, "首次失败")
    assert "snapshot" not in real._cache, "失败值进了成功缓存"
    real._http_json = lambda url, timeout=10: (
        _page([{"f2": 10.0, "f3": -1, "f5": 100, "f8": 1.0,
                "f12": "600000", "f14": "虚构恢复"}]) if "m:0+" in url else _page([]))
    rows = real._snapshot()
    assert len(rows) == 1 and rows[0]["change"] == -1.0, f"恢复重试异常: {rows}"


# ─── 5. sentiment 链 ────────────────────────────────────────────
@run("sentiment: 指数涨跌幅缺失(None) → 抛 MarketDataUnavailable, 不以缺省0计算")
def t_sentiment_index_change_missing():
    real._snapshot = lambda: [{"change": 1.0}, {"change": -0.5}]
    real.real_fetch_indices = lambda: [{"change": None}, {"change": 0.3}]
    _expect_raises(real.MarketDataUnavailable, real.real_fetch_sentiment,
                   "指数缺涨跌幅")


@run("sentiment: -1%指数与-1%个股按真实值计算 (非50分/非缺省)")
def t_sentiment_with_legal_negatives():
    real._snapshot = lambda: [{"change": -1.0}, {"change": 0.0}, {"change": 2.0}]
    real.real_fetch_indices = lambda: [{"change": -1.0}, {"change": 0.5}]
    s = real.real_fetch_sentiment()
    # up=1 down=1 total=2 → up_ratio=50; idx_avg=-0.25
    expected = round(max(0, min(100, 50 * 0.7 + (-0.25 + 3) * 5)), 1)
    assert s["total_score"] == expected, f"score: {s['total_score']} != {expected}"
    assert s["phase"] == "偏弱调整", f"phase: {s['phase']}"
    assert s["up"] == 1 and s["down"] == 1 and s["sample_size"] == 3, s


# ─── 6. 汇总: 任一失败或触网 → 退出码1 ─────────────────────────
if __name__ == "__main__":
    print("=" * 66)
    if VIOLATIONS:
        print(f"触网/子进程违规 {len(VIOLATIONS)} 次: {VIOLATIONS}")
        FAILURES.append(("guard", VIOLATIONS))
    else:
        print("触网/子进程计数: 0")
    if FAILURES:
        print(f"失败 {len(FAILURES)} 项 → 退出码 1")
        sys.exit(1)
    print("全部通过 → 退出码 0")
    sys.exit(0)
