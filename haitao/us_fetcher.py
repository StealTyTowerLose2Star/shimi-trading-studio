"""海淘掘金 - 数据抓取核心 v3
双源架构: stooq.com (主力实时) + Yahoo Chart API/curl_cffi (ETF/指数/K线)

stooq: 个股全部支持(AAPL.US, BABA.US...), 免费无Key, 不限流
Yahoo: ETF(SPY/QQQ)/指数(^GSPC)/历史K线, curl_cffi模拟Chrome, 有429风险
"""
import time, logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

from haitao.config import US_INDICES, CHINESE_ADR, HOT_US_STOCKS
from haitao.config import CACHE_TTL_QUOTES, CACHE_TTL_INDICES, CACHE_TTL_HISTORY

# ─── HTTP Sessions ──────────────────────────
import requests as std_requests
_stooq_session = std_requests.Session()
_stooq_session.headers.update({"User-Agent": "Mozilla/5.0"})
STOOQ = "https://stooq.com/q/l/"

# curl_cffi for Yahoo (lazy-loaded to avoid import errors)
_yahoo_session = None
YF_BASE = "https://query1.finance.yahoo.com/v8/finance/chart"

def _get_yahoo():
    global _yahoo_session
    if _yahoo_session is None:
        try:
            from curl_cffi import requests
            _yahoo_session = requests.Session()
        except ImportError:
            _yahoo_session = std_requests.Session()
    return _yahoo_session

# ─── 缓存 ──────────────────────────────
_cache, _cache_ttl = {}, {}
def _cached(k, ttl):
    if k in _cache and k in _cache_ttl and time.time() < _cache_ttl[k]:
        return _cache[k]
    return None
def _set_cache(k, d, ttl):
    _cache[k] = d; _cache_ttl[k] = time.time() + ttl


# ─── 市场状态 ──────────────────────────────

def get_us_market_status() -> dict:
    now = datetime.now(timezone.utc)
    m = now.month; offset = -4 if 3 <= m <= 10 else -5
    eh = (now.hour + offset) % 24; em = now.minute; wd = now.weekday()
    s = "休市"
    if wd < 5:
        if 4 <= eh < 9 or (eh == 9 and em < 30): s = "盘前"
        elif (eh == 9 and em >= 30) or (10 <= eh < 16): s = "盘中"
        elif 16 <= eh < 20: s = "盘后"
    return {"status": s, "is_open": s == "盘中", "time_et": f"{eh:02d}:{em:02d}",
            "weekday": ["周一","周二","周三","周四","周五","周六","周日"][wd]}


# ─── stooq 实时报价（主力） ────────────────

def _stooq_price(ticker: str) -> dict:
    """Fetch from stooq.com - reliable, no rate limit"""
    try:
        key = f"{ticker}.US" if not ticker.startswith("^") else ticker.replace("^", "%5E")
        url = f"{STOOQ}?s={key}&f=sd2t2ohlcvn&h&e=csv"
        r = _stooq_session.get(url, timeout=10)
        lines = r.text.strip().split("\n")
        if len(lines) < 2: return {}
        parts = lines[1].split(",")
        if len(parts) < 9: return {}
        c = parts[6].strip()
        if c in ("N/A", ""): return {}
        price = float(c)
        # Format: Symbol,Date,Time,Open,High,Low,Close,Volume,Name
        op = float(parts[3]) if parts[3] != "N/A" else price
        return {
            "price": price, "name": parts[8].strip(),
            "open": op,
            "change": round(price - op, 2) if op else 0,
            "change_pct": round((price - op) / op * 100, 2) if op and op > 0 else 0,
            "volume": int(float(parts[7])) if parts[7] != "N/A" else 0,
        }
    except Exception as e:
        logger.debug(f"stooq fail {ticker}: {e}")
        return {}


# ─── Yahoo 后备（带重试） ──────────────────────

_yahoo_last = 0
_yahoo_retry_count = {}

def _yahoo_fetch(ticker: str, endpoint: str = "chart") -> Optional[dict]:
    """Fetch from Yahoo API with rate limiting and retry"""
    global _yahoo_last
    now = time.time()
    elapsed = now - _yahoo_last
    min_delay = 3.0 if endpoint == "chart" else 2.0
    if elapsed < min_delay:
        time.sleep(min_delay - elapsed)
    _yahoo_last = time.time()
    
    try:
        s = _get_yahoo()
        if endpoint == "chart":
            enc = ticker.replace("^", "%5E")
            url = f"{YF_BASE}/{enc}?interval=1d&range=5d"
        else:
            return None
        
        r = s.get(url, impersonate="chrome131", timeout=15)
        if r.status_code == 429:
            logger.debug(f"Yahoo 429 on {ticker}, waiting 5s...")
            time.sleep(5)
            r = s.get(url, impersonate="chrome131", timeout=15)
            if r.status_code == 429:
                logger.warning(f"Yahoo still 429 on {ticker}, skipping")
                return None
        if r.status_code != 200:
            return None
        return r.json().get("chart", {}).get("result", [None])[0]
    except Exception as e:
        logger.debug(f"Yahoo error {ticker}: {e}")
        return None


def _yahoo_price(ticker: str) -> dict:
    """Fetch price from Yahoo (for ^ indices and ETF fallback)"""
    result = _yahoo_fetch(ticker, "chart")
    if not result:
        return {}
    meta = result.get("meta", {})
    price = meta.get("regularMarketPrice") or 0
    prev = meta.get("chartPreviousClose") or 0
    chg = round(float(price) - float(prev), 2) if prev and price else 0
    cpct = round((float(price) - float(prev)) / float(prev) * 100, 2) if prev and price else 0
    return {"price": float(price) if price else 0, "change": chg, "change_pct": cpct,
            "name": meta.get("shortName", meta.get("longName", ticker))}


# ─── 统一报价接口 ──────────────────────────

def _get_ticker_price(ticker: str) -> dict:
    """Get price: stooq first, Yahoo fallback"""
    c = _cached(f"q_{ticker}", CACHE_TTL_QUOTES)
    if c: return c
    
    # stooq handles most stocks; Yahoo needed for ETFs/indices with ^ prefix
    if ticker.startswith("^"):
        r = _yahoo_price(ticker)
    else:
        r = _stooq_price(ticker)
        if not r or not r.get("price"):
            r = _yahoo_price(ticker)
    
    if r and r.get("price"):
        _set_cache(f"q_{ticker}", r, CACHE_TTL_QUOTES)
    return r or {"price": 0, "name": ticker}
def get_quotes(tickers: List[str]) -> List[dict]:
    """Get quotes - parallel stooq + sequential Yahoo"""
    clean = [t.strip().upper() for t in tickers if t.strip()]
    if not clean:
        return []
    
    # First try cache for all
    now = time.time()
    cached_results = {}
    uncached_stooq = []
    uncached_yahoo = []
    for t in clean:
        c = _cached(f"q_{t}", CACHE_TTL_QUOTES)
        if c:
            cached_results[t] = c
        elif t.startswith("^"):
            uncached_yahoo.append(t)
        else:
            uncached_stooq.append(t)
    
    # Batch fetch stooq tickers in parallel (no rate limit)
    if uncached_stooq:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=8) as ex:
            def _st(t):
                r = _stooq_price(t)
                if r and r.get("price"):
                    _set_cache(f"q_{t}", r, CACHE_TTL_QUOTES)
                return t, r
            fut = {ex.submit(_st, t): t for t in uncached_stooq}
            for f in as_completed(fut):
                t = fut[f]; r = f.result()[1]
                cached_results[t] = r or {"price": 0, "name": t}
    
    # Sequential Yahoo for ^ tickers (needs rate limiting)
    for t in uncached_yahoo:
        r = _yahoo_price(t)
        if r and r.get("price"):
            _set_cache(f"q_{t}", r, CACHE_TTL_QUOTES)
        cached_results[t] = r or {"price": 0, "name": t}
    
    # Build results
    return [{
        "ticker": t, "name": cached_results.get(t, {}).get("name", t),
        "price": cached_results.get(t, {}).get("price", 0),
        "change": cached_results.get(t, {}).get("change", 0),
        "change_pct": cached_results.get(t, {}).get("change_pct", 0),
        "volume": cached_results.get(t, {}).get("volume", 0),
        "avg_vol": 0, "market_cap": 0, "sector": "", "industry": "",
    } for t in clean]


# ─── 指数 / 热门 / 中概 ────────────────────

def get_indices() -> List[dict]:
    c = _cached("us_indices", CACHE_TTL_INDICES)
    if c: return c
    qs = get_quotes(list(US_INDICES.keys()))
    r = [{"ticker": q["ticker"], "name": US_INDICES.get(q["ticker"], q.get("name","")),
          "price": q["price"], "change": q["change"], "change_pct": q["change_pct"]} for q in qs]
    _set_cache("us_indices", r, CACHE_TTL_INDICES)
    return r

def get_chinese_adr() -> List[dict]:
    c = _cached("us_adr", CACHE_TTL_QUOTES)
    if c: return c
    r = get_quotes(CHINESE_ADR)
    _set_cache("us_adr", r, CACHE_TTL_QUOTES)
    return r

def get_hot_stocks() -> List[dict]:
    c = _cached("us_hot", CACHE_TTL_QUOTES)
    if c: return c
    r = get_quotes(HOT_US_STOCKS)
    _set_cache("us_hot", r, CACHE_TTL_QUOTES)
    return r

def get_pre_post_market(extra: List[str] = None) -> dict:
    c = _cached("us_prepost", CACHE_TTL_QUOTES)
    if c: return c
    all_t = list(set(HOT_US_STOCKS + CHINESE_ADR + (extra or [])))
    qs = get_quotes(all_t)
    sq = sorted(qs, key=lambda x: abs(x.get("change_pct", 0)), reverse=True)
    r = {"pre_market": [], "post_market": [],
         "top_movers": [{"ticker": q["ticker"], "name": q.get("name",""),
                         "change_pct": q["change_pct"], "price": q["price"]} for q in sq[:15]]}
    _set_cache("us_prepost", r, CACHE_TTL_QUOTES)
    return r


# ─── 历史 K 线（Yahoo） ─────────────────────

def get_history(ticker: str, period: str = "3mo", interval: str = "1d") -> Optional[pd.DataFrame]:
    ck = f"hist_{ticker}_{period}_{interval}"
    c = _cached(ck, CACHE_TTL_HISTORY)
    if c: return c
    
    # Try Yahoo for full history
    result = None
    try:
        s = _get_yahoo()
        enc = ticker.replace("^", "%5E")
        url = f"{YF_BASE}/{enc}?interval={interval}&range={period}"
        r = s.get(url, impersonate="chrome131", timeout=15)
        if r.status_code == 200:
            result = r.json().get("chart", {}).get("result", [None])[0]
        elif r.status_code == 429:
            # Fallback: use the short chart from _yahoo_fetch (already called)
            time.sleep(2)
            result = _yahoo_fetch(ticker, "chart")
    except:
        result = _yahoo_fetch(ticker, "chart")
    
    if not result:
        return None
    
    ts = result.get("timestamp", [])
    if not ts:
        return None
    quotes = result.get("indicators", {}).get("quote", [{}])[0]
    recs = []
    for i, tstamp in enumerate(ts):
        cv = quotes.get("close", [None])[i] if quotes else None
        if cv is None: continue
        recs.append({"Date": datetime.fromtimestamp(tstamp, tz=timezone.utc),
                     "Open": quotes.get("open", [None])[i] if quotes else None,
                     "High": quotes.get("high", [None])[i] if quotes else None,
                     "Low": quotes.get("low", [None])[i] if quotes else None,
                     "Close": cv,
                     "Volume": int(quotes.get("volume", [0])[i] or 0) if quotes else 0})
    if not recs: return None
    df = pd.DataFrame(recs).dropna(subset=["Close"])
    _set_cache(ck, df, CACHE_TTL_HISTORY)
    return df


# ─── 技术指标 ──────────────────────────────

def calc_technical_indicators(df: pd.DataFrame) -> dict:
    if df is None or len(df) < 20: return {}
    c = df["Close"].values.astype(float)
    h = df["High"].values.astype(float)
    l = df["Low"].values.astype(float)
    
    def sma(n): return float(np.mean(c[-n:])) if len(c) >= n else None
    s5, s20, s60, s100 = sma(5), sma(20), sma(60), sma(100)
    
    rsi = None
    if len(c) >= 15:
        d = np.diff(c)
        ag = float(np.mean(np.where(d > 0, d, 0)[-14:]))
        al = float(np.mean(np.where(d < 0, -d, 0)[-14:]))
        rsi = round(100 - 100/(1+ag/al), 1) if al > 0 else 100.0
    
    atr = None
    if len(c) >= 16:
        p = c[:-1]
        tr = np.maximum(h[1:]-l[1:], np.maximum(np.abs(h[1:]-p), np.abs(l[1:]-p)))
        atr = round(float(np.mean(tr[-14:])), 2)
    
    macd = None
    if len(c) >= 26:
        e12 = _ema(c, 12); e26 = _ema(c, 26)
        ml = e12[-1]-e26[-1]; sg = _ema_single(c, 9)
        macd = {"macd": round(float(ml), 2), "signal": round(float(sg), 2), "hist": round(float(ml-sg), 2)}
    
    cur = float(c[-1])
    vr = None
    if "Volume" in df.columns and len(c) >= 21:
        v = df["Volume"].values.astype(float)
        av = float(np.mean(v[-21:-1]))
        vr = round(float(v[-1])/av, 2) if av > 0 else None
    
    bb_u = bb_l = None
    if len(c) >= 20 and s20:
        std = float(np.std(c[-20:]))
        bb_u = round(s20+2*std, 2); bb_l = round(s20-2*std, 2)
    
    return {"current_price": round(cur, 2), "sma5": round(s5,2) if s5 else None,
            "sma20": round(s20,2) if s20 else None, "sma60": round(s60,2) if s60 else None,
            "sma100": round(s100,2) if s100 else None, "rsi14": rsi, "atr14": atr, "macd": macd,
            "bb_upper": bb_u, "bb_lower": bb_l, "volume_ratio": vr,
            "price_vs_sma5": round((cur/s5-1)*100, 2) if s5 and s5>0 else None,
            "price_vs_sma20": round((cur/s20-1)*100, 2) if s20 and s20>0 else None}

def _ema(vals, n):
    r = np.zeros_like(vals); r[:n] = float(np.mean(vals[:n]))
    m = 2/(n+1)
    for i in range(n, len(vals)): r[i] = (vals[i]-r[i-1])*m+r[i-1]
    return r

def _ema_single(vals, n):
    v = _ema(vals, n); return float(v[-1]) if len(v)>0 else 0


def get_us_dashboard() -> dict:
    return {"indices": get_indices(), "hot_stocks": get_hot_stocks(),
            "chinese_adr": get_chinese_adr(), "market_status": get_us_market_status(),
            "pre_post": get_pre_post_market()}

def clear_cache():
    _cache.clear(); _cache_ttl.clear()
