"""海淘掘金 — 统一数据提供商
支持多个后端: Finnhub (主) → Alpha Vantage (备) → Stooq (紧急备)

配置: .env 中设置 FINNHUB_KEY=c5q8n... 
      未设置时自动使用 Alpha Vantage demo keys 轮询
"""
import os, time, logging
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

# ─── 缓存 ──────────────────────────────
_cache, _cache_ttl = {}, {}
def _cached(k, ttl):
    if k in _cache and k in _cache_ttl and time.time() < _cache_ttl[k]:
        return _cache[k]
    return None
def _set_cache(k, d, ttl):
    _cache[k] = d; _cache_ttl[k] = time.time() + ttl


# ═══════════════════════════════════════════
# Finnhub Provider (主要)
# ═══════════════════════════════════════════

FINNHUB_KEY = os.environ.get("FINNHUB_KEY", "")
_finnhub = None

def _get_finnhub():
    global _finnhub
    if _finnhub is None and FINNHUB_KEY:
        try:
            import finnhub
            _finnhub = finnhub.Client(api_key=FINNHUB_KEY)
        except:
            pass
    return _finnhub


# ═══════════════════════════════════════════
# Alpha Vantage Provider (备用, 轮询多个demo key)
# ═══════════════════════════════════════════

_AV_KEYS = [f"demo{i}" for i in range(1, 10)]  # demo1~demo9
_av_idx = 0
_av_last = 0

def _av_quote(ticker: str) -> dict:
    """Get quote from Alpha Vantage with key rotation"""
    import requests
    global _av_idx, _av_last
    
    elapsed = time.time() - _av_last
    if elapsed < 1.5:
        time.sleep(1.5 - elapsed)
    _av_last = time.time()
    
    key = _AV_KEYS[_av_idx % len(_AV_KEYS)]
    _av_idx += 1
    
    try:
        url = f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={ticker}&apikey={key}"
        r = requests.get(url, timeout=10)
        data = r.json()
        q = data.get("Global Quote", {})
        if q:
            price = float(q.get("05. price", 0))
            change = float(q.get("09. change", 0))
            pct = float(q.get("10. change percent", "0%").replace("%", ""))
            return {"price": price, "change": change, "change_pct": pct,
                    "name": ticker, "source": "alphavantage"}
        # Check for rate limit
        if "Note" in data:
            logger.debug(f"AV rate limited on {key}, next key")
    except Exception as e:
        logger.debug(f"AV fail {ticker}: {e}")
    return {}


# ═══════════════════════════════════════════
# Stooq Provider (紧急备用)
# ═══════════════════════════════════════════

import requests as std_req

def _stooq_quote(ticker: str) -> dict:
    """Emergency backup from stooq"""
    try:
        key = f"{ticker}.US" if not ticker.startswith("^") else ticker
        url = f"https://stooq.com/q/l/?s={key}&f=sd2t2ohlcvn&h&e=csv"
        r = std_req.get(url, timeout=10)
        lines = r.text.strip().split("\n")
        if len(lines) < 2: return {}
        parts = lines[1].split(",")
        if len(parts) < 9: return {}
        c = parts[6].strip()
        if c in ("N/A", ""): return {}
        price = float(c)
        op = float(parts[3]) if parts[3] != "N/A" else price
        return {"price": price, "name": parts[8].strip(), "change": round(price-op,2) if op else 0,
                "change_pct": round((price-op)/op*100,2) if op and op>0 else 0, "source": "stooq"}
    except:
        return {}


# ═══════════════════════════════════════════
# 统一报价接口
# ═══════════════════════════════════════════

def get_quotes(tickers: List[str]) -> List[dict]:
    """获取报价: Finnhub → Alpha Vantage → Stooq"""
    clean = [t.strip().upper() for t in tickers if t.strip()]
    if not clean:
        return []
    
    # Check cache
    now = time.time()
    results = {}
    remaining = []
    for t in clean:
        c = _cached(f"q_{t}", 60)
        if c:
            results[t] = c
        else:
            remaining.append(t)
    
    if remaining:
        # Try Finnhub first (if key available)
        fh = _get_finnhub()
        if fh:
            for t in remaining:
                try:
                    q = fh.quote(t)
                    if q and q.get("c", 0) > 0:
                        r = {"price": float(q["c"]), "change": round(float(q["c"])-float(q["pc"]),2),
                             "change_pct": round((float(q["c"])-float(q["pc"]))/float(q["pc"])*100,2) if q.get("pc") else 0,
                             "name": t, "source": "finnhub"}
                        _set_cache(f"q_{t}", r, 60)
                        results[t] = r
                        continue
                except: pass
                time.sleep(0.5)
        
        # Finnhub unavailable: parallel Alpha Vantage for remaining
        av_remaining = [t for t in remaining if t not in results]
        if av_remaining:
            # Sequential AV (rate limited to 5/min per key, rotate)
            for t in av_remaining:
                r = _av_quote(t)
                if r.get("price", 0) > 0:
                    _set_cache(f"q_{t}", r, 60)
                    results[t] = r
    
    # Stooq emergency for any still missing
    for t in clean:
        if t not in results:
            r = _stooq_quote(t)
            if r.get("price", 0) > 0:
                _set_cache(f"q_{t}", r, 60)
                results[t] = r
    
    return [{"ticker": t,
             "name": results.get(t, {}).get("name", t),
             "price": results.get(t, {}).get("price", 0),
             "change": results.get(t, {}).get("change", 0),
             "change_pct": results.get(t, {}).get("change_pct", 0),
             "volume": 0, "avg_vol": 0, "market_cap": 0,
             "sector": "", "industry": "",
             "source": results.get(t, {}).get("source", "none"),
            } for t in clean]


# ═══════════════════════════════════════════
# 指数 / 热门 / 中概
# ═══════════════════════════════════════════

from haitao.config import US_INDICES, CHINESE_ADR, HOT_US_STOCKS

def get_indices():
    c = _cached("us_idx", 120)
    if c: return c
    qs = get_quotes(list(US_INDICES.keys()))
    r = [{"ticker": q["ticker"], "name": US_INDICES.get(q["ticker"], q.get("name","")),
          "price": q["price"], "change": q["change"], "change_pct": q["change_pct"],
          "source": q.get("source","")} for q in qs]
    _set_cache("us_idx", r, 120)
    return r

def get_hot():
    c = _cached("us_hot", 60)
    if c: return c
    r = get_quotes(HOT_US_STOCKS)
    _set_cache("us_hot", r, 60)
    return r

def get_adr():
    c = _cached("us_adr", 60)
    if c: return c
    r = get_quotes(CHINESE_ADR)
    _set_cache("us_adr", r, 60)
    return r

def get_prepost(extra=None):
    c = _cached("us_pp", 60)
    if c: return c
    all_t = list(set(HOT_US_STOCKS + CHINESE_ADR + (extra or [])))
    qs = get_quotes(all_t)
    sq = sorted(qs, key=lambda x: abs(x.get("change_pct",0)), reverse=True)
    r = {"top_movers": sq[:15]}
    _set_cache("us_pp", r, 60)
    return r

def get_us_dashboard():
    return {"indices": get_indices(), "hot_stocks": get_hot(),
            "chinese_adr": get_adr(), "pre_post": get_prepost()}


# ═══════════════════════════════════════════
# 历史K线（Finnhub）
# ═══════════════════════════════════════════

def get_history(ticker: str, days: int = 60):
    """获取历史K线 — Yahoo/AV/Finnhub, 都受限时回退到5天"""
    ck = f"hist_{ticker}_{days}"
    c = _cached(ck, 300)
    if c: return c
    
    # Try Yahoo via curl_cffi
    try:
        from curl_cffi import requests as yreq
        enc = ticker.replace("^", "%5E")
        rng = "1y" if days >= 200 else "6mo" if days >= 100 else "3mo" if days >= 50 else "1mo" if days >= 20 else "5d"
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{enc}?interval=1d&range={rng}"
        r = yreq.get(url, impersonate="chrome131", timeout=15)
        if r.status_code == 200:
            return _parse_yahoo_chart(r.json(), ck)
        elif r.status_code == 429:
            # Fallback: use 5d chart from quote endpoint
            time.sleep(1)
            r2 = yreq.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{enc}?interval=1d&range=5d", impersonate="chrome131", timeout=15)
            if r2.status_code == 200:
                return _parse_yahoo_chart(r2.json(), ck)
    except Exception as e:
        logger.debug(f"Yahoo history fail {ticker}: {e}")
    
    return None

def _parse_yahoo_chart(data, ck):
    """Parse Yahoo Chart API response into DataFrame"""
    try:
        result = data.get("chart", {}).get("result", [None])[0]
        if not result: return None
        ts = result.get("timestamp", [])
        if not ts: return None
        quotes = result.get("indicators", {}).get("quote", [{}])[0]
        recs = []
        for i, tstamp in enumerate(ts):
            cv = quotes.get("close", [None])[i] if quotes else None
            if cv is None: continue
            recs.append({
                "Date": datetime.fromtimestamp(tstamp, tz=timezone.utc),
                "Open": quotes.get("open", [None])[i] if quotes else 0,
                "High": quotes.get("high", [None])[i] if quotes else 0,
                "Low": quotes.get("low", [None])[i] if quotes else 0,
                "Close": cv,
                "Volume": int(quotes.get("volume", [0])[i] or 0) if quotes else 0,
            })
        if recs:
            df = pd.DataFrame(recs)
            _set_cache(ck, df, 300)
            return df
    except:
        pass
    return None


def get_market_status() -> dict:
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


def clear_cache():
    _cache.clear(); _cache_ttl.clear()


# ─── 技术指标计算 ──────────────────────────

def calc_technical_indicators(df) -> dict:
    """计算技术指标"""
    import numpy as np
    if df is None or len(df) < 5: return {}
    c = df["Close"].values.astype(float)
    h = df["High"].values.astype(float) if "High" in df.columns else c
    l = df["Low"].values.astype(float) if "Low" in df.columns else c
    
    def sma(n): return float(np.mean(c[-n:])) if len(c) >= n else None
    s5, s20, s60 = sma(5), sma(20), sma(60)
    
    rsi = None
    if len(c) >= 15:
        d = np.diff(c)
        ag = float(np.mean(np.where(d > 0, d, 0)[-14:])) if len(c) >= 15 else 0
        al = float(np.mean(np.where(d < 0, -d, 0)[-14:])) if len(c) >= 15 else 0
        rsi = round(100 - 100/(1+ag/al), 1) if al > 0 else (100.0 if ag > 0 else 50.0)
    
    atr = None
    if len(c) >= 16:
        p = c[:-1]
        tr = np.maximum(h[1:]-l[1:], np.maximum(np.abs(h[1:]-p), np.abs(l[1:]-p)))
        atr = round(float(np.mean(tr[-14:])), 2)
    
    macd = None
    if len(c) >= 26:
        e12 = _ema(c, 12); e26 = _ema(c, 26)
        ml = e12[-1]-e26[-1]; sg = _ema_single(c, 9)
        macd = {"macd": round(float(ml),2), "signal": round(float(sg),2), "hist": round(float(ml-sg),2)}
    
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
    
    return {"current_price": round(cur,2), "sma5": round(s5,2) if s5 else None,
            "sma20": round(s20,2) if s20 else None, "sma60": round(s60,2) if s60 else None,
            "rsi14": rsi, "atr14": atr, "macd": macd,
            "bb_upper": bb_u, "bb_lower": bb_l, "volume_ratio": vr,
            "price_vs_sma5": round((cur/s5-1)*100, 2) if s5 and s5 > 0 else None,
            "price_vs_sma20": round((cur/s20-1)*100, 2) if s20 and s20 > 0 else None}

def _ema(vals, n):
    import numpy as np
    r = np.zeros_like(vals); r[:n] = float(np.mean(vals[:n]))
    m = 2/(n+1)
    for i in range(n, len(vals)): r[i] = (vals[i]-r[i-1])*m+r[i-1]
    return r

def _ema_single(vals, n):
    v = _ema(vals, n); return float(v[-1]) if len(v) > 0 else 0
