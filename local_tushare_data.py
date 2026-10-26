"""
[本地部署组件] 拾米交易工作室 — tushare 真实只读数据后端 (EOD 日线口径)

real 模式 (SHIMI_DATA_MODE=real) 数据面, 由 local_run.py 接线。函数契约与
local_real_data.py (东方财富备用后端) 完全一致:
  real_fetch_indices / real_fetch_sectors / real_fetch_sector_flow /
  real_fetch_hot_stocks / real_fetch_limit_up / real_fetch_sentiment /
  real_get_stock_basic / real_get_kline / real_get_latest_date / real_us_quote
2026-10-25 扩展 (模块启用):
  real_get_kline_batch (>=30只且<=200日走全市场日线面板(深度规范化150日,本体600s),
  小批量逐只) /
  RealProShim+real_pro (pro_api 安全替代: daily/daily_basic/trade_cal/
  fina_indicator/top_list/moneyflow, 全在 _api 闸门内) /
  real_get_margin_detail (margin_detail, 积分已实测) /
  real_day_cached (按数据日键控的 6h 重计算缓存, 单飞)

纪律:
  - token 仅从环境变量 TUSHARE_TOKEN 读取, 绝不落盘/打印; 未配置时各函数
    明确报错 ("未配置 TUSHARE_TOKEN"), 不静默降级、不以模糊 500 兜底
  - 传输验收: API_URL 已于 2026-10-24 实测切换 HTTPS (本机代理路径下
    https://api.tushare.pro 真实调用 trade_cal/daily/us_daily 全部成功,
    令牌不再明文传输); 仍为唯一数据主机, 不绕过证书/代理管控
  - 用途闸门: _api 在读令牌前要求 SHIMI_MARKET_USE=personal_view
    (操作者显式确认个人非商业本地只读); commercial/未确认一律拒绝
  - 全局限流: 任意两次 API 调用间隔 >= 1s (线程锁串行); us_daily 官方限
    1 次/小时 → 成功结果缓存 65 分钟, 失败结果短缓存 10 分钟防反复撞击
  - 缓存: 全市场日线/每日指标按交易日 6h (历史日不可变; 空结果 10min);
    股票名录 6h; 指数行情 15min; 指数历史 1h; K线 5min (面板批量路径写入
    30min, EOD日内不变); 数据日期解析 30min
  - EOD 口径: 盘中"最新"= 最近一个有日线数据的交易日收盘; 今日数据发布前
    自动回退上一开市日 (trade_cal 驱动), 响应 note 中注明
"""
import json
import math
import os
import re
import threading
import time
import urllib.request
from urllib.parse import urlparse
from datetime import datetime, timezone

import pandas as pd

API_URL = "https://api.tushare.pro"  # 2026-10-24 实测: HTTPS 直连可用 (token 不再明文传输)

# 2026-10-23已读官方数据服务协议（二）5：个人查看/非商业许可。
# 接口可用、拥有积分或HTTPS均不等于商业使用许可；未确认用途不得读令牌。
# 2026-10-24: 本人(token所有者)确认用途=个人本机非商业只读查看,
# 经用户环境变量 SHIMI_MARKET_USE=personal_view 显式声明后接入。
SOURCE_USAGE_POLICY = {
    'provider': 'Tushare Pro',
    'terms_url': 'https://tushare.pro/document/1?doc_id=405',
    'checked_on': '2026-10-23',
    'published_scope': 'personal_noncommercial_view',
    'commercial_permission': 'not_approved',
    'actual_user_purpose': 'personal_noncommercial_view',
    'purpose_confirmed_on': '2026-10-24',
}

_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_api_lock = threading.Lock()
_last_call = [0.0]
_cache = {}


class TushareError(RuntimeError):
    """tushare 调用失败 (未配置 token / 频率超限 / 积分不足 / 传输错误)"""


class MarketDataUnavailable(RuntimeError):
    """数据暂不可用 (非调用错误, 如近期开市日均无日线数据)"""


def _pace(min_interval=1.0):
    dt = time.time() - _last_call[0]
    if dt < min_interval:
        time.sleep(min_interval - dt)
    _last_call[0] = time.time()


def _cached(key, ttl, fn, empty_ttl=None):
    """进程内存缓存; 空结果 (None/空集合) 可用 empty_ttl 缩短有效期"""
    ent = _cache.get(key)
    now = time.time()
    if ent and now - ent[0] < ent[1]:
        return ent[2]
    val = fn()
    empty = val is None or (hasattr(val, "__len__") and len(val) == 0)
    _cache[key] = (now, (empty_ttl or ttl) if empty else ttl, val)
    return val


def _api(api_name, params=None, fields=""):
    """tushare HTTP API 单点入口 → (fields, items); 失败一律抛 TushareError"""
    # 闸门1 传输: 仅 HTTPS (2026-10-24 已实测验收); 若被改回明文HTTP,
    # 在读取令牌之前拒绝 — 不绕过证书/代理管控
    if urlparse(API_URL).scheme.lower() != "https":
        raise TushareError("transport_not_verified: 仅允许HTTPS；明文连接不会读取或发送令牌")
    # 闸门2 用途: 需操作者显式声明确认个人非商业用途后才读取令牌
    purpose = os.environ.get('SHIMI_MARKET_USE', 'unconfirmed')
    if purpose == 'commercial':
        raise TushareError('commercial_permission_not_approved: 官方现行许可限个人非商业查看；不能作为商业产品数据授权')
    if purpose != 'personal_view':
        raise TushareError('purpose_unconfirmed: 需本人明确数据用途与许可后才能读取令牌；不自动选择个人用途规避商业限制')
    token = os.environ.get("TUSHARE_TOKEN", "").strip()
    if not token:
        raise TushareError("未配置 TUSHARE_TOKEN — 请设置本机用户环境变量后重启服务")
    body = json.dumps({"api_name": api_name, "token": token,
                       "params": params or {}, "fields": fields or ""}).encode()
    req = urllib.request.Request(API_URL, data=body,
                                 headers={"Content-Type": "application/json"})
    with _api_lock:  # 串行 + 节流: 防并发突发触发频率限制 (us_daily 1次/小时)
        last_err = None
        # 本机代理路径观测到瞬断/慢读 (WinError 10054 / IncompleteRead / 全市场日线
        # MB级响应读超时): 读超时60s + 网络级重试1次, 仍失败则显式抛错不补位;
        # us_daily 官方限1次/小时不重试防翻倍计数
        for _ in ((1,) if api_name == "us_daily" else (1, 2)):
            _pace(1.0)
            try:
                with _opener.open(req, timeout=60) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                last_err = None
                break
            except Exception as e:
                last_err = e
        if last_err is not None:
            raise TushareError(f"tushare {api_name} 请求失败: {last_err}") from last_err
    if not isinstance(payload, dict) or payload.get("code") != 0:
        message = str(payload.get("msg", "响应格式错误")) if isinstance(payload, dict) else "响应格式错误"
        raise TushareError(f"tushare {api_name} 拒绝: {message.replace(token, '[redacted]')[:100]}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise TushareError(f"tushare {api_name} 缺少数据结构")
    fields, items = data.get("fields"), data.get("items")
    _rows(fields, items)
    return fields, items


def _rows(fields, items):
    if (not isinstance(fields, list) or not all(isinstance(f, str) and f for f in fields)
            or len(fields) != len(set(fields)) or not isinstance(items, list)):
        raise MarketDataUnavailable("来源字段/记录结构无效")
    if any(not isinstance(it, (list, tuple)) or len(it) != len(fields) for it in items):
        raise MarketDataUnavailable("来源记录与字段数量不一致，不能截断拼接")
    return [dict(zip(fields, it)) for it in items]


def _number(value, field, *, positive=False, nonnegative=False, optional=False):
    """Never turn missing/NaN/bool into real zero in the active EOD backend."""
    try:
        if isinstance(value, bool) or type(value).__name__ == "bool_":
            raise ValueError("boolean")
        number = float(value)
        if (not math.isfinite(number) or (positive and number <= 0)
                or (nonnegative and number < 0)):
            raise ValueError("numeric range")
    except (TypeError, ValueError, OverflowError):
        if optional:
            return None
        raise MarketDataUnavailable(f"必要数值缺失或非法: {field}") from None
    return number


def _validate_daily(frame, trade_date):
    required = {"ts_code", "trade_date", "close", "pct_chg"}
    if not required.issubset(frame.columns):
        raise MarketDataUnavailable("日线缺少代码/日期/收盘/涨跌幅")
    if frame["ts_code"].duplicated().any():
        raise MarketDataUnavailable("日线代码重复，不能重复计入分母")
    for _, row in frame.iterrows():
        if not isinstance(row["ts_code"], str) or not row["ts_code"].strip():
            raise MarketDataUnavailable("日线代码缺失")
        if _fmt_date(row["trade_date"]) != _fmt_date(trade_date):
            raise MarketDataUnavailable("日线日期与请求日期不符")
    for field in ("close", "pct_chg"):
        frame[field] = [_number(value, field, positive=field == "close")
                        for value in frame[field]]
    return frame


def _fmt_date(d):
    """"20261022" → "2026-10-22" """
    d = str(d or "")
    try:
        if not re.fullmatch(r"\d{8}", d):
            raise ValueError("format")
        return datetime.strptime(d, "%Y%m%d").date().isoformat()
    except ValueError:
        raise MarketDataUnavailable("来源交易日期缺失或非法") from None


def _strip_suffix(ts_code):
    return str(ts_code).replace(".SZ", "").replace(".SH", "").replace(".BJ", "")


# ─── 交易日历 / 全市场快照 / 数据日期解析 ─────────────────────

def _open_days(n=10):
    """最近 n 个开市日 (YYYYMMDD 降序, 含今日若开市); trade_cal 缓存6h"""
    def _fetch():
        today = time.strftime("%Y%m%d")
        start = time.strftime("%Y%m%d", time.localtime(time.time() - 45 * 86400))
        _, items = _api("trade_cal", {"exchange": "SSE", "start_date": start,
                                      "end_date": today}, "cal_date,is_open")
        days = sorted((r[0] for r in items if r[1] == 1 and r[0] <= today),
                      reverse=True)
        if not days:
            raise MarketDataUnavailable("trade_cal 近期无开市日")
        return days
    return _cached("trade_cal", 21600, _fetch)[:n]


def _daily_snapshot(trade_date):
    """全市场单日日线 DataFrame; 该日无数据(盘中未发布) → None.
    历史日缓存6h (不可变), 空结果 10min.
    仅当 API 实际回传且字段验证通过时, 在 DataFrame.attrs 记录
    retrieved_at_utc (UTC 明确时区, ISO8601); 缓存命中沿用原始取数时点,
    不用 HTTP 响应/本轮计算时间刷新; 失败/空结果不产生任何取数时点"""
    def _fetch():
        fields, items = _api("daily", {"trade_date": trade_date},
                             "ts_code,trade_date,open,high,low,close,"
                             "pre_close,pct_chg,vol,amount")
        if not items:
            return None
        df = _validate_daily(pd.DataFrame(_rows(fields, items)), trade_date)
        df.attrs["retrieved_at_utc"] = datetime.now(timezone.utc).isoformat()
        return df
    return _cached(f"daily:{trade_date}", 21600, _fetch, empty_ttl=600)


def _daily_basic_snapshot(trade_date):
    """每日指标 (换手率等) 单日 DataFrame; 无数据 → None. 缓存同 _daily_snapshot"""
    def _fetch():
        fields, items = _api("daily_basic", {"trade_date": trade_date},
                             "ts_code,trade_date,turnover_rate,volume_ratio,"
                             "pe,pb,total_mv,circ_mv")
        if not items:
            return None
        return pd.DataFrame(_rows(fields, items))
    return _cached(f"daily_basic:{trade_date}", 21600, _fetch, empty_ttl=600)


def _data_date():
    """最近一个有日线数据的交易日 (EOD 口径核心: 今日未发布则回退) — 解析缓存30min"""
    def _resolve():
        for d in _open_days(8):
            df = _daily_snapshot(d)
            if df is not None and len(df):
                return d
        raise MarketDataUnavailable("最近8个开市日均无日线数据")
    return _cached("data_date", 1800, _resolve)


# ─── 指数行情 / 指数历史 ─────────────────────────────────────

INDEX_MAP = {  # 与原 data/fetcher_indices.py 一致
    "000001.SH": "上证指数",
    "399001.SZ": "深证成指",
    "399006.SZ": "创业板指",
    "000688.SH": "科创50",
}


def real_fetch_indices():
    """四大指数最近收盘 → [{name,code,price,change,high,low}] (EOD, 盘中=最近收盘)"""
    def _fetch():
        start = time.strftime("%Y%m%d", time.localtime(time.time() - 15 * 86400))
        end = time.strftime("%Y%m%d")
        out = []
        for code, name in INDEX_MAP.items():
            fields, items = _api("index_daily",
                                 {"ts_code": code, "start_date": start, "end_date": end},
                                 "ts_code,trade_date,close,pct_chg,high,low,vol,amount")
            if not items:
                continue
            last = max(_rows(fields, items), key=lambda r: r["trade_date"])
            out.append({"name": name, "code": code,
                        "price": round(_number(last.get("close"), "index_close", positive=True), 2),
                        "change": round(_number(last.get("pct_chg"), "index_change"), 2),
                        "high": round(_number(last.get("high"), "index_high", positive=True), 2),
                        "low": round(_number(last.get("low"), "index_low", positive=True), 2),
                        "trade_date": _fmt_date(last["trade_date"]), "source": "tushare-index_daily",
                        "note": "日线收盘值(EOD)，不是实时行情；来源/许可待独立核验"})
        if not out:
            raise MarketDataUnavailable("index_daily 无数据")
        return out
    return _cached("indices", 900, _fetch)


def _index_hist(ts_code, days=150):
    """指数近 days 个自然日日线 DataFrame (升序, 预计算 ma5/10/20/60) — 缓存1h"""
    def _fetch():
        start = time.strftime("%Y%m%d", time.localtime(time.time() - days * 86400))
        end = time.strftime("%Y%m%d")
        fields, items = _api("index_daily",
                             {"ts_code": ts_code, "start_date": start, "end_date": end},
                             "trade_date,close,vol,amount")
        if not items:
            raise MarketDataUnavailable(f"指数历史无数据: {ts_code}")
        df = pd.DataFrame(_rows(fields, items)).sort_values("trade_date") \
            .reset_index(drop=True)
        for value in df["trade_date"]:
            _fmt_date(value)
        if df["trade_date"].duplicated().any():
            raise MarketDataUnavailable("指数历史交易日重复")
        for field in ("close", "amount"):
            if field not in df:
                raise MarketDataUnavailable(f"指数历史缺少{field}")
            df[field] = [_number(value, field, positive=field == "close", nonnegative=field == "amount")
                         for value in df[field]]
        for w in (5, 10, 20, 60):
            df[f"ma{w}"] = df["close"].rolling(w).mean()
        return df
    return _cached(f"idx_hist:{ts_code}", 3600, _fetch)


# ─── 行业板块 (复刻 data/fetcher_indices.py 原逻辑) ─────────────

def _eod_meta(data_date, daily, data_kind):
    """板块输出的来源/时点元数据: trade_date 来自实际所用的数据日
    (_data_date 解析结果, 绝不是机器今日); retrieved_at_utc 来自
    _daily_snapshot.attrs (无可核取数时点的既有/合成快照 → None, 不猜时间)"""
    return {"trade_date": _fmt_date(data_date),
            "source": "tushare-daily-eod",
            "data_kind": data_kind,
            "retrieved_at_utc": daily.attrs.get("retrieved_at_utc"),
            "source_verification": "not_verified"}


def real_fetch_sectors():
    """行业板块排行 — 成分股平均涨跌幅 TOP 15 (EOD日线统计, 非实时资金流)"""
    d = _data_date()  # 一次解析并保留: 元数据 trade_date 即此实际数据日
    daily = _daily_snapshot(d)
    basic = real_get_stock_basic()
    meta = _eod_meta(d, daily, "EOD日线行业统计(成分股平均涨跌幅)")
    merged = daily.copy()
    merged["industry"] = merged["ts_code"].map(
        lambda c: basic.get(c, {}).get("industry") or "未知")
    groups = merged[merged["industry"] != "未知"].groupby("industry")
    result = []
    for ind, grp in groups:
        avg_chg = grp["pct_chg"].mean()
        up = len(grp[grp["pct_chg"] > 0])
        result.append({"name": ind, "change": round(float(avg_chg), 2),
                       "up_count": int(up), "down_count": int(len(grp[grp["pct_chg"] < 0])),
                       "flat_count": int(len(grp[grp["pct_chg"] == 0])), **meta})
    if not result:
        raise MarketDataUnavailable("日线没有可分类的行业样本")
    result.sort(key=lambda x: x["change"], reverse=True)
    return result[:15]


def real_fetch_sector_flow():
    """板块资金流分析 — 行业涨跌幅 + 上涨占比 + 强度评分 (原公式;
    基于EOD日线的派生指标, 非实时资金流)"""
    d = _data_date()  # 一次解析并保留: 元数据 trade_date 即此实际数据日
    daily = _daily_snapshot(d)
    basic = real_get_stock_basic()
    meta = _eod_meta(d, daily, "EOD日线派生指标(强度评分), 非实时资金流")
    df = daily.copy()
    df["industry"] = df["ts_code"].map(
        lambda c: basic.get(c, {}).get("industry") or "未知")
    df = df[df["industry"] != "未知"]
    result = []
    for ind, grp in df.groupby("industry"):
        avg_chg = grp["pct_chg"].mean()
        up_ratio = len(grp[grp["pct_chg"] > 0]) / max(len(grp), 1) * 100
        strength = round(float(avg_chg) * 0.6 + up_ratio * 0.04, 1)
        result.append({"name": ind, "change": round(float(avg_chg), 2),
                       "up_ratio": round(up_ratio, 1), "stock_count": int(len(grp)),
                       "strength": strength, "hot": strength > 2, **meta})
    if not result:
        raise MarketDataUnavailable("日线没有可分类的行业样本")
    result.sort(key=lambda x: x["strength"], reverse=True)
    return result[:15]


# ─── 热门个股 / 涨停板 (复刻 data/fetcher_sentiment.py 原逻辑) ────

def real_fetch_hot_stocks():
    """热门个股涨幅 TOP 20 (turnover=换手率%, volume=成交额亿元)"""
    d = _data_date()
    daily = _daily_snapshot(d)
    basic = real_get_stock_basic()
    db = _daily_basic_snapshot(d)
    turnover_map = {}
    if db is not None and len(db):
        if not {"ts_code", "turnover_rate"}.issubset(db.columns) or db["ts_code"].duplicated().any():
            raise MarketDataUnavailable("每日指标结构不完整或代码重复")
        turnover_map = db.set_index("ts_code")["turnover_rate"].to_dict()
    df = daily.copy()
    df["name"] = df["ts_code"].map(lambda c: basic.get(c, {}).get("name", ""))
    df["turnover"] = df["ts_code"].map(lambda c: turnover_map.get(c))
    top = df.sort_values("pct_chg", ascending=False).head(20)
    def optional_round(value, field, divisor=1, precision=1):
        number = _number(value, field, nonnegative=True, optional=True)
        return round(number / divisor, precision) if number is not None else None
    return [{"code": _strip_suffix(row["ts_code"]), "name": row["name"],
             "price": round(_number(row.get("close"), "close", positive=True), 2),
             "change": round(_number(row.get("pct_chg"), "pct_chg"), 2),
             "volume": optional_round(row.get("amount"), "amount", divisor=1e5),
             "turnover": optional_round(row.get("turnover"), "turnover"),
             "trade_date": _fmt_date(d), "source": "tushare-daily-eod"}
            for _, row in top.iterrows()]


def real_fetch_limit_up():
    """涨停板 (pct_chg >= 9.5) TOP 20 + 连板数.
    连板由多日全市场快照回溯统计 (最多回溯5个开市日, 惰性: 仅当候选仍有连板
    时才拉取更早一日, 快照全部走6h缓存)"""
    d0 = _data_date()
    days = [d for d in _open_days(8) if d <= d0][:6]
    snap0 = _daily_snapshot(d0)
    basic = real_get_stock_basic()
    df = snap0[snap0["pct_chg"] >= 9.5].copy()
    df["name"] = df["ts_code"].map(lambda c: basic.get(c, {}).get("name", ""))
    boards = {ts: 1 for ts in df["ts_code"]}
    active = set(boards)
    if not days or days[0] != d0:
        raise MarketDataUnavailable("连板统计缺少对应交易日历")
    for d in days[1:]:
        if not active:
            break
        snap = _daily_snapshot(d)
        if snap is None or not len(snap):
            for ts in active:
                boards[ts] = None
            break
        prev = dict(zip(snap["ts_code"], snap["pct_chg"]))
        for ts in list(active):
            v = prev.get(ts)
            if v is None or pd.isna(v):
                boards[ts] = None
                active.remove(ts)
            elif v >= 9.5:
                boards[ts] += 1
            else:
                active.remove(ts)
    df["board_count"] = df["ts_code"].map(boards)
    df = df.sort_values(["board_count", "pct_chg"], ascending=False).head(20)
    return [{"code": _strip_suffix(row["ts_code"]), "name": row["name"],
             "price": round(float(row["close"]), 2),
             "change": round(float(row["pct_chg"]), 2),
             "board_count": int(row["board_count"]) if pd.notna(row["board_count"]) else None,
             "board_count_lower_bound": row["ts_code"] in active and pd.notna(row["board_count"]),
             "note": "9.5%阈值筛选样例，不代表适用于所有股票的正式涨停规则"}
            for _, row in df.iterrows()]


# ─── 市场情绪 (复刻 data/fetcher_sentiment.py 原版四维模型) ──────

def real_fetch_sentiment():
    """市场状态分析 — 4 维综合评分 (趋势40%+位置20%+情绪20%+量能20%),
    数据=tushare 全市场日线 + 三指数 150 日历史 (EOD)"""
    d0 = _data_date()
    idx_codes = {"上证指数": "000001.SH", "深证成指": "399001.SZ",
                 "创业板指": "399006.SZ"}
    idx_weights = {"上证指数": 0.40, "深证成指": 0.35, "创业板指": 0.25}
    index_data = {}
    for name, code in idx_codes.items():
        history = _index_hist(code)
        if history is None or not {"trade_date", "close", "amount"}.issubset(history.columns):
            raise MarketDataUnavailable("指数历史字段缺失")
        history = history[history["trade_date"] <= d0].copy()
        if history.empty or history["trade_date"].iloc[-1] != d0:
            raise MarketDataUnavailable("指数与个股日线时点不一致")
        index_data[name] = history

    def calc_index_score(df):
        if df is None or len(df) < 60:
            raise MarketDataUnavailable("指数历史不足60条，不能用0分代替缺失")
        last = df.iloc[-1]
        ma5, ma10, ma20, ma60 = last["ma5"], last["ma10"], last["ma20"], last["ma60"]
        close = last["close"]
        align = 0
        if ma5 > ma10:
            align += 12.5
        if ma10 > ma20:
            align += 12.5
        if ma20 > ma60:
            align += 12.5
        if ma5 > ma60:
            align += 12.5
        slope_score = 12.5
        if len(df) >= 25:
            slope = (ma20 - df["ma20"].iloc[-5]) / df["ma20"].iloc[-20] * 100
            slope_score = min(25, max(0, slope * 10))
        above_ma20 = close > ma20
        above_ma60 = close > ma60
        if above_ma20 and above_ma60:
            dist = (close - ma20) / ma20 * 100
            pos_score = 15 if dist > 15 else 25
        elif above_ma20:
            pos_score = 15
        elif above_ma60:
            pos_score = 10
        else:
            pos_score = 5 if (ma60 - close) / ma60 * 100 < 5 else 0
        return {"trend": round(align + slope_score + pos_score, 1),
                "position": pos_score}

    trend_total = 0
    pos_total = 0
    for name, w in idx_weights.items():
        s = calc_index_score(index_data[name])
        trend_total += s["trend"] * w
        pos_total += s["position"] * w

    daily = _daily_snapshot(d0)
    limit_up_count = len(daily[daily["pct_chg"] >= 9.5])
    limit_down_count = len(daily[daily["pct_chg"] <= -9.5])

    if limit_down_count == 0:
        ratio = 10 + limit_up_count
    else:
        ratio = limit_up_count / limit_down_count

    if ratio >= 5:
        sentiment_score = 90
    elif ratio >= 3:
        sentiment_score = 75
    elif ratio >= 1.5:
        sentiment_score = 60
    elif ratio >= 0.8:
        sentiment_score = 45
    elif ratio >= 0.3:
        sentiment_score = 25
    else:
        sentiment_score = 10

    vol_ratio = 0
    for name, w in idx_weights.items():
        df = index_data.get(name)
        if df is not None and len(df) >= 25:
            cur = df["amount"].iloc[-1]
            avg = df["amount"].tail(20).mean()
            if cur > 0 and avg > 0:
                vol_ratio += (cur / avg) * w
            elif cur == 0 and avg > 0:
                vol_ratio += 0  # 实际零成交额不同于缺失
            else:
                raise MarketDataUnavailable("指数成交额不足以核验量能分母")

    if vol_ratio >= 2.0:
        volume_score = 100
    elif vol_ratio >= 1.5:
        volume_score = 85
    elif vol_ratio >= 1.2:
        volume_score = 70
    elif vol_ratio >= 0.8:
        volume_score = 50
    elif vol_ratio >= 0.5:
        volume_score = 30
    else:
        volume_score = 10

    total_score = (trend_total * 0.40 + pos_total * 0.20 +
                   sentiment_score * 0.20 + volume_score * 0.20)

    MARKET_STATES = [
        (85, "强势牛市🚀", "指数多头排列，量价齐升", (80, 100)),
        (65, "牛市📈", "指数趋势向上，市场活跃", (65, 80)),
        (45, "震荡偏多↗️", "指数震荡上行，精选个股", (45, 65)),
        (25, "震荡市➡️", "指数横盘整理，控制仓位", (25, 45)),
        (10, "震荡偏空↘️", "重心下移，轻仓防守", (10, 25)),
        (0, "熊市📉", "空头排列，空仓为主", (0, 10)),
    ]
    state = "危机模式⚠️"
    state_desc = "市场恐慌，空仓观望"
    pos_range = (0, 5)
    for threshold, s_name, s_desc, s_range in MARKET_STATES:
        if total_score >= threshold:
            state = s_name
            state_desc = s_desc
            pos_range = s_range
            break
    suggested_pos = round((pos_range[0] + pos_range[1]) / 2, 1)

    total_stocks = len(daily)
    up = len(daily[daily["pct_chg"] > 0])
    down = len(daily[daily["pct_chg"] < 0])

    return {
        "total_score": round(total_score, 1),
        "phase": state, "description": state_desc,
        "position_ratio": suggested_pos,
        "position_range": f"{pos_range[0]}%-{pos_range[1]}%",
        "index_trend_score": round(trend_total, 1),
        "index_position_score": round(pos_total, 1),
        "sentiment_score": sentiment_score,
        "volume_score": volume_score,
        "volume_ratio": round(vol_ratio, 2),
        "total": total_stocks, "sample_size": total_stocks,
        "up": up, "down": down,
        "limit_up": limit_up_count, "limit_down": limit_down_count,
        "zt_ratio": round(ratio, 2),
        "date": _fmt_date(d0),
        "source": "tushare-eod 复刻四维模型",
        "formula": "4维综合: 趋势40%+位置20%+情绪20%+量能20%",
        "note": "Tushare 日线 EOD (盘中=最近收盘); 原版四维评分模型本地复刻, 不构成投资建议",
    }


# ─── A股名录 ─────────────────────────────────────────────────

def real_get_stock_basic():
    """A股名录 {ts_code: {ts_code,symbol,name,industry,market,area}} — 缓存6h"""
    def _fetch():
        fields, items = _api("stock_basic", {"list_status": "L"},
                             "ts_code,symbol,name,industry,market,area")
        basic = {}
        for r in _rows(fields, items):
            basic[r["ts_code"]] = {
                "ts_code": r["ts_code"], "symbol": r.get("symbol", ""),
                "name": r.get("name", ""), "industry": r.get("industry") or "",
                "market": r.get("market") or "", "area": r.get("area") or ""}
        if not basic:
            raise MarketDataUnavailable("stock_basic 返回空")
        return basic
    return _cached("stock_basic", 21600, _fetch)


# ─── A股: 日K线 ──────────────────────────────────────────────

def _ts_code_cn(code):
    """600519 → 600519.SH (与 api/market.py 名称查询同一映射:
    0/3→SZ, 9→BJ, 其余→SH); 已带后缀则规范化"""
    code = str(code or "").strip().upper()
    if "." in code:
        sym, _, sfx = code.partition(".")
        return f"{sym}.{sfx}"
    sym = re.sub(r"\D", "", code)
    if len(sym) != 6:
        raise MarketDataUnavailable(f"股票代码格式不支持: {code}")
    if sym.startswith(("0", "3")):
        return sym + ".SZ"
    if sym.startswith("9"):
        return sym + ".BJ"
    return sym + ".SH"


def real_get_kline(code, days=60, force=False):
    """日K线 → DataFrame[date,open,high,low,close,volume,pct_chg] 升序最近 days 根 (EOD)
    pct_chg 取自 daily 原始字段; 个别记录缺失时由收盘价序列推导 (首根无前收保持 NaN)。
    同代码更长序列在缓存内未过期时直接切片应答; 共享全市场面板 (kline_panel:*) 温热时
    亦可切片应答 (同源同数据日, 均不再发请求)。
    days 上限 1300 (约5年, 个股雷达长周期分位需要); HTTP 端点仍受 _KLINE_RE 1-200 限制"""
    days = int(min(max(days or 60, 1), 1300))
    ts_code = _ts_code_cn(code)
    key = f"kline:{ts_code}:{days}"
    if force:
        _cache.pop(key, None)
    else:
        now = time.time()
        prefix = f"kline:{ts_code}:"
        for k, ent in list(_cache.items()):
            if not k.startswith(prefix) or k == key:
                continue
            try:
                n0 = int(k[len(prefix):])
            except ValueError:
                continue
            df0 = ent[2]
            remain = ent[1] - (now - ent[0])
            if n0 >= days and remain > 0 and df0 is not None and len(df0) >= days:
                df = df0.iloc[-days:].reset_index(drop=True)
                _cache[key] = (now, min(300, remain), df)
                return df
        # 共享面板切片: 批量路径构建的全市场面板覆盖未单独缓存的代码
        # (形态扫描 detect 的 ma_convergence_score 等按120日取数即依赖此命中)
        need = max(int(days * 0.4), 10)
        for k, ent in list(_cache.items()):
            if not k.startswith("kline_panel:"):
                continue
            try:
                n0 = int(k[len("kline_panel:"):])
            except ValueError:
                continue
            frames = ent[2]
            remain = ent[1] - (now - ent[0])
            if n0 < days or remain <= 0 or frames is None:
                continue
            f = frames.get(ts_code)
            if f is not None and len(f) >= need:
                df = f.iloc[-days:].reset_index(drop=True) if len(f) > days else f
                _cache[key] = (now, min(300, remain), df)
                return df

    def _fetch():
        start = time.strftime(
            "%Y%m%d", time.localtime(time.time() - (int(days * 2.2) + 15) * 86400))
        end = time.strftime("%Y%m%d")
        fields, items = _api("daily",
                             {"ts_code": ts_code, "start_date": start, "end_date": end},
                             "trade_date,open,high,low,close,pct_chg,vol")
        rows = sorted(_rows(fields, items), key=lambda r: r["trade_date"])[-days:]
        if not rows:
            raise MarketDataUnavailable("K线未取得记录")
        result = []
        seen = set()
        for row in rows:
            day = _fmt_date(row.get("trade_date"))
            if day in seen:
                raise MarketDataUnavailable("K线交易日重复")
            seen.add(day)
            values = {field: _number(row.get(field), field, positive=True)
                      for field in ("open", "high", "low", "close")}
            if (values["high"] < max(values["open"], values["close"], values["low"])
                    or values["low"] > min(values["open"], values["close"], values["high"])):
                raise MarketDataUnavailable("K线OHLC范围不一致")
            result.append({"date": day, **values,
                           "volume": _number(row.get("vol"), "volume", nonnegative=True),
                           "pct_chg": _number(row.get("pct_chg"), "pct_chg", optional=True)})
        df = pd.DataFrame(
            result,
            columns=["date", "open", "high", "low", "close", "volume", "pct_chg"])
        df["pct_chg"] = df["pct_chg"].fillna(df["close"].pct_change() * 100)
        return df
    return _cached(key, 300, _fetch)


def real_get_latest_date():
    """最近有实际日线的日期，不拿日历开市日冒充已发布数据日期。"""
    try:
        return _fmt_date(_data_date())
    except Exception:
        return None


# ─── 美股: us_daily 日线报价 (EOD; 官方限 1 次/小时) ─────────────

_US_SYMBOL_RE = re.compile(r"^[A-Za-z]{1,6}(\.[A-Za-z])?$")


def _us_last(symbol):
    """us_daily 最近一行 (dict) 或 None"""
    fields, items = _api("us_daily", {
        "ts_code": symbol,
        "start_date": time.strftime("%Y%m%d", time.localtime(time.time() - 20 * 86400)),
        "end_date": time.strftime("%Y%m%d")}, "")
    rows = _rows(fields, items)
    if not rows:
        return None
    return max(rows, key=lambda r: r.get("trade_date", ""))


def _us_name(symbol):
    """us_basic 中文名/英文名; 失败回退 symbol 本身 — 缓存6h"""
    def _fetch():
        fields, items = _api("us_basic", {"ts_code": symbol}, "ts_code,name,enname")
        rows = _rows(fields, items)
        if not rows:
            return symbol
        return rows[0].get("name") or rows[0].get("enname") or symbol
    try:
        return _cached(f"us_name:{symbol}", 21600, _fetch, empty_ttl=3600)
    except TushareError:
        return symbol


def real_us_quote(symbol):
    """美股最近收盘报价 → dict 或 {"error": ...}。仅只读行情, 不含任何交易能力.
    us_daily 官方限 1 次/小时: 成功缓存 65min, 失败/无数据短缓存 10min 防撞击"""
    symbol = (symbol or "").strip().upper()
    if not _US_SYMBOL_RE.match(symbol):
        return {"error": "invalid_symbol",
                "message": "股票代码格式不支持 (1-6位字母, 可带一个点)"}
    key = f"usq:{symbol}"

    def _fetch():
        row = _us_last(symbol)
        if not row or row.get("close") is None:
            return None
        close = _number(row["close"], "us_close", positive=True)
        pre = row.get("pre_close")
        pct = row.get("pct_change")
        if pct is None and pre is not None:
            previous = _number(pre, "us_pre_close", positive=True)
            pct = (close - previous) / previous * 100
        pct = _number(pct, "us_change", optional=True)
        return {"symbol": symbol, "name": _us_name(symbol),
                "price": round(close, 2),
                "change_pct": round(float(pct), 2) if pct is not None else None,
                "trade_date": _fmt_date(row.get("trade_date", "")),
                "market": "US", "currency": "USD", "source": "tushare-us_daily",
                "note": "Tushare us_daily 美股日线(EOD, 盘中=最近收盘); "
                        "该接口限1次/小时, 结果缓存65分钟; "
                        "仅个人本地只读查看, 不构成投资建议"}
    try:
        q = _cached(key, 3900, _fetch, empty_ttl=600)
    except (TushareError, MarketDataUnavailable) as e:
        q = {"error": "upstream_failed", "symbol": symbol,
             "message": f"tushare us_daily 不可用: {e}"}
        _cache[key] = (time.time(), 600, q)  # 失败短缓存, 防限流窗口内反复撞击
    if not q:
        return {"error": "not_found", "symbol": symbol,
                "message": f"未查询到 {symbol} 的美股日线(EOD)"}
    return q


# ─── 批量K线 (全市场日线面板 / 逐只) ─────────────────────────

def _open_days_range(n):
    """最近 n 个开市日 (YYYYMMDD 降序); 日历窗口随 n 自适应, 6h 缓存"""
    n = int(min(max(n or 10, 1), 400))

    def _fetch():
        today = time.strftime("%Y%m%d")
        start = time.strftime("%Y%m%d", time.localtime(time.time() - (int(n * 1.7) + 20) * 86400))
        _, items = _api("trade_cal", {"exchange": "SSE", "start_date": start,
                                      "end_date": today}, "cal_date,is_open")
        days = sorted((r[0] for r in items if r[1] == 1 and r[0] <= today),
                      reverse=True)
        if not days:
            raise MarketDataUnavailable("trade_cal 近期无开市日")
        return days
    return _cached(f"trade_cal_range:{n}", 21600, _fetch)[:n]


def _kline_panel_frames(days):
    """最近 days 个已发布开市日的全市场日线面板 → {ts_code: kline契约DataFrame}
    每交易日快照走 _daily_snapshot (6h缓存, 跨模块共享); 无数据日跳过(不补位)"""
    d0 = _data_date()
    dates = [d for d in _open_days_range(days + 5) if d <= d0][:days]
    if not dates:
        raise MarketDataUnavailable("面板区间内无已发布交易日")
    series = {}
    for d in sorted(dates):
        snap = _daily_snapshot(d)
        if snap is None or not len(snap):
            continue
        day = _fmt_date(d)
        for row in snap.itertuples(index=False):
            series.setdefault(row.ts_code, []).append(
                (day, row.open, row.high, row.low, row.close, row.vol, row.pct_chg))
    frames = {}
    for ts_code, rows in series.items():
        frames[ts_code] = pd.DataFrame(
            rows, columns=["date", "open", "high", "low", "close", "volume", "pct_chg"])
    return frames


def real_get_kline_batch(codes, days=120):
    """批量K线 → {输入代码: DataFrame|None} (realtime_scorer.get_kline_batch 契约)
    面板路径 (>=30只且<=200日): 全市场日线面板 — 每交易日快照1次调用且6h缓存跨模块共享;
      面板深度规范化为150日 (策略120/形态60/翻倍D11 150 共用同一面板与快照缓存),
      个股结果按需切片到请求天数; 面板本体600s缓存(分批调用不重复构建),
      切片结果同时写入单只K线缓存 (1800s, 供后续 get_kline 命中; EOD日内不变);
    小批量 (<30只) 或超长 (>200日): 逐只 real_get_kline (全局1s节流串行)"""
    days = int(min(max(days or 120, 1), 365))
    out = {}
    codes = [str(c) for c in codes if c]
    if len(codes) >= 30 and days <= 200:
        pdays = 150 if days < 150 else days
        frames = _cached(f"kline_panel:{pdays}", 600, lambda: _kline_panel_frames(pdays))
        need = max(int(days * 0.4), 10)
        for c in codes:
            df = None
            try:
                ts_code = _ts_code_cn(c)
                f = frames.get(ts_code)
                if f is not None and len(f) >= need:
                    df = f.iloc[-days:].reset_index(drop=True) if len(f) > days else f
                    _cache[f"kline:{ts_code}:{days}"] = (time.time(), 1800, df)
            except MarketDataUnavailable:
                df = None
            out[c] = df
        return out
    for c in codes:
        try:
            out[c] = real_get_kline(c, days)
        except Exception:
            out[c] = None
    return out


# ─── pro_api 安全替代 (HTTPS 闸门内) ─────────────────────────

class RealProShim:
    """tushare pro_api 客户端的本地替代: 仅实现下列只读方法, 全部经 _api 单点闸门
    (HTTPS传输验收 + 用途声明 + 全局1s节流); 未实现的方法一律 AttributeError。
    方法返回 pandas.DataFrame (与 pro_api 契约一致), 空结果 = 空DataFrame。
    按 (接口,参数,字段) 6h 缓存; 空结果 10min (盘中未发布不反复撞击)"""

    def _df(self, api_name, params, fields="", ttl=21600, empty_ttl=600):
        params = {k: v for k, v in (params or {}).items() if v not in (None, "")}
        key = f"pro:{api_name}:{sorted(params.items())}:{fields}"

        def _fetch():
            f, items = _api(api_name, params, fields)
            return pd.DataFrame(_rows(f, items))
        return _cached(key, ttl, _fetch, empty_ttl=empty_ttl)

    def daily(self, ts_code=None, trade_date=None, start_date=None, end_date=None,
              fields=""):
        if trade_date and not ts_code:
            snap = _daily_snapshot(trade_date)
            if snap is None:
                return pd.DataFrame()
            cols = [c for c in fields.split(",") if c in snap.columns] if fields else None
            return snap[cols] if cols else snap
        return self._df("daily", {"ts_code": ts_code, "trade_date": trade_date,
                                  "start_date": start_date, "end_date": end_date}, fields)

    def daily_basic(self, ts_code=None, trade_date=None, start_date=None, end_date=None,
                    fields=""):
        if trade_date and not ts_code:
            snap = _daily_basic_snapshot(trade_date)
            if snap is None:
                return pd.DataFrame()
            cols = [c for c in fields.split(",") if c in snap.columns] if fields else None
            return snap[cols] if cols else snap
        if ts_code and not (start_date or end_date or trade_date):
            end_date = time.strftime("%Y%m%d")
            start_date = time.strftime("%Y%m%d", time.localtime(time.time() - 370 * 86400))
        return self._df("daily_basic", {"ts_code": ts_code, "trade_date": trade_date,
                                        "start_date": start_date, "end_date": end_date},
                        fields)

    def trade_cal(self, start_date=None, end_date=None, is_open="", **kw):
        return self._df("trade_cal", {"exchange": "SSE", "start_date": start_date,
                                      "end_date": end_date, "is_open": is_open},
                        "cal_date,is_open")

    def fina_indicator(self, ts_code=None, fields="", **kw):
        return self._df("fina_indicator", {"ts_code": ts_code}, fields)

    def top_list(self, trade_date=None, fields="", **kw):
        return self._df("top_list", {"trade_date": trade_date}, fields)

    def moneyflow(self, trade_date=None, fields="", **kw):
        return self._df("moneyflow", {"trade_date": trade_date}, fields)


_PRO_SHIM = RealProShim()


def real_pro():
    """获取 pro_api 安全替代实例 (单例)"""
    return _PRO_SHIM


# ─── 融资融券 (margin_detail, 2026-10-24 实测积分可用) ────────

def _margin_row(ts_code, trade_date):
    def _fetch():
        fields, items = _api("margin_detail",
                             {"ts_code": ts_code, "trade_date": trade_date},
                             "ts_code,trade_date,rzye,rzmre,rqyl,rqmcl")
        rows = _rows(fields, items)
        if not rows:
            return None
        r = rows[0]
        return {"rzye": _number(r.get("rzye"), "rzye", nonnegative=True, optional=True),
                "rzmre": _number(r.get("rzmre"), "rzmre", nonnegative=True, optional=True),
                "rqyl": _number(r.get("rqyl"), "rqyl", nonnegative=True, optional=True),
                "rqmcl": _number(r.get("rqmcl"), "rqmcl", nonnegative=True, optional=True),
                "trade_date": _fmt_date(r.get("trade_date") or trade_date),
                "source": "tushare-margin_detail"}
    return _cached(f"margin:{ts_code}:{trade_date}", 21600, _fetch, empty_ttl=600)


def real_get_margin_detail(ts_code=None, trade_date=None):
    """个股融资融券余额 → {rzye,rzmre,rqyl,rqmcl,trade_date,name,source} 或 {"error": ...}
    (data.fetcher.get_margin_detail 契约); 数据日无该券记录时回退最近3个开市日
    (真实历史记录, trade_date 字段注明实际日期, 不冒充当日)"""
    try:
        ts = _ts_code_cn(ts_code) if ts_code else None
        if not ts:
            return {"error": "missing ts_code"}
        d0 = trade_date or _data_date()
        candidates = [d0] + [d for d in _open_days(8) if d < d0][:3]
        for d in candidates:
            row = _margin_row(ts, d)
            if row:
                row = dict(row)
                try:
                    row["name"] = real_get_stock_basic().get(ts, {}).get("name", "")
                except Exception:
                    row["name"] = ""
                return row
        return {"error": "no margin data"}
    except (TushareError, MarketDataUnavailable) as e:
        return {"error": str(e)[:120]}


# ─── 按数据日键控的计算缓存 (扫描/建议/推荐 等重计算) ──────────

_day_locks = {}
_day_locks_guard = threading.Lock()


def real_day_cached(key, fn):
    """按 EOD 数据日键控的 6h 缓存包装: 同一数据日内重计算只跑一次
    (日线发布后当日不再变化, refresh 同日不重复消耗 API);
    同键并发单飞 — 后来的线程等先算的写缓存后直接读"""
    def _run():
        d = _data_date()
        ckey = f"daycache:{key}:{d}"
        ent = _cache.get(ckey)
        if ent and time.time() - ent[0] < ent[1]:
            return ent[2]
        with _day_locks_guard:
            lock = _day_locks.setdefault(ckey, threading.Lock())
        with lock:
            return _cached(ckey, 21600, fn)
    return _run
