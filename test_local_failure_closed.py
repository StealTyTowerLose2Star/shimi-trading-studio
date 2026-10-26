# -*- coding: utf-8 -*-
"""local_real_data.py 失败闭合修复的纯离线契约测试 (2026-10-23)

验证范围 (对应 handoff/to-codex/20261023-拾米失败闭合离线审计.md 的 S1b/S4/S5/S7):
  - _fetch_market_snapshot: 首屏超时/空data/分页中途失败 → 抛 MarketDataUnavailable;
    合法分页结束与正常数据保留; 失败不进 snapshot/snapshot_long 成功缓存, 恢复后可取
  - real_fetch_sentiment: 无样本/缺指数 → 抛 MarketDataUnavailable, 不产出分数;
    真实零涨跌样本不按无数据处理; 有效样本输出兼容
  - real_get_latest_date: 失败/空K线 → None; 有效K线日期原样保留

约束: import 前拦截 socket.create_connection/connect/connect_ex/getaddrinfo 与
subprocess.*; 全部数据为虚构 payload; 不 import local_run.py/app.py, 不连库,
不启动服务, 不装依赖。触网/子进程计数必须为0。
任一断言失败 → 退出码 1 (绝不以退出0掩盖失败)。

运行: venv/Scripts/python test_local_failure_closed.py   (在 shimi-local 目录)
"""
import os
import socket
import subprocess
import sys
import time

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

# ─── 1. 导入被测模块 (仅 local_real_data, 无服务无DB) ───────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import local_real_data as real  # noqa: E402

real._pace = lambda *a, **kw: None  # 桩: 去节流, 纯逻辑测试

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


_ORIG = {k: getattr(real, k) for k in ("_http_json", "_snapshot", "_snapshot_long",
                                       "real_fetch_indices")}


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


def _stock(i, change=1.0):
    return {"f2": 10.0 + i * 0.01, "f3": change, "f5": 1000 + i, "f8": 2.5,
            "f12": f"{600000 + i}", "f14": f"虚构样例{i}"}


def _page(items):
    return {"rc": 0, "data": {"total": len(items), "diff": items}}


# ─── 2. _fetch_market_snapshot ──────────────────────────────────
@run("snapshot: 首屏超时 → MarketDataUnavailable")
def t_first_page_timeout():
    real._http_json = lambda url, timeout=10: (_ for _ in ()).throw(
        socket.timeout("timed out"))
    _expect_raises(real.MarketDataUnavailable, real._fetch_market_snapshot,
                   "首屏超时")


@run("snapshot: 空data(null) → MarketDataUnavailable")
def t_empty_data():
    real._http_json = lambda url, timeout=10: {"rc": 0, "data": None}
    _expect_raises(real.MarketDataUnavailable, real._fetch_market_snapshot,
                   "空data")


@run("snapshot: 分页中途失败 → 整体失败, 不返回残缺样本")
def t_mid_pagination_failure():
    def stub(url, timeout=10):
        if "pn=2" in url:
            raise ConnectionError("page2 dropped")
        if "m:0+" in url:
            return _page([_stock(i) for i in range(500)])  # 满页 → 触发第2页
        return _page([_stock(900)])
    real._http_json = stub
    _expect_raises(real.MarketDataUnavailable, real._fetch_market_snapshot,
                   "分页中途失败")


@run("snapshot: 全部市场空响应 → 无可用样本 → MarketDataUnavailable")
def t_all_empty():
    real._http_json = lambda url, timeout=10: _page([])
    _expect_raises(real.MarketDataUnavailable, real._fetch_market_snapshot,
                   "全空响应")


@run("snapshot: 合法分页结束(diff空/不足一页)与正常数据兼容")
def t_legal_pagination():
    def stub(url, timeout=10):
        if "m:0+" in url:
            return _page([_stock(i) for i in range(3)])   # <500 → 合法结束
        if "m:1+" in url:
            return _page([])                               # 空diff → 合法结束
        return _page([_stock(800)])
    real._http_json = stub
    rows = real._fetch_market_snapshot()
    assert len(rows) == 4, f"期望4行, 实际{len(rows)}"
    r0 = rows[0]
    for k in ("code", "ts_code", "name", "price", "change", "volume", "turnover"):
        assert k in r0, f"缺字段 {k}"
    assert r0["ts_code"].endswith(".SZ"), f"市场0后缀应为.SZ: {r0['ts_code']}"
    assert r0["price"] == 10.0 and r0["change"] == 1.0, f"数值异常: {r0}"


@run("snapshot: 失败不进成功缓存, 桩恢复后可重新取数")
def t_failure_not_cached_recovery():
    real._http_json = lambda url, timeout=10: (_ for _ in ()).throw(
        socket.timeout("timed out"))
    _expect_raises(real.MarketDataUnavailable, real._snapshot, "snapshot首次失败")
    _expect_raises(real.MarketDataUnavailable, real._snapshot_long,
                   "snapshot_long首次失败")
    assert "snapshot" not in real._cache, "失败结果进了 snapshot 成功缓存"
    assert "snapshot_long" not in real._cache, "失败结果进了 snapshot_long 成功缓存"
    real._http_json = lambda url, timeout=10: _page([_stock(1)])
    rows = real._snapshot()
    assert len(rows) == 1 and rows[0]["name"] == "虚构样例1", f"恢复后取数异常: {rows}"
    rows2 = real._snapshot_long()
    assert len(rows2) == 1, f"snapshot_long 恢复后取数异常: {rows2}"


# ─── 3. real_fetch_sentiment ────────────────────────────────────
@run("sentiment: 无有效快照样本 → 抛异常, 不产出分数")
def t_sentiment_no_sample():
    real._snapshot = lambda: []
    real.real_fetch_indices = lambda: [{"change": 0.5}]
    _expect_raises(real.MarketDataUnavailable, real.real_fetch_sentiment,
                   "无样本情绪")


@run("sentiment: 缺指数 → 抛异常, 不输出正常phase")
def t_sentiment_no_indices():
    real._snapshot = lambda: [{"change": 1.0}, {"change": -0.5}]
    real.real_fetch_indices = lambda: []
    _expect_raises(real.MarketDataUnavailable, real.real_fetch_sentiment,
                   "缺指数情绪")


@run("sentiment: 快照源失败向上传播, 不吞")
def t_sentiment_snapshot_raises():
    def boom():
        raise real.MarketDataUnavailable("快照失败")
    real._snapshot = boom
    _expect_raises(real.MarketDataUnavailable, real.real_fetch_sentiment,
                   "快照失败传播")


@run("sentiment: 真实零涨跌样本 ≠ 无数据 (不抛异常)")
def t_sentiment_real_zero_change():
    real._snapshot = lambda: [{"change": 0.0}, {"change": 0.0}, {"change": 0.0}]
    real.real_fetch_indices = lambda: [{"change": 0.0}, {"change": 0.0}]
    s = real.real_fetch_sentiment()
    assert s["sample_size"] == 3, f"样本数错误: {s}"
    assert s["up"] == 0 and s["down"] == 0, f"涨跌统计错误: {s}"
    assert s["phase"] and s["total_score"] is not None, "有效样本未产出结果"


@run("sentiment: 有效样本结果兼容 (phase/score/计数正确)")
def t_sentiment_valid():
    real._snapshot = lambda: [{"change": 1.0}, {"change": -0.5}, {"change": 0.0},
                              {"change": 9.6}, {"change": -9.6}]
    real.real_fetch_indices = lambda: [{"change": 0.5}, {"change": -0.1}]
    s = real.real_fetch_sentiment()
    assert s["sample_size"] == 5, f"样本数: {s['sample_size']}"
    assert (s["up"], s["down"], s["limit_up"], s["limit_down"]) == (2, 2, 1, 1), s
    # up_ratio=50, idx_avg=0.2 → score=50*0.7+(0.2+3)*5=51.0, phase=偏强震荡
    assert s["total_score"] == 51.0, f"score: {s['total_score']}"
    assert s["phase"] == "偏强震荡", f"phase: {s['phase']}"
    assert "error" not in s


# ─── 4. real_get_latest_date ────────────────────────────────────
@run("latest_date: 源失败 → None, 不冒充今日")
def t_latest_date_failure():
    real._http_json = lambda url, timeout=10: (_ for _ in ()).throw(
        ConnectionError("upstream down"))
    d = real.real_get_latest_date()
    assert d is None, f"失败时应为 None, 实际 {d!r}"
    assert d != time.strftime("%Y-%m-%d") or d is None


@run("latest_date: 空K线 → None")
def t_latest_date_empty_kline():
    for payload in ({"data": None}, {"data": {"klines": []}}, {"data": {}}):
        real._cache.clear()
        real._http_json = lambda url, timeout=10, p=payload: p
        d = real.real_get_latest_date()
        assert d is None, f"空K线 {payload!r} 应为 None, 实际 {d!r}"


@run("latest_date: 有效K线日期原样保留")
def t_latest_date_valid():
    real._http_json = lambda url, timeout=10: {
        "data": {"klines": ["2026-10-21,10.0,10.2,10.3,9.9,1000,0",
                            "2026-10-22,10.2,10.5,10.6,10.1,2000,0"]}}
    d = real.real_get_latest_date()
    assert d == "2026-10-22", f"有效日期应为 2026-10-22, 实际 {d!r}"


# ─── 5. 汇总: 任一失败或触网 → 退出码1 ─────────────────────────
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
