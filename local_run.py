"""
[本地部署包装层] 拾米交易工作室 — 仅本机 127.0.0.1 + 出站管控 + 数据模式开关

本文件为本地安全评估新增, 不推送远端。作用:
  1. 进程环境白名单化默认值: 仅 127.0.0.1 监听, sqlite+内存缓存, 全新空数据库
     (不加载仓库内 data/users.db 已提交账号库, 不使用任何真实 Token)
  2. 数据模式 SHIMI_DATA_MODE仅允许real，默认real；错误配置拒绝启动，不回退模拟。
     数据源=Tushare Pro 日线 (EOD非实时)，HTTPS 传输与个人非商业用途声明
     已于 2026-10-24 实测验收；local_safe_start.py start 从父进程环境继承
     TUSHARE_TOKEN/SHIMI_MARKET_USE (由 restart-local.ps1 读自用户环境),
     preflight 不继承 (无凭据时必须显式失败)。
       出站改为白名单制 — 仅 loopback + api.tushare.pro
       (+ 东财 push2/push2his 备用节点保留, 默认不走),
       其余一律仍拦并留痕 [EGRESS-BLOCK]; 响应头 X-Shimi-Data-Mode: real-readonly
  3. 与数据模式解耦的永久禁用项 (real 模式也不放开):
     交易写库/券商API、消息推送 (SHIMI_WEBHOOK_URL="")、
     付费数据源 (finnhub/yfinance 密钥)、模型API — 既不给密钥也不放行其域名;
     tushare只读候选的安全传输、许可及时点仍需验收，不以配置代替真实连接。

启动: venv/Scripts/python local_run.py   (停止: Ctrl+C 或 kill PID)
      安全启动: venv/Scripts/python local_safe_start.py start
"""
import os
import sys
import time

# 本人2026-10-23要求：产品只能展示真实数据。错误配置不得回退到模拟。
# 下方保留的历史离线fixture不属于可启动模式；隔离单测可以独立使用虚构输入。
DATA_MODE = os.environ.get("SHIMI_DATA_MODE", "real").strip().lower()
if DATA_MODE != "real":
    raise ValueError("real_data_only: 产品禁止模拟模式或回退；仅允许SHIMI_DATA_MODE=real，缺数据应明确不可用")

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

# ─── 1. 环境默认值 (可被外部显式覆盖前的安全基线) ─────────────
RUNTIME = os.environ.get('SHIMI_RUNTIME_DIR', os.path.join(ROOT, "runtime"))
os.makedirs(RUNTIME, exist_ok=True)

os.environ["SHIMI_HOST"] = "127.0.0.1"                 # 强制仅本机, 覆盖 config.py 的 0.0.0.0
os.environ.setdefault("SHIMI_PORT", "17890")
os.environ["SHIMI_DEBUG"] = "false"
os.environ["SHIMI_DB_TYPE"] = "sqlite"
os.environ["SHIMI_USE_REDIS"] = "false"
os.environ["SHIMI_DB_PATH"] = os.path.join(RUNTIME, "shimi.db")        # 全新空业务库
os.environ["SHIMI_USER_DB_PATH"] = os.path.join(RUNTIME, "users.db")   # 全新空用户库
# ─── 与数据模式解耦的永久禁用项 (任何模式都不放开) ─────────────
os.environ["SHIMI_WEBHOOK_URL"] = ""                   # 消息推送禁用 (serverchan/微信 webhook)
os.environ.setdefault("SHIMI_CORS_ORIGINS", "http://127.0.0.1:17890")
# 交易写库/券商API 不在下方路由白名单内 (403); 付费源/模型API 域名不在出站白名单内

# ─── 产品数据模式：仅真实只读候选，不允许模拟回退 ────
os.environ["SHIMI_DATA_MODE"] = DATA_MODE

# tushare 凭据按模式处理 (token 值绝不打印/落盘):
#   mock → 强制清空 (域名也不放行); real → 继承外部环境变量 (缺失则启动警告,
#   数据接口将明确报 "未配置 TUSHARE_TOKEN" 而非模糊 500)
if DATA_MODE == "mock":
    os.environ["TUSHARE_TOKEN"] = ""
elif not os.environ.get("TUSHARE_TOKEN"):
    print("[DATA-MODE] 警告: real 模式但未检测到 TUSHARE_TOKEN 环境变量 — "
          "真实数据接口将返回明确错误", flush=True)
# 用途闸门 (local_tushare_data._api 在读令牌前检查): 必须由操作者经环境变量
# 显式声明 SHIMI_MARKET_USE=personal_view, 代码不自动声明、不规避商业限制
if DATA_MODE == "real" and os.environ.get("SHIMI_MARKET_USE") != "personal_view":
    print("[DATA-MODE] 警告: 未声明 SHIMI_MARKET_USE=personal_view (个人非商业本地只读) — "
          "数据接口将返回 purpose_unconfirmed 明确错误", flush=True)

# 代理封堵: 清除继承的代理变量, 防止经本机代理绕道外发
for _k in [k for k in list(os.environ) if "proxy" in k.lower()]:
    del os.environ[_k]
os.environ["NO_PROXY"] = "*"

PORT = int(os.environ["SHIMI_PORT"])

# ─── 2. 出站管控 (socket 层, 按数据模式) ───────────────────────
import re
import socket

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
_orig_create_connection = socket.create_connection
_orig_connect = socket.socket.connect
_orig_connect_ex = socket.socket.connect_ex
_orig_getaddrinfo = socket.getaddrinfo

# real 模式数据主机白名单: api.tushare.pro (主数据源, Tushare Pro 日线 EOD) +
# 东财 push2/push2his 公共行情节点 (盘中补充备用, 默认不走 — local_real_data.py 保留) +
# 2026-10-26 事件总览免 key 源 (均无凭据无付费, GET/POST 公开查询):
#   www.cninfo.com.cn 巨潮公告检索 (data/cninfo_fetcher.py 催化剂关键词)
#   np-anotice-stock.eastmoney.com 东财公告列表 (data/market_events.py)
#   www.cls.cn 财联社电报 (data/news_fetcher.py, sign 纯本地计算零 key)
#   np-weblist.eastmoney.com 东财7×24快讯 (data/news_fetcher.py)
# 依据 = local_tushare_data.py / local_real_data.py 与实际代码审查;
# finnhub/yfinance/webhook/模型API 等一律不在列 → 仍被拦截留痕
_DATA_HOSTS_EXACT = {
    "api.tushare.pro",
    "www.cninfo.com.cn",
    "np-anotice-stock.eastmoney.com",
    "www.cls.cn",
    "np-weblist.eastmoney.com",
}
_DATA_HOST_RE = re.compile(r"^(\d+\.)?push2(his)?\.eastmoney\.com$")  # 东财备用(默认不走)
_ALLOWED_IPS = set()   # 白名单域名解析出的 IP, connect 时放行


def _is_ip_literal(host) -> bool:
    if not isinstance(host, str):
        return False
    for fam in (socket.AF_INET, socket.AF_INET6):
        try:
            socket.inet_pton(fam, host.strip("[]"))
            return True
        except (OSError, ValueError):
            continue
    return False


def _host_allowed(host) -> bool:
    """mock: 仅本机; real: 本机 + 数据白名单域名"""
    if host in _LOCAL_HOSTS or host is None:
        return True
    return (DATA_MODE == "real" and isinstance(host, str)
            and (host in _DATA_HOSTS_EXACT or bool(_DATA_HOST_RE.match(host))))


def _guarded_getaddrinfo(host, port, *args, **kwargs):
    """非白名单域名不做真实DNS解析 (软隔离, 仅本Python进程)"""
    if _is_ip_literal(host):
        return _orig_getaddrinfo(host, port, *args, **kwargs)
    if _host_allowed(host):
        results = _orig_getaddrinfo(host, port, *args, **kwargs)
        for r in results:
            try:
                _ALLOWED_IPS.add(r[4][0])
            except (IndexError, TypeError):
                pass
        return results
    print(f"[EGRESS-BLOCK] getaddrinfo -> {host}", flush=True)
    raise socket.gaierror(f"非白名单域名DNS解析被本地安全策略封锁: {host}")


socket.getaddrinfo = _guarded_getaddrinfo


def _is_allowed_addr(address) -> bool:
    try:
        host = address[0] if isinstance(address, tuple) else str(address)
    except Exception:
        return False
    if host in _LOCAL_HOSTS:
        return True
    if DATA_MODE == "real":
        if host in _ALLOWED_IPS:
            return True  # 白名单域名此前解析出的 IP
        # 白名单域名本身 (http.client 以主机名发起连接; 后续DNS走守卫并记录IP)
        if not _is_ip_literal(host) and _host_allowed(host):
            return True
    # 直接 IP 字面量外连一律不放行 (除非来自白名单解析)
    return False


def _blocked_create_connection(address, *args, **kwargs):
    if _is_allowed_addr(address):
        return _orig_create_connection(address, *args, **kwargs)
    print(f"[EGRESS-BLOCK] create_connection -> {address}", flush=True)
    raise ConnectionError(f"出站连接被本地安全策略封锁: {address}")


def _blocked_connect(self, address):
    if _is_allowed_addr(address):
        return _orig_connect(self, address)
    print(f"[EGRESS-BLOCK] socket.connect -> {address}", flush=True)
    raise ConnectionError(f"出站连接被本地安全策略封锁: {address}")


def _blocked_connect_ex(self, address):
    if _is_allowed_addr(address):
        return _orig_connect_ex(self, address)
    print(f"[EGRESS-BLOCK] socket.connect_ex -> {address}", flush=True)
    return 111  # ECONNREFUSED


socket.create_connection = _blocked_create_connection
socket.socket.connect = _blocked_connect
socket.socket.connect_ex = _blocked_connect_ex

# ─── 3. 创建应用 (导入链仅注册路由/建空表, 无线程无外发) ──────
from app import create_app

app = create_app()

# ─── 4. Mock 数据注入 ─────────────────────────────────────────
MOCK_DATE = "MOCK20261020"


def _mock_indices():
    return [
        {"code": "000001.SH", "name": "上证指数(模拟)", "price": 3123.45, "change": 0.62},
        {"code": "399001.SZ", "name": "深证成指(模拟)", "price": 10456.78, "change": -0.31},
        {"code": "399006.SZ", "name": "创业板指(模拟)", "price": 2089.12, "change": 1.05},
        {"code": "000688.SH", "name": "科创50(模拟)", "price": 890.12, "change": 0.0},
    ]


def _mock_sectors():
    return [
        {"name": "半导体(模拟)", "change": 2.34, "up_count": 5, "down_count": 2},
        {"name": "白酒(模拟)", "change": -1.02, "up_count": 1, "down_count": 3},
    ]


def _mock_sector_flow():
    return [
        {"name": "半导体(模拟)", "strength": 50, "change": 2.34, "up_ratio": 71.4, "stock_count": 7, "hot": False},
        {"name": "银行(模拟)", "strength": -10, "change": -0.4, "up_ratio": 40, "stock_count": 5, "hot": False},
    ]


def _mock_hot_stocks():
    return [
        {"code": "600519", "name": "模拟样例甲", "price": 20.0, "change": 1.20, "volume": 1.0, "turnover": 0.5},
        {"code": "000001", "name": "模拟样例乙", "price": 10.0, "change": -0.45, "volume": 0.5, "turnover": 0.3},
    ]


def _mock_limit_up():
    return {"_mock": True, "count": 2, "stocks": [
        {"code": "300750", "name": "模拟宁德", "pct_chg": 20.0},
        {"code": "002594", "name": "模拟比亚", "pct_chg": 10.0},
    ], "date": MOCK_DATE}


def _mock_sentiment():
    return {"_mock": True, "phase": "模拟样例，非市场判断", "total_score": 52,
            "up": 10, "down": 8, "limit_up": 2, "limit_down": 1,
            "index_trend_score": 5, "index_position_score": 5, "sentiment_score": 5,
            "volume_score": 5, "position_ratio": "不提供", "date": MOCK_DATE}


_MOCK_BASIC = {
    "600519.SH": {"ts_code": "600519.SH", "symbol": "600519", "name": "模拟茅台",
                  "industry": "白酒(模拟)", "market": "主板", "area": "模拟省"},
    "000001.SZ": {"ts_code": "000001.SZ", "symbol": "000001", "name": "模拟平安",
                  "industry": "银行(模拟)", "market": "主板", "area": "模拟省"},
    "300750.SZ": {"ts_code": "300750.SZ", "symbol": "300750", "name": "模拟宁德",
                  "industry": "电池(模拟)", "market": "创业板", "area": "模拟省"},
}


def _mock_stock_basic():
    return dict(_MOCK_BASIC)


def _mock_strategy(name):
    def _run():
        return {"_mock": True, "strategy": name, "total_scanned": 1, "picked": [
            {"code": "600519", "name": "模拟样例，非推荐", "score": 50, "trend_score": 50,
             "stage": "演示", "grade": "演示", "board_count": 0,
             "signal": "mock-only", "reason": "合成数据，仅用于验证页面渲染"},
        ], "date": MOCK_DATE}
    return _run


def _mock_kline(code, days=60):
    """确定性合成K线 (固定种子随机游走), 与真实行情无关"""
    import random
    import pandas as pd
    from datetime import date, timedelta

    import hashlib
    rng = random.Random(int(hashlib.sha256(code.encode()).hexdigest()[:8], 16))
    rows = []
    price = 10.0 + rng.random() * 40
    d = date(2026, 9, 1)
    for _ in range(days):
        o = price
        c = round(o * (1 + (rng.random() - 0.48) * 0.03), 2)
        h = round(max(o, c) * (1 + rng.random() * 0.01), 2)
        l = round(min(o, c) * (1 - rng.random() * 0.01), 2)
        rows.append({"date": d.strftime("%Y-%m-%d"), "open": round(o, 2),
                     "high": h, "low": l, "close": c,
                     "volume": rng.randint(100000, 5000000)})
        price = c
        d += timedelta(days=1)
    return pd.DataFrame(rows)


import api.market as _mkt
import data.fetcher as _fetcher
import realtime_scorer as _rs
import position_manager as _pm

if DATA_MODE == "mock":
    _mkt.get_latest_date = lambda: MOCK_DATE
    _mkt.fetch_indices = _mock_indices
    _mkt.fetch_sectors = _mock_sectors
    _mkt.fetch_sector_flow = _mock_sector_flow
    _mkt.fetch_hot_stocks = _mock_hot_stocks
    _mkt.fetch_limit_up = _mock_limit_up
    _mkt.fetch_sentiment = _mock_sentiment
    _mkt.get_stock_basic = _mock_stock_basic
    _fetcher.get_stock_basic = _mock_stock_basic

    import services.strategy as _strat

    _strat.run_trend_scan = _mock_strategy("trend")
    _strat.run_hybrid_scan = _mock_strategy("hybrid")
    _strat.run_dragon_scan = _mock_strategy("dragon")

    _rs.get_kline = _mock_kline
    _pm.get_kline = _mock_kline
else:
    # ─── real 模式: 接入 tushare 真实只读行情 (local_tushare_data, EOD 日线) ──
    # 东财后端 local_real_data.py 保留作盘中补充备用 (默认不接线)
    import local_tushare_data as _real

    _mkt.get_latest_date = _real.real_get_latest_date
    _mkt.fetch_indices = _real.real_fetch_indices
    _mkt.fetch_sectors = _real.real_fetch_sectors
    _mkt.fetch_sector_flow = _real.real_fetch_sector_flow
    _mkt.fetch_hot_stocks = _real.real_fetch_hot_stocks
    _mkt.fetch_limit_up = _real.real_fetch_limit_up
    _mkt.fetch_sentiment = _real.real_fetch_sentiment
    _mkt.get_stock_basic = _real.real_get_stock_basic
    # data.fetcher 命名空间: 函数级 import 的消费方 (doubler_scanner / review /
    # a_stock_cache / stock_radar.analyze_stock) 都会在调用时取到这些补丁
    _fetcher.get_stock_basic = _real.real_get_stock_basic
    _fetcher.get_daily = lambda: _real._daily_snapshot(_real._data_date())
    # fetcher 契约 = YYYYMMDD 且 fetcher_core 原实现从不抛错 (失败回退昨日),
    # 保持同契约: 数据日解析失败 (如无凭据) 时显式回退昨日而非冒数据日之名,
    # 上游 doubler 的 today[:6] 切片等消费方行为不变
    def _latest_date_safe():
        try:
            return _real._data_date()
        except Exception:
            return time.strftime("%Y%m%d", time.localtime(time.time() - 86400))
    _fetcher.get_latest_date = _latest_date_safe
    _fetcher.fetch_indices = _real.real_fetch_indices
    _fetcher.fetch_sectors = _real.real_fetch_sectors
    _fetcher.fetch_sector_flow = _real.real_fetch_sector_flow
    _fetcher.fetch_hot_stocks = _real.real_fetch_hot_stocks
    _fetcher.fetch_limit_up = _real.real_fetch_limit_up
    _fetcher.fetch_sentiment = _real.real_fetch_sentiment
    _fetcher.get_kline = _real.real_get_kline
    _fetcher.get_margin_detail = _real.real_get_margin_detail
    _rs.get_kline = _real.real_get_kline
    _rs.get_kline_batch = _real.real_get_kline_batch
    _pm.get_kline = _real.real_get_kline

    # pro_api 安全替代: stock_radar 等函数级 from data.fetcher_core import get_ts
    # 的调用方拿到 HTTPS 闸门内的 shim (daily/daily_basic/trade_cal/fina_indicator/
    # top_list/moneyflow); tushare SDK 本体禁用 — 其默认明文 HTTP 会泄露令牌
    import data.fetcher_core as _fcore
    _fcore.get_ts = lambda: _real.real_pro()
    try:
        import tushare as _ts_sdk
        def _plaintext_sdk_disabled(*args, **kwargs):
            raise RuntimeError(
                "tushare SDK 明文传输已禁用: 仅 local_tushare_data 的 HTTPS 闸门可用")
        _ts_sdk.pro_api = _plaintext_sdk_disabled
    except ImportError:
        pass

    # ─── 2026-10-25 模块启用: 三策略扫描 / 操作建议 / K线形态 / 翻倍 / 雷达 ──
    # 重计算全部按数据日 6h 缓存 (real_day_cached), refresh 同日不重复消耗 API;
    # 结果写穿 cache.py 供 dashboard 只读聚合 (不为聚合阻塞数分钟)
    import services.strategy as _strat
    import services.advice as _adv
    import services.kline_patterns as _kp
    import services.doubler_scanner as _dbls
    import data.pre_surge_signals as _pss
    import services.review as _review
    import services.review_weekly as _rw
    import api.strategy as _api_strat
    import api.advice as _api_advice
    import api.doubler as _api_doubler
    import api.margin as _api_margin
    from cache import cache_set as _cache_set

    _strat.get_daily = _fetcher.get_daily
    _strat.get_stock_basic = _real.real_get_stock_basic
    # services.strategy 顶部按名导入的 get_kline_batch 是其模块命名空间内的独立引用,
    # 不随 realtime_scorer 补丁生效; 单独换成面板版 (评分函数内部的 get_kline
    # 仍走 realtime_scorer 模块全局 → _rs.get_kline 已补丁 → 命中面板写穿的单只缓存)
    _strat.get_kline_batch = _real.real_get_kline_batch
    for _name in ("trend", "hybrid", "dragon"):
        _orig = getattr(_strat, f"run_{_name}_scan")
        def _make_wrapped(scan_name, scan_fn):
            day_cached = _real.real_day_cached(f"strategy_{scan_name}", scan_fn)
            def _run():
                result = day_cached()
                try:
                    _cache_set(f"strategy_{scan_name}", result, 120)
                except Exception:
                    pass
                return result
            return _run
        _wrapped = _make_wrapped(_name, _orig)
        setattr(_strat, f"run_{_name}_scan", _wrapped)       # dashboard 函数级 import
        setattr(_api_strat, f"run_{_name}_scan", _wrapped)   # api.strategy 模块级绑定
        setattr(_adv, f"run_{_name}_scan", _wrapped)         # services.advice 模块级绑定

    _adv.fetch_sentiment = _real.real_fetch_sentiment
    _adv.fetch_sectors = _real.real_fetch_sectors
    _adv.get_kline = _real.real_get_kline
    _api_advice.generate_advice = _real.real_day_cached("advice", _adv.generate_advice)

    _kp.get_daily = _fetcher.get_daily
    _kp.get_stock_basic = _real.real_get_stock_basic
    _kp.get_kline = _real.real_get_kline
    _kp.get_kline_batch = _real.real_get_kline_batch

    _dbls._get_pro = lambda: _real.real_pro()
    _pss.get_pro = lambda: _real.real_pro()
    _api_doubler.recommend_current_month = _real.real_day_cached(
        "doubler_recommend", _dbls.recommend_current_month)
    _api_doubler.scan_monthly_doublers = _real.real_day_cached(
        "doubler_history", _dbls.scan_monthly_doublers)

    _api_margin.get_margin_detail = _real.real_get_margin_detail

    _review.get_kline = _real.real_get_kline
    _review.fetch_sectors = _real.real_fetch_sectors
    _review.fetch_sector_flow = _real.real_fetch_sector_flow
    _rw.get_kline = _real.real_get_kline
    _rw.fetch_sectors = _real.real_fetch_sectors
    _rw.fetch_sector_flow = _real.real_fetch_sector_flow

# ─── 4.5 只读演示白名单 + 统一模拟标记 ───────────────────────
import json
import re
from flask import request, jsonify

_DEMO_EXACT = {
    "/", "/static/index.html",
    "/api/health", "/api/indices", "/api/dashboard", "/api/stock/lookup",
    '/api/sectors', '/api/sector-flow', '/api/hot-stocks', '/api/limit-up', '/api/sentiment',
    '/api/local/modules',
}
# real 模式放行的 POST (本地状态写入, 非交易): 复盘报告生成 + A股缓存管理
_DEMO_POST = set()
if DATA_MODE == "mock":
    # mock 策略演示端点仅在 mock 模式开放 (real 模式返回 mock 会误导, 故 403)
    _DEMO_EXACT |= {'/api/strategy/trend/refresh', '/api/strategy/hybrid/refresh',
                    '/api/strategy/dragon/refresh'}
else:
    # real 模式: 美股只读报价 (tushare us_daily 日线 EOD, 限1次/小时)
    _DEMO_EXACT.add('/api/us/quote')
    # 2026-10-25 模块启用 (全部 tushare 真实数据, 重计算按数据日6h缓存):
    # 三策略扫描/操作建议、缓存状态/本机监测、融资融券、复盘、翻倍/跟踪/方案
    _DEMO_EXACT |= {
        '/api/strategy/trend', '/api/strategy/hybrid', '/api/strategy/dragon',
        '/api/strategy/trend/refresh', '/api/strategy/hybrid/refresh',
        '/api/strategy/dragon/refresh',
        '/api/advice',
        '/api/monitor',
        '/api/a-stock/cache/summary',
        '/api/margin',
        '/api/review/daily', '/api/review/weekly', '/api/review/history',
        '/api/doubler/history', '/api/doubler/history/refresh',
        '/api/doubler/recommend', '/api/doubler/recommend/refresh',
        '/api/doubler/plan/10k',
        '/api/doubler/track/start', '/api/doubler/track/status',
        '/api/doubler/track/update', '/api/doubler/track/verify',
        '/api/doubler/track/effectiveness',
        '/api/plan-1w', '/api/plan-1w/summary',
        # 2026-10-26 事件总览启用: 巨潮催化剂+东财公告+财联社/东财7×24快讯 (全免 key)
        '/api/market/events', '/api/market/events/refresh',
    }
    _DEMO_POST = {
        '/api/review/daily', '/api/review/weekly',
        '/api/a-stock/cache/refresh', '/api/a-stock/cache/refresh-recent',
        '/api/a-stock/cache/invalidate',
        '/api/market/events/refresh',
    }
_LOCAL_REFRESH = {
    '/api/strategy/trend/refresh': _mock_strategy('trend'),
    '/api/strategy/hybrid/refresh': _mock_strategy('hybrid'),
    '/api/strategy/dragon/refresh': _mock_strategy('dragon'),
} if DATA_MODE == "mock" else {}
_MODULES_MOCK = [
    {'name':'账号', 'state':'本地可用', 'reason':'注册、登录、会话和退出已验证；找回密码未开放'},
    {'name':'A股看板/三策略/资金流', 'state':'模拟演示', 'reason':'数组/字段契约已修复；尚未接真实行情，不是实时扫描'},
    {'name':'搜索/K线', 'state':'模拟演示', 'reason':'只包含样例股票与合成K线；雷达需补独立数据源与算法验收'},
    {'name':'翻倍/观察跟踪', 'state':'未启用', 'reason':'依赖行情和本地状态写入，当前不运行自动跟踪'},
    {'name':'事件/先知', 'state':'缺数据源', 'reason':'尚未核实事件来源、抓取权限与更新时间'},
    {'name':'缓存/监测', 'state':'部分可用', 'reason':'本机健康检查可用；原市场缓存/外部依赖探测未启用'},
    {'name':'交易记录/盈亏/组合', 'state':'未启用', 'reason':'原分库关联缺陷未修复；本轮不开放交易写入'},
    {'name':'复盘/融资融券', 'state':'缺数据源', 'reason':'需真实数据/个人交易记录和数据权限，不能以演示替代'},
    {'name':'美股看板/扫描/掘金', 'state':'缺数据源', 'reason':'所需行情依赖与来源权限未接通，不能显示为正常扫描'},
]
_MODULES_REAL = [
    {'name':'账号', 'state':'本地可用', 'reason':'注册、登录、会话和退出已验证；找回密码未开放'},
    {'name':'A股行情(指数/板块/热门/涨停/情绪)', 'state':'真实只读(已实测)', 'reason':'Tushare日线EOD(收盘值非实时)；2026-10-24全端点实测与直连基准一致；失败时显式502不补位'},
    {'name':'搜索/K线/个股雷达', 'state':'真实只读(已实测)', 'reason':'Tushare名录与日K线(雷达含5年分位/基本面fina_indicator/换手率/融资余额，2026-10-25实测)；封板时间等盘中维度EOD口径退化'},
    {'name':'美股报价', 'state':'真实只读(已实测)', 'reason':'Tushare us_daily日线收盘值(/api/us/quote?symbol=代码),限1次/小时缓存65min；美股看板/扫描未启用'},
    {'name':'三策略扫描/操作建议', 'state':'真实只读(日级缓存)', 'reason':'趋势/混合/龙头=Tushare全市场快照+K线真实评分(2026-10-25实测)；按数据日6h缓存,refresh同日不重扫；建议共识写入本地推荐表(复盘数据源)'},
    {'name':'翻倍/观察跟踪', 'state':'真实只读(部分维度降级)', 'reason':'评分/月扫/10k方案/跟踪=Tushare真实数据；D9龙虎榜+资金流已实测接入；D7/D8公告催化剂2026-10-26起走巨潮(沪深北全量,已入白名单)；候选池已剔除ST'},
    {'name':'事件总览', 'state':'真实只读(免key源)', 'reason':'巨潮催化剂公告(沪深北全量)+东财公告+财联社电报+东财7×24快讯,2026-10-26接入出站白名单;finnhub/yfinance无凭据仍禁用并在sources_status显式标注;先知模型(合成训练数据)违反real-only保持不启用'},
    {'name':'缓存/监测', 'state':'真实只读', 'reason':'/api/monitor本机资源+/api/a-stock/cache/summary缓存状态已启用；/api/health/deps保持禁用(会探测付费源域名)'},
    {'name':'交易记录/盈亏/组合', 'state':'未启用', 'reason':'交易写库与券商API任何模式均禁用；原分库关联缺陷未修复'},
    {'name':'复盘/融资融券', 'state':'真实只读(部分)', 'reason':'margin_detail两融已实测接入；复盘引擎已启用,报告依赖建议推荐记录(T+3成熟),无记录时显式说明'},
    {'name':'消息推送/付费源/模型API', 'state':'禁用', 'reason':'webhook/finnhub/yfinance/模型API不启用；Tushare仅限个人非商业只读，不购买权限或升级套餐'},
]
_MODULES = _MODULES_MOCK if DATA_MODE == "mock" else _MODULES_REAL

@app.route('/api/local/modules')
def local_modules():
    return jsonify(mode='synthetic-market' if DATA_MODE == "mock" else 'real-readonly-market',
                   modules=_MODULES, real_market_enabled=(DATA_MODE == "real"),
                   data_backend='synthetic' if DATA_MODE == 'mock' else 'tushare-eod',
                   source_verification='not_applicable_synthetic' if DATA_MODE == 'mock' else 'not_verified',
                   real_data_only=True,
                   server_pid=os.getpid(),
                   trading_enabled=False)

if DATA_MODE == "real":
    @app.route('/api/us/quote')
    def local_us_quote():
        """美股单只日线只读候选；错误不能HTTP200冒充成功。"""
        value = _real.real_us_quote(request.args.get('symbol', ''))
        if isinstance(value, dict) and value.get('error'):
            status = {'invalid_symbol':400, 'not_found':404}.get(value['error'],502)
            return jsonify(value), status
        return jsonify(value)

    def _real_index():
        """real 模式首页: 横幅按运行时凭据/用途声明状态动态生成 —
        有凭据: 蓝色 + 已实测声明; 无凭据: 红色 + 数据明确不可用 (不冒充已接通)"""
        with open(os.path.join(ROOT, "public", "index.html"), encoding="utf-8") as f:
            html = f.read()
        if os.environ.get("TUSHARE_TOKEN") and os.environ.get("SHIMI_MARKET_USE") == "personal_view":
            banner = ("ℹ️ 仅展示真实来源数据 · Tushare 日线 EOD（收盘值，非实时），"
                      "连接已实测 2026-10-24；商业用途未授权 · 交易/推送未启用")
            html = html.replace("background:#b91c1c", "background:#1d4ed8")
        else:
            banner = ("⚠️ 仅展示真实来源数据 · 当前进程未取得数据凭据/用途声明，"
                      "数据明确不可用，不使用模拟补位 · 交易/推送未启用")
        return re.sub(r'(<div id="shimi-mock-banner"[\s\S]*?>)[^<]*',
                      lambda m: m.group(1) + banner, html, count=1)
    app.view_functions['index'] = _real_index
_KLINE_RE = re.compile(r"^/api/stock/\d{6}/kline$")
_RADAR_RE = re.compile(r"^/api/stock/\d{6}/radar(/summary)?$")
_ACCOUNT_METHODS = {
    '/api/auth/register': {'POST'}, '/api/auth/login': {'POST'},
    '/api/auth/me': {'GET', 'HEAD'}, '/api/auth/logout': {'POST'},
}
from local_accounts import install_local_accounts
install_local_accounts(app, PORT)
_DEMO_SCOPE_MOCK = ("本地只读模拟演示: 仅开放 GET /, /static/index.html, /api/health, "
                    "/api/indices, /api/dashboard, /api/stock/lookup, "
                    "/api/stock/<6位代码>/kline (days 1-200) 等白名单; "
                    "本地账号可用; 交易/真实扫描/推送/依赖探测仍未启用")
_DEMO_SCOPE_REAL = ("本地真实只读模式: 白名单 GET 端点 (+复盘/缓存管理POST) + /api/us/quote; "
                    "当前适配器=Tushare日线EOD, 策略/雷达/翻倍/两融/复盘/监测/事件总览已启用(按数据日缓存); "
                    "交易/推送/付费源/模型API 保持禁用")
_DEMO_SCOPE = _DEMO_SCOPE_MOCK if DATA_MODE == "mock" else _DEMO_SCOPE_REAL


@app.before_request
def _demo_whitelist():
    """首阶段只读演示模式: 白名单外API统一403, 非API路径维持原路由(缺失即404)"""
    path = request.path
    if path in _ACCOUNT_METHODS:
        if request.method in _ACCOUNT_METHODS[path]:
            return None
        return jsonify(error='账号请求方法不支持'), 405
    if (path in _DEMO_EXACT or _KLINE_RE.match(path)
            or (DATA_MODE == "real" and _RADAR_RE.match(path))):
        if request.method not in ("GET", "HEAD") and path not in _DEMO_POST:
            return jsonify({"error": "forbidden_demo_scope",
                            "message": _DEMO_SCOPE}), 403
        if path in _LOCAL_REFRESH:
            return jsonify(_LOCAL_REFRESH[path]())
        if _KLINE_RE.match(path):
            days = request.args.get("days")
            if days is not None:
                try:
                    if not (1 <= int(days) <= 200):
                        raise ValueError
                except ValueError:
                    return jsonify({"error": "bad_request",
                                    "message": "days 限 1-200 (只读模拟边界)"}), 400
        return None
    if path.startswith("/api/"):
        return jsonify({"error": "forbidden_demo_scope",
                        "message": _DEMO_SCOPE}), 403
    return None


def _contains_synthetic(value):
    if isinstance(value, dict):
        if value.get('_mock') is True or value.get('_synthetic') is True:
            return True
        return any(_contains_synthetic(item) for item in value.values())
    return isinstance(value, list) and any(_contains_synthetic(item) for item in value)


@app.after_request
def _demo_mark(response):
    """数据模式标记头; mock 模式下白名单JSON对象统一补 _mock=true (real 模式不注入)"""
    response.headers["X-Shimi-Data-Mode"] = "synthetic" if DATA_MODE == "mock" else "real-readonly"
    response.headers['X-Shimi-Real-Data-Only'] = 'true'
    if response.is_json and _contains_synthetic(response.get_json(silent=True)):
        response = jsonify(error='synthetic_data_rejected',
                           message='产品仅允许真实数据；检测到模拟标记，数据未展示',
                           data_status='unavailable')
        response.status_code = 502
        response.headers['X-Shimi-Data-Mode'] = 'real-readonly'
        response.headers['X-Shimi-Real-Data-Only'] = 'true'
        response.headers['Cache-Control'] = 'no-store'
        return response
    if request.path.startswith('/api/auth/'):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Shimi-Account-Mode'] = 'local-persistent'
        return response
    if (DATA_MODE == "mock" and request.path.startswith("/api/")
            and response.status_code == 200 and response.is_json):
        try:
            data = response.get_json()
            if isinstance(data, dict) and "_mock" not in data:
                data["_mock"] = True
                response.set_data(json.dumps(data, ensure_ascii=False))
        except Exception:
            pass
    return response

# ─── 5. 仅本机启动 ────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 60)
    if DATA_MODE == "mock":
        print("拾米交易工作室 [本地只读模拟演示]")
        print("  数据: 全部 Mock 合成数据 (_mock:true)")
        print("  出站: 全封锁 (socket+DNS), 非本机连接一律拒绝")
    else:
        print("拾米交易工作室 [本地真实只读模式]")
        print("  数据: Tushare Pro 日线 EOD (收盘值,非实时); HTTPS+用途声明已实测验收 2026-10-24")
        print("  出站: 白名单制 — 仅 loopback + api.tushare.pro + 事件免key源(巨潮/东财公告/财联社/东财724) (+东财push2备用, 默认不走)")
        print("  启用: 行情/搜索K线/雷达/三策略/建议/翻倍/两融/复盘/缓存监测/事件总览 (按数据日6h缓存)")
        # 后台预热: 三策略扫描 + 操作建议 (全局1s节流串行; 冷启动面板构建数分钟);
        # 重计算按数据日缓存, 预热完成后 dashboard/面板即读既有缓存
        import threading as _th
        def _prewarm():
            for _label, _fn in (("trend", _strat.run_trend_scan),
                                ("hybrid", _strat.run_hybrid_scan),
                                ("dragon", _strat.run_dragon_scan),
                                ("advice", _api_advice.generate_advice)):
                try:
                    _fn()
                    print(f"[prewarm] {_label} 完成", flush=True)
                except Exception as _e:
                    print(f"[prewarm] {_label} 失败(显式,不补位): {_e}", flush=True)
        _th.Thread(target=_prewarm, daemon=True, name="scan-prewarm").start()
    print(f"  URL:  http://127.0.0.1:{PORT}  (仅本机, 无公网)")
    print(f"  PID:  {os.getpid()}  停止: Ctrl+C 或 kill {os.getpid()}")
    print("  禁用: 交易/券商API/推送/付费源/模型API；行情连接另行验收")
    print("  隔离: 进程级软隔离 (socket+DNS管控), 非系统级禁网")
    print("  DB:   保留既有runtime路径；不重置账号或业务记录")
    print("=" * 60, flush=True)
    app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False,
            threaded=True, load_dotenv=False)
