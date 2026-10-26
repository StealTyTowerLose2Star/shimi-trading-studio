"""
[本地真实只读数据层] 东方财富公共行情接口 — 仅供个人本地只读使用

仅在 SHIMI_DATA_MODE=real 时由 local_run.py 加载。特点:
  - 零新增依赖 (urllib 直连东财 push2/push2his 公共接口, 无需 token/付费)
  - 只读: 仅行情查询 (指数/板块/个股报价/涨跌统计/K线/美股报价),
    无任何交易、账户、推送、写库能力
  - 显式禁用代理 (ProxyHandler({})): 防止 urllib 读系统注册表代理绕道,
    保证出站只走 local_run.py 的东财白名单直连
  - 来源许可、访问条件、数据时点尚未独立验收；实现存在不等于获准接通或商用

数据契约与 data/fetcher_indices.py / fetcher_sentiment.py 保持一致,
使 api/market.py 的白名单只读端点原样工作。
"""

import json
import math
import re
import time
import urllib.request

import pandas as pd

# ─── 东财端点 (local_run.py 出站白名单即按此清单放行, 其余域名一律拦截) ──
# fltt=2&invt=2: 让 push2 返回浮点原价 (缺省返回放大100/1000倍的整数)
# ut: 东财网页端公开通用参数 (其官网前端内嵌, 非付费密钥), kline 接口缺省会 rc=102
ULIST_URL = "http://push2.eastmoney.com/api/qt/ulist.np/get"
CLIST_URL = "http://80.push2.eastmoney.com/api/qt/clist/get"
KLINE_URL = "http://push2his.eastmoney.com/api/qt/stock/kline/get"
_UT = "fa5fd1943c7b386f172d6893dbfba10b"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://quote.eastmoney.com/",
}

# 显式空代理: 不读系统注册表代理, 不读环境变量代理
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# ─── 请求节流 (公共接口有 WAF 频率限制, 过快会临时封IP; 实测0.2s仍会触发, 故1s) ─
_last_req = [0.0]


def _pace(interval=1.0):
    dt = time.time() - _last_req[0]
    if dt < interval:
        time.sleep(interval - dt)
    _last_req[0] = time.time()


# ─── 轻量内存缓存 (避免频繁请求公共接口) ──────────────────────
_cache = {}


def _cached(key, ttl, fn):
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    val = fn()
    _cache[key] = (now, val)
    return val


def _http_json(url, timeout=10):
    _pace()
    req = urllib.request.Request(url, headers=_HEADERS)
    with _OPENER.open(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _num(v, default=None):
    """通用数值解析: None/空串/破折号/非法数字/NaN/Inf → default。
    不再全局把 -1 当缺失: -1 是合法涨跌幅 (-1%), 只有价格字段上下文
    (见 _price) 才把东财的 -1 视为停牌/无数据。"""
    try:
        if isinstance(v, bool):
            return default
        if v is None or (isinstance(v, str) and v.strip() in ("-", "")):
            return default
        f = float(v)
        if math.isnan(f) or math.isinf(f):  # NaN/Inf 非法, 不得进入统计
            return default
        return f
    except (TypeError, ValueError, OverflowError):
        return default


def _price(v):
    """价格字段: 缺失/非法/NaN/Inf/东财停牌标记 -1 → None (显式未知),
    绝不回退成 0 假价；股票/指数价格必须严格为正。
    真实0涨跌/成交量由各自字段处理，不能据此接受0价格。"""
    f = _num(v, None)
    if f is None or f <= 0:
        return None
    return f


class MarketDataUnavailable(RuntimeError):
    """上游行情源失败/无有效样本 — 调用方必须显式失败,
    不得把超时/坏响应/空样本当作正常空数据继续产出行情或分数"""


def _payload_rows(payload, field, *, allow_empty=False):
    """Validate source envelope before deriving/cacheing successful results."""
    if not isinstance(payload, dict) or payload.get("rc", 0) != 0:
        raise MarketDataUnavailable("行情响应无效或来源返回错误")
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get(field), list):
        raise MarketDataUnavailable(f"行情响应缺少有效{field}列表")
    rows = data[field]
    if not rows and not allow_empty:
        raise MarketDataUnavailable(f"行情{field}没有可用记录")
    return rows


def _required_number(value, field, *, count=False):
    number = _num(value)
    if number is None or (count and (number < 0 or not number.is_integer())):
        raise MarketDataUnavailable(f"行情必要字段缺失或非法: {field}")
    return int(number) if count else number


# ─── 代码转换 ────────────────────────────────────────────────

def _secid_cn(code: str) -> str:
    """A股代码 → 东财 secid (1=沪, 0=深/创); 已是 secid 形式则原样返回"""
    code = code.strip().upper()
    if code.endswith(".SH"):
        return "1." + code[:-3]
    if code.endswith(".SZ"):
        return "0." + code[:-3]
    if re.match(r"^\d+\.[A-Z0-9]+$", code):
        return code  # 已是 secid (如 1.000001 上证指数)
    return ("1." if code[0] in "69" else "0.") + code


def _ts_code(code: str) -> str:
    code = code.strip()
    if "." in code:
        return code.upper()
    suffix = ".SH" if code[0] in "69" else ".SZ"
    return code + suffix


# ─── 报价 (ulist: 指数/个股/美股通用) ─────────────────────────

def _ulist_quotes(secids):
    """一次请求多个 secid 的 最新价/涨跌幅/代码/名称 → {secid: {...}}
    价格缺失/停牌 → 该条剔除; 涨跌幅缺失 → None (显式未知, 不填假0)"""
    url = f"{ULIST_URL}?fltt=2&invt=2&fields=f2,f3,f12,f14&secids={','.join(secids)}"
    data = _http_json(url)
    out = {}
    diff = _payload_rows(data, "diff", allow_empty=True)
    for item in diff:
        if not isinstance(item, dict):
            raise MarketDataUnavailable("行情报价记录不是对象")
        sid = str(item.get("f12", ""))
        price = _price(item.get("f2"))
        if price is None:
            continue
        change = _num(item.get("f3"), None)
        out[sid] = {
            "price": round(price, 2),
            "change": round(change, 2) if change is not None else None,
            "name": str(item.get("f14", "")),
        }
    return out


# ─── A股: 四大指数 ───────────────────────────────────────────
_INDEX_SECIDS = [("1.000001", "000001.SH", "上证指数"),
                 ("0.399001", "399001.SZ", "深证成指"),
                 ("0.399006", "399006.SZ", "创业板指"),
                 ("1.000688", "000688.SH", "科创50")]


def real_fetch_indices():
    quotes = _ulist_quotes([s for s, _, _ in _INDEX_SECIDS])
    result = []
    for secid, code, name in _INDEX_SECIDS:
        q = quotes.get(secid.split(".")[1])
        if q:
            result.append({"name": q["name"] or name, "code": code,
                           "price": q["price"], "change": q["change"]})
    if not result:
        raise MarketDataUnavailable("没有可用指数报价")
    return result


# ─── A股: 行业板块 (clist m:90+t:2, f104/f105=上涨/下跌家数) ────

def _fetch_boards():
    url = (f"{CLIST_URL}?pn=1&pz=200&po=1&np=1&fltt=2&invt=2"
           f"&fields=f3,f12,f14,f104,f105&fs=m:90+t:2")
    data = _http_json(url)
    boards = []
    for item in _payload_rows(data, "diff"):
        if not isinstance(item, dict) or not isinstance(item.get("f14"), str) or not item["f14"].strip():
            raise MarketDataUnavailable("板块记录或名称缺失")
        name = item["f14"].strip()
        up = _required_number(item.get("f104"), "up_count", count=True)
        down = _required_number(item.get("f105"), "down_count", count=True)
        change = _required_number(item.get("f3"), "board_change")
        boards.append({"name": name, "change": round(change, 2),
                       "up_count": up, "down_count": down})
    boards.sort(key=lambda x: x["change"], reverse=True)
    return boards


def real_fetch_sectors():
    return _cached("boards", 120, _fetch_boards)[:15]


def real_fetch_sector_flow():
    result = []
    for b in _cached("boards", 120, _fetch_boards):
        total = b["up_count"] + b["down_count"]
        up_ratio = round(b["up_count"] / total * 100, 1) if total else None
        strength = round(b["change"] * 0.6 + up_ratio * 0.04, 1) if total else None
        result.append({"name": b["name"], "change": b["change"],
                       "up_ratio": up_ratio, "stock_count": total,
                       "strength": strength, "hot": strength > 2 if strength is not None else None,
                       **({"status": "unknown", "reason": "no_directional_samples"} if not total else {})})
    result.sort(key=lambda x: (x["strength"] is not None, x["strength"] or 0), reverse=True)
    return result[:15]


# ─── A股: 全市场快照 (分页, 用于情绪统计/股票名录) ──────────────
_MARKET_FS = [("0", ".SZ"), ("1", ".SH"), ("80", ".SZ")]


def _fetch_market_snapshot():
    """全市场代码/名称/最新价/涨跌幅/成交量/换手率 (按涨跌幅排序, 分页+节流)

    失败闭合: 任一页请求异常或响应无效 (data 缺失) 即整体抛
    MarketDataUnavailable — 宁可整体失败, 不把残缺分页/空样本当正常结果;
    仅合法分页结束 (diff 为空或不足一页) 正常返回。失败时不写入
    snapshot/snapshot_long 成功缓存 (_cached 仅在正常返回时写缓存),
    上游恢复后可重新获取。
    """
    rows = []
    seen = set()
    for market, suffix in _MARKET_FS:
        for page in range(1, 5):  # 每市场最多 4 页 × 500
            url = (f"{CLIST_URL}?pn={page}&pz=500&po=1&np=1&fltt=2&invt=2"
                   f"&fields=f2,f3,f5,f8,f12,f14&fs=m:{market}+t:6")
            try:
                data = _http_json(url, timeout=15)
            except Exception as e:
                raise MarketDataUnavailable(
                    f"行情快照分页请求失败 (m:{market} p:{page}): {e}") from e
            diff = _payload_rows(data, "diff", allow_empty=True)
            if not diff:
                break
            for item in diff:
                if not isinstance(item, dict):
                    raise MarketDataUnavailable("行情快照记录不是对象")
                code = str(item.get("f12", ""))
                if not code or code in seen:
                    continue
                price = _price(item.get("f2"))
                if price is None:
                    continue
                change = _num(item.get("f3"), None)
                if change is None:
                    raise MarketDataUnavailable(
                        f"行情快照必要涨跌幅缺失: m:{market} code:{code}；不缩小统计分母")
                volume = _num(item.get("f5"), None)
                turnover = _num(item.get("f8"), None)
                seen.add(code)
                rows.append({"code": code, "ts_code": code + suffix,
                             "name": str(item.get("f14", "")),
                             "price": round(price, 2),
                             "change": round(change, 2),
                             # 手→万手; 量/换缺失 → None 显式未知, 真0保留
                             "volume": round(volume / 1e4, 1) if volume is not None else None,
                             "turnover": round(turnover, 2) if turnover is not None else None})
            if len(diff) < 500:
                break
    if not rows:
        raise MarketDataUnavailable("行情快照未取得任何可用样本")
    return rows


def _snapshot():
    """60s 短缓存快照 (盘中价格新鲜度)"""
    return _cached("snapshot", 60, _fetch_market_snapshot)


def _snapshot_long():
    """6h 长缓存名录快照 (名称基本不变; 与60s快照共享抓取函数但独立TTL,
    避免lookup/K线名称解析额外引发分页风暴)"""
    return _cached("snapshot_long", 6 * 3600, _fetch_market_snapshot)


def real_fetch_hot_stocks():
    return [{"code": r["code"], "name": r["name"], "price": r["price"],
             "change": r["change"], "volume": r["volume"],
             "turnover": r["turnover"]} for r in _snapshot()[:20]]


def real_fetch_limit_up():
    picks = [r for r in _snapshot() if r["change"] >= 9.5][:20]
    result = []
    for i, r in enumerate(picks):
        board_count = 1
        if i < 5:  # 仅前 5 名计算连板 (每只一次K线请求, 缓存5分钟)
            try:
                df = real_get_kline(r["code"], days=20)
                if df is not None and len(df) >= 1:
                    pct = df["close"].pct_change() * 100
                    count = 0
                    for v in reversed(pct.fillna(0).tolist()):
                        if v >= 9.5:
                            count += 1
                        else:
                            break
                    board_count = max(count, 1)
            except Exception:
                pass
        result.append({"code": r["code"], "name": r["name"], "price": r["price"],
                       "change": r["change"], "board_count": board_count})
    return result


def real_fetch_sentiment():
    """基于真实快照的简版情绪统计 (涨跌停家数/涨跌家数/综合分启发式)

    失败闭合: 无有效快照样本或无可用指数时抛 MarketDataUnavailable,
    绝不以缺省值 (up_ratio=50 / idx_avg=0 → "窄幅震荡"约50分) 冒充真实统计。
    有真实样本但恰好零涨跌 (total=0) 属有效样本, 不按无数据处理。
    """
    snap = _snapshot()
    if not snap:
        raise MarketDataUnavailable("情绪统计无有效快照样本")
    up = sum(1 for r in snap if r["change"] > 0)
    down = sum(1 for r in snap if r["change"] < 0)
    limit_up = sum(1 for r in snap if r["change"] >= 9.5)
    limit_down = sum(1 for r in snap if r["change"] <= -9.5)
    idx = _cached("indices", 30, real_fetch_indices)
    if not idx:
        raise MarketDataUnavailable("情绪统计无可用指数数据")
    if any(i.get("change") is None for i in idx):
        raise MarketDataUnavailable("指数涨跌幅缺失, 情绪统计拒绝以缺省0计算")
    idx_avg = sum(i["change"] for i in idx) / len(idx)
    total = up + down
    up_ratio = up / total * 100 if total else 50.0
    score = round(max(0, min(100, up_ratio * 0.7 + (idx_avg + 3) * 5)), 1)
    if idx_avg >= 1:
        phase = "强势上涨"
    elif idx_avg >= 0.2:
        phase = "偏强震荡"
    elif idx_avg > -0.2:
        phase = "窄幅震荡"
    elif idx_avg > -1:
        phase = "偏弱调整"
    else:
        phase = "明显下跌"
    return {"phase": phase, "total_score": score, "up": up, "down": down,
            "limit_up": limit_up, "limit_down": limit_down,
            "sample_size": len(snap),
            "index_trend_score": round((idx_avg + 3) / 6 * 100, 1),
            "index_position_score": None,   # 需历史分位数据, 真实模式暂不提供
            "sentiment_score": round(up_ratio, 1),
            "volume_score": None,           # 需量能基准, 暂不提供
            "position_ratio": "不提供",
            "note": "真实涨跌统计(按涨跌幅排序的活跃股样本, 非全市场) + 启发式评分, "
                    "非原版四维模型, 不构成市场判断"}


def real_get_stock_basic():
    """A股名录 {ts_code: {...}} — 派生自6h长缓存快照 (top-6000活跃股),
    不单独发起全量分页 (避免请求风暴触发WAF); 行业字段东财名录不含, 置空"""
    basic = {}
    for r in _snapshot_long():
        basic[r["ts_code"]] = {
            "ts_code": r["ts_code"], "symbol": r["code"], "name": r["name"],
            "industry": "",
            "market": "沪主板" if r["ts_code"].endswith(".SH") else "深/创",
            "area": ""}
    return basic


# ─── A股/指数: 日K线 (push2his) ───────────────────────────────

def real_get_kline(code, days=60, force=False):
    """日K线 → DataFrame[date,open,high,low,close,volume]"""
    days = int(min(max(days or 60, 1), 365))
    key = f"kline:{code}:{days}"
    if force:
        _cache.pop(key, None)

    def _fetch():
        from datetime import datetime, timedelta
        secid = _secid_cn(code)
        # beg 按自然日 ≈ days×2.2 估算 (含周末/节假日), 避免 beg=0 拉全量历史
        beg = (datetime.now() - timedelta(days=int(days * 2.2) + 10)).strftime("%Y%m%d")
        url = (f"{KLINE_URL}?secid={secid}&klt=101&fqt=0"
               f"&beg={beg}&end=20500101&lmt={days}"
               f"&fields1=f1,f2,f3,f4,f5,f6"
               f"&fields2=f51,f52,f53,f54,f55,f56,f57&ut={_UT}")
        data = _http_json(url, timeout=15)
        klines = _payload_rows(data, "klines")
        rows = []
        for line in klines:
            if not isinstance(line, str):
                raise MarketDataUnavailable("K线记录不是文本")
            p = line.split(",")
            if len(p) < 6:
                raise MarketDataUnavailable("K线记录字段不足")
            try:
                day = datetime.strptime(p[0], "%Y-%m-%d").date().isoformat()
            except ValueError as error:
                raise MarketDataUnavailable("K线日期非法") from error
            if day != p[0] or (rows and day <= rows[-1]["date"]):
                raise MarketDataUnavailable("K线日期格式/顺序/重复无效")
            prices = [_price(value) for value in p[1:5]]
            if any(value is None for value in prices):
                raise MarketDataUnavailable("K线价格缺失或非正数")
            opening, close, high, low = prices
            if high < max(opening, close, low) or low > min(opening, close, high):
                raise MarketDataUnavailable("K线OHLC范围不一致")
            volume = _required_number(p[5], "kline_volume", count=True)
            rows.append({"date": day, "open": opening, "close": close,
                         "high": high, "low": low, "volume": volume})
        rows = rows[-days:]
        return pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
    return _cached(key, 300, _fetch)


def real_get_latest_date():
    """最近交易日 (上证指数最后一根日K日期);
    无法取得真实有效交易日时返回 None (明确未知), 不以机器今日冒充交易日"""
    try:
        df = _cached("kline:1.000001_idx:1", 3600, lambda: real_get_kline("1.000001", days=1))
        if df is not None and len(df):
            return str(df.iloc[-1]["date"])
    except Exception:
        pass
    return None


# ─── 美股: 报价 (push2 市场代码 105=纳斯达克 106=纽交所 107=美交所) ──
_US_SYMBOL_RE = re.compile(r"^[A-Za-z]{1,6}(\.[A-Za-z]{1,2})?$")


def real_us_quote(symbol: str):
    """美股单只报价 → dict 或 {"error": ...}。仅只读行情, 不含任何交易能力"""
    symbol = (symbol or "").strip().upper()
    if not _US_SYMBOL_RE.match(symbol):
        return {"error": "invalid_symbol", "message": "股票代码格式不支持 (1-6位字母)"}
    secids = [f"105.{symbol}", f"106.{symbol}", f"107.{symbol}"]
    try:
        quotes = _cached(f"us:{symbol}", 30, lambda: _ulist_quotes(secids))
    except Exception as e:
        return {"error": "upstream_failed", "message": f"东财美股行情请求失败: {e}"}
    q = quotes.get(symbol)
    if not q:
        return {"error": "not_found", "message": f"未查询到 {symbol} 的美股报价"}
    return {"symbol": symbol, "name": q["name"], "price": q["price"],
            "change_pct": q["change"], "market": "US",
            "currency": "USD", "source": "eastmoney-push2",
            "note": "真实行情(可能延迟), 仅个人本地只读查看, 不构成投资建议"}
