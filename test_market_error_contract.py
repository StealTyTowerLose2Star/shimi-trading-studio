# -*- coding: utf-8 -*-
"""api/market.py 错误契约的纯离线测试 (2026-10-23)

验证"错误不当成功":
  - /indices /sectors /sector-flow /hot-stocks /limit-up /sentiment 六端点:
    上游异常 (cache_or_fetch 转成 error 字典) → 502 + 可读 error, 不再 HTTP200 包错误;
    cache_or_fetch 自身直接抛异常 → 502;
    有效数据(非空/合法空列表/合法字典) → 200 原 JSON 契约
  - /dashboard: 失败项 → None 且记入 _errors (key+简短错误), 健康项保留,
    聚合含成功项仍 200 并带 X-Shimi-Degraded 头; 全部健康时 _errors 为 None 且无降级头;
    策略键 (strategy_*) 2026-10-25 起只读 cache.py 既有缓存不为聚合阻塞,
    缓存未生成 → None 且不记错误, 写穿值 → 原样下发

约束: import 前拦截 socket.create_connection/connect/connect_ex/getaddrinfo 与
subprocess.*; 预置 data.fetcher / services.strategy 桩模块 (不 import local_run/app),
Flask.test_client 注册 market 蓝图; 不连库不启动服务; 触网/子进程计数必须为0;
任一断言失败 → 退出码 1。

运行: venv/Scripts/python test_market_error_contract.py   (在 shimi-local 目录)
"""
import os
import socket
import subprocess
import sys
import types

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

# ─── 1. 桩模块: data.fetcher / services.strategy ────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("SHIMI_USE_REDIS", "false")  # 内存缓存, 不碰 redis

BEHAVIOR = {}  # name -> 返回值 | 可调用 | Exception实例(抛出)


def _make(name):
    def f(*a, **kw):
        b = BEHAVIOR.get(name)
        if isinstance(b, Exception):
            raise b
        return b() if callable(b) else b
    f.__name__ = name
    return f


_fetcher = types.ModuleType("data.fetcher")
for _n in ("fetch_indices", "fetch_sectors", "fetch_sector_flow",
           "fetch_hot_stocks", "fetch_limit_up", "fetch_sentiment",
           "get_stock_basic", "get_latest_date"):
    setattr(_fetcher, _n, _make(_n))
_data_pkg = types.ModuleType("data")
_data_pkg.fetcher = _fetcher
sys.modules["data"] = _data_pkg
sys.modules["data.fetcher"] = _fetcher

_strat = types.ModuleType("services.strategy")
for _n in ("run_trend_scan", "run_hybrid_scan", "run_dragon_scan"):
    setattr(_strat, _n, _make(_n))
_svc_pkg = types.ModuleType("services")
_svc_pkg.strategy = _strat
sys.modules["services"] = _svc_pkg
sys.modules["services.strategy"] = _strat

# ─── 2. 导入被测蓝图并建测试客户端 ───────────────────────────────
import cache as shimi_cache  # noqa: E402  (真实cache模块, 内存后端)
from flask import Flask      # noqa: E402
import api.market as mkt     # noqa: E402

_app = Flask("error-contract-test")
_app.register_blueprint(mkt.bp)
_client = _app.test_client()

FAILURES = []

SIX = [("/api/indices", "fetch_indices"),
       ("/api/sectors", "fetch_sectors"),
       ("/api/sector-flow", "fetch_sector_flow"),
       ("/api/hot-stocks", "fetch_hot_stocks"),
       ("/api/limit-up", "fetch_limit_up"),
       ("/api/sentiment", "fetch_sentiment")]


def run(name):
    def deco(fn):
        try:
            fn()
            print(f"[PASS] {name}")
        except Exception as e:
            FAILURES.append((name, e))
            print(f"[FAIL] {name}: {type(e).__name__}: {e}")
        finally:
            BEHAVIOR.clear()
            shimi_cache.MEM_CACHE.clear()
            shimi_cache.MEM_TTL.clear()
        return fn
    return deco


def _healthy(name):
    if name == "fetch_sentiment":
        return {"phase": "虚构震荡", "total_score": 51.0, "sample_size": 5}
    if name == "get_latest_date":
        return "2026-10-22"
    if name == "get_stock_basic":
        return {}
    if name.startswith("run_"):
        return {"picked": [], "total_scanned": 0}
    return [{"code": "600519", "name": "虚构样例", "price": 10.0, "change": 1.0}]


def _set_all_healthy():
    for _, fname in SIX:
        BEHAVIOR[fname] = _healthy(fname)
    for n in ("run_trend_scan", "run_hybrid_scan", "run_dragon_scan",
              "get_stock_basic", "get_latest_date"):
        BEHAVIOR[n] = _healthy(n)


# ─── 3. 六端点: 上游异常 → 502, 不得 HTTP200 包 error ───────────
@run("六端点: 上游抛异常 → 全部 502 + 可读error, 无200")
def t_six_endpoints_upstream_error():
    for path, fname in SIX:
        BEHAVIOR.clear()
        shimi_cache.MEM_CACHE.clear()
        shimi_cache.MEM_TTL.clear()
        BEHAVIOR[fname] = ConnectionError("Remote end closed connection without response")
        r = _client.get(path)
        assert r.status_code == 502, f"{path} 上游失败应502, 实际 {r.status_code} body={r.get_json()}"
        body = r.get_json()
        assert isinstance(body, dict) and "error" in body and body["error"], \
            f"{path} 缺可读error: {body!r}"
        assert "Remote end closed" in body["error"], f"{path} error不含原因: {body!r}"


@run("六端点: cache_or_fetch 自身直接抛异常 → 502 (非500堆栈/非200)")
def t_six_endpoints_cache_layer_raise():
    orig = mkt.cache_or_fetch

    def boom(*a, **kw):
        raise RuntimeError("cache backend broken")
    mkt.cache_or_fetch = boom
    try:
        for path, _ in SIX:
            shimi_cache.MEM_CACHE.clear()
            shimi_cache.MEM_TTL.clear()
            r = _client.get(path)
            assert r.status_code == 502, f"{path} 缓存层异常应502, 实际 {r.status_code}"
            assert "error" in (r.get_json() or {}), f"{path} 缺error体"
    finally:
        mkt.cache_or_fetch = orig


@run("六端点: 有效非空数据 → 200 + 原JSON契约")
def t_six_endpoints_valid_data():
    _set_all_healthy()
    for path, fname in SIX:
        shimi_cache.MEM_CACHE.clear()
        shimi_cache.MEM_TTL.clear()
        r = _client.get(path)
        assert r.status_code == 200, f"{path} 有效数据应200, 实际 {r.status_code}"
        assert r.get_json() == _healthy(fname), f"{path} 契约被改写: {r.get_json()!r}"


@run("六端点: 合法空列表 → 200 [] (空数据不是错误)")
def t_six_endpoints_legal_empty():
    for path, fname in SIX:
        BEHAVIOR.clear()
        shimi_cache.MEM_CACHE.clear()
        shimi_cache.MEM_TTL.clear()
        BEHAVIOR[fname] = []
        r = _client.get(path)
        assert r.status_code == 200 and r.get_json() == [], \
            f"{path} 合法空列表应200 [], 实际 {r.status_code} {r.get_json()!r}"


# ─── 4. dashboard 聚合容错 ──────────────────────────────────────
@run("dashboard: 单项失败 → 该项None+_errors记录, 健康项保留, 带降级头, 仍200")
def t_dashboard_partial_failure():
    _set_all_healthy()
    BEHAVIOR["fetch_sentiment"] = ConnectionError("sentiment upstream down")
    r = _client.get("/api/dashboard")
    assert r.status_code == 200, f"含成功项的聚合应200, 实际 {r.status_code}"
    body = r.get_json()
    assert body["sentiment"] is None, f"失败项应None: {body['sentiment']!r}"
    assert body["indices"] == _healthy("fetch_indices"), "健康项 indices 未保留"
    assert body["hot_stocks"] == _healthy("fetch_hot_stocks"), "健康项 hot_stocks 未保留"
    # 策略键 2026-10-25 起只读既有缓存 (不为聚合阻塞): 缓存未生成 → None 且不记错误
    for _k in ("strategy_trend", "strategy_hybrid", "strategy_dragon"):
        assert body[_k] is None, f"{_k} 无缓存应None"
    keys = [e["key"] for e in (body["_errors"] or [])]
    assert "sentiment" in keys, f"_errors 缺失败key: {body['_errors']!r}"
    assert not any(k.startswith("strategy_") for k in keys), "策略缓存缺失不应记错误"
    assert all(e.get("error") for e in body["_errors"]), "_errors 缺简短错误"
    assert r.headers.get("X-Shimi-Degraded") == "true", "缺 X-Shimi-Degraded 降级头"


@run("dashboard: 全部健康 → 200, _errors=None, 无降级头 (兼容旧契约)")
def t_dashboard_all_healthy():
    _set_all_healthy()
    # 模拟策略扫描写穿: /api/strategy/* 计算后 cache_set, dashboard 只读缓存
    for _k in ("strategy_trend", "strategy_hybrid", "strategy_dragon"):
        shimi_cache.cache_set(_k, _healthy(f"run_{_k.removeprefix('strategy_')}_scan"), 120)
    r = _client.get("/api/dashboard")
    assert r.status_code == 200, f"实际 {r.status_code}"
    body = r.get_json()
    assert body["_errors"] is None, f"_errors 应为 None: {body['_errors']!r}"
    assert "X-Shimi-Degraded" not in r.headers, "健康聚合不应带降级头"
    assert body["sentiment"] == _healthy("fetch_sentiment")
    assert body["strategy_trend"] == _healthy("run_trend_scan")


@run("health仅服务存活，不调用数据源；市场状态未知")
def t_health_does_not_fetch_source():
    calls = []
    BEHAVIOR["get_latest_date"] = lambda: calls.append("unexpected")
    r = _client.get("/api/health")
    assert r.status_code == 200 and r.get_json()["market_data_status"] == "not_checked"
    assert r.get_json()["latest_trade_date"] is None
    assert calls == [], "health不应请求上游"


@run("名录失败502，真实无匹配才返回空数组")
def t_lookup_failure_vs_no_match():
    for value in (RuntimeError("missing source"), {}, {"error": "no source"}, []):
        BEHAVIOR["get_stock_basic"] = value
        assert _client.get("/api/stock/lookup?q=60").status_code == 502
    BEHAVIOR["get_stock_basic"] = {"000001.SZ": {"name": "fictional"}}
    response = _client.get("/api/stock/lookup?q=60")
    assert response.status_code == 200 and response.get_json() == []


@run("K线源失败502，合法无记录404，不吞成500或成功")
def t_kline_source_failure():
    scorer = types.ModuleType('realtime_scorer')
    previous = sys.modules.get('realtime_scorer')
    sys.modules['realtime_scorer'] = scorer
    try:
        for message in ('transport_not_verified', 'synthetic source timeout'):
            def fail(*args, **kwargs):
                raise RuntimeError(message)
            scorer.get_kline = fail
            response = _client.get('/api/stock/600000/kline')
            assert response.status_code == 502
            assert message in response.get_json()['error']
            assert response.get_json()['data_status'] == 'unavailable'
        scorer.get_kline = lambda *args, **kwargs: None
        assert _client.get('/api/stock/600000/kline').status_code == 404
    finally:
        if previous is None:
            sys.modules.pop('realtime_scorer', None)
        else:
            sys.modules['realtime_scorer'] = previous


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
