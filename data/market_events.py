"""
拾米交易工作室 - 市场事件监控引擎 (Market Sentinel)
职责: 抓取市场事件 → 映射受影响标的 → 生成做多/做空信号

数据源:
  - 巨潮资讯 (A股公告/政策)
  - Finnhub News API (美股新闻)
  - yfinance (财报日历)
  - 东方财富 (A股要闻)

架构: 零耦合 — 仅依赖 data/fetcher.py 和 haitao/us_fetcher.py
"""

import json
import os
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from collections import defaultdict

# ─── 事件类型定义 ─────────────────────────────────

EVENT_TYPES = {
    "policy": {
        "name": "政策驱动",
        "keywords": ["国务院", "发改委", "工信部", "央行", "证监会", "降准", "降息", "LPR",
                    "减税", "补贴", "新基建", "碳中和", "数据要素"],
        "en_keywords": ["fed", "federal reserve", "white house", "congress", "regulation",
                       "regulator", "ban", "executive order", "tariff", "subsidy",
                       "biden", "trump", "administration", "lawmakers", "legislation"],
        "impact": "high",
        "duration_days": 30,
    },
    "earnings": {
        "name": "财报窗口",
        "keywords": ["业绩预告", "年报", "季报", "营收", "净利润", "EPS"],
        "en_keywords": ["earnings", "revenue", "profit", "quarterly", "annual report",
                       "eps", "guidance", "beat estimates", "miss estimates",
                       "upgrad", "downgrad", "outlook", "forecast", "dividend"],
        "impact": "high",
        "duration_days": 14,
    },
    "macro": {
        "name": "宏观数据",
        "keywords": ["GDP", "CPI", "PPI", "PMI", "社融", "M2", "进出口", "外汇储备",
                    "非农", "FOMC", "美联储", "利率决议"],
        "en_keywords": ["gdp", "cpi", "ppi", "pmi", "inflation", "employment",
                       "jobless claims", "payrolls", "fomc", "interest rate",
                       "treasury", "yield", "bond", "recession", "economic data"],
        "impact": "medium",
        "duration_days": 7,
    },
    "geopolitical": {
        "name": "地缘事件",
        "keywords": ["制裁", "关税", "贸易战", "冲突", "协议", "脱钩"],
        "en_keywords": ["sanction", "tariff", "trade war", "conflict", "tension",
                       "military", "war", "iran", "north korea", "russia", "ukraine",
                       "china-us", "decoupling", "national security", "diplomatic"],
        "impact": "high",
        "duration_days": 60,
    },
    "sector": {
        "name": "行业动态",
        "keywords": ["光伏", "新能源", "半导体", "AI", "人工智能", "医药", "消费",
                    "房地产", "汽车", "芯片", "锂电", "储能"],
        "en_keywords": ["semiconductor", "chip", "ai", "artificial intelligence",
                       "ev", "electric vehicle", "solar", "battery", "biotech",
                       "pharma", "tech", "cloud", "software", "cyber",
                       "nvidia", "apple", "tesla", "microsoft", "google"],
        "impact": "medium",
        "duration_days": 14,
    },
    "commodity": {
        "name": "商品价格",
        "keywords": ["原油", "黄金", "铜", "铝", "锂", "螺纹钢", "铁矿石", "天然气"],
        "en_keywords": ["oil", "gold", "copper", "aluminum", "lithium",
                       "natural gas", "commodity", "crude", "opec", "metal"],
        "impact": "medium",
        "duration_days": 7,
    },
}

# ─── 行业→股票映射 ─────────────────────────────

SECTOR_STOCK_MAP = {
    "半导体": ["002371", "688981", "603501", "NVDA", "AMD", "AVGO"],
    "新能源": ["300750", "002594", "601012", "TSLA", "NIO", "RIVN"],
    "光伏": ["601012", "688599", "002459", "ENPH", "FSLR"],
    "AI": ["688111", "002230", "300033", "NVDA", "MSFT", "GOOGL"],
    "医药": ["600276", "300760", "000661", "PFE", "MRNA", "JNJ"],
    "消费": ["600519", "000858", "002304", "AAPL", "AMZN", "COST"],
    "房地产": ["000002", "001979", "600048"],
    "汽车": ["002594", "000625", "601238", "TSLA", "F", "GM"],
    "芯片": ["688981", "603986", "002049", "NVDA", "AMD", "INTC"],
    "锂电": ["300750", "002460", "002466", "ALB", "SQM"],
    "金融": ["601318", "600036", "600030", "JPM", "GS", "BAC"],
    "石油": ["601857", "600028", "XOM", "CVX", "COP"],
    "黄金": ["600547", "601899", "GOLD", "NEM"],
}

# 英文关键词 → 中文板块映射 (用于美股事件匹配)
EN_SECTOR_MAP = {
    "semiconductor": "半导体", "chip": "芯片", "nvidia": "芯片", "amd": "芯片",
    "ai": "AI", "artificial intelligence": "AI",
    "ev": "汽车", "electric vehicle": "汽车", "tesla": "汽车",
    "solar": "光伏", "battery": "锂电", "lithium": "锂电",
    "biotech": "医药", "pharma": "医药",
    "oil": "石油", "crude": "石油", "gold": "黄金", "copper": "商品",
    "tech": "AI", "cloud": "AI", "software": "AI", "cyber": "AI",
    "bank": "金融", "finance": "金融",
}

# ─── 事件存储 ──────────────────────────────────────

EVENT_CACHE = os.path.join(os.path.dirname(__file__), "data", "market_events.json")


def _load_events() -> List[Dict]:
    if os.path.exists(EVENT_CACHE):
        try:
            with open(EVENT_CACHE) as f:
                return json.load(f)
        except:
            pass
    return []


def _save_events(events: List[Dict]):
    os.makedirs(os.path.dirname(EVENT_CACHE), exist_ok=True)
    # 只保留最近90天
    cutoff = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
    events = [e for e in events if e.get("date", "") >= cutoff]
    with open(EVENT_CACHE, "w") as f:
        json.dump(events, f, ensure_ascii=False, indent=1)


# ─── A股事件抓取 (巨潮资讯) ──────────────────────────

def _parse_cn_catalysts(catalysts) -> List[Dict]:
    """巨潮催化剂公告 dict → 事件列表 (纯解析, 无 IO)"""
    events = []
    today = datetime.now().strftime("%Y-%m-%d")
    if isinstance(catalysts, dict):
        # cninfo_fetcher 返回 {"events": [...], "total": N, "period": "..."}
        evt_list = catalysts.get("events", [])
        for item in evt_list:
            if isinstance(item, dict):
                code = item.get("sec_code", "")
                # 补全6位代码格式
                if len(code) == 5:
                    code = "0" + code
                cninfo_type = item.get("type", "")
                events.append({
                    "date": item.get("date", today),
                    "market": "A",
                    "type": _cninfo_type_to_event_type(cninfo_type),
                    "title": item.get("title", "公告事件"),
                    "code": code,
                    "source": "cninfo",
                    "impact": "high" if cninfo_type == "C8_ST摘帽" else "medium",
                })
    return events


def fetch_cn_events_detailed() -> tuple:
    """返回 (events, status): status ∈ ok/empty/unavailable
    (2026-10-27 四态: cninfo_fetcher 全部关键词失败 → unavailable, 不得以空冒充)"""
    try:
        from data.cninfo_fetcher import fetch_monthly_catalysts
        catalysts = fetch_monthly_catalysts(datetime.now().strftime("%Y%m"))
    except Exception as e:
        print(f"[market_events] cninfo 失败: {e}")
        return [], "unavailable"
    events = _parse_cn_catalysts(catalysts)
    if isinstance(catalysts, dict) and catalysts.get("status") == "unavailable":
        return [], "unavailable"
    return events, ("ok" if events else "empty")


def fetch_cn_events() -> List[Dict]:
    """从巨潮资讯抓取A股公告/政策事件 (失败返回 []; 状态区分用 detailed 变体)"""
    events, _ = fetch_cn_events_detailed()
    return events


def _cninfo_type_to_event_type(cninfo_type: str) -> str:
    """巨潮资讯类型 → 事件类型映射"""
    mapping = {
        "C3_重大合同": "earnings",
        "C5_资产重组": "earnings",
        "C7_业绩超预期": "earnings",
        "C8_ST摘帽": "earnings",   # 摘帽=重大利好
    }
    return mapping.get(cninfo_type, "sector")


# ─── 美股事件抓取 (Finnhub) ─────────────────────────

def _fetch_us_events_core() -> List[Dict]:
    """Finnhub 抓取核心: 无凭据/非200/异常一律抛 (供状态判定)"""
    import requests
    finnhub_key = os.getenv("FINNHUB_KEY", "")
    if not finnhub_key:
        raise RuntimeError("FINNHUB_KEY 未配置")

    today = datetime.now().strftime("%Y-%m-%d")
    url = f"https://finnhub.io/api/v1/news?category=general&token={finnhub_key}"
    r = requests.get(url, timeout=10)
    if r.status_code != 200:
        raise RuntimeError(f"finnhub HTTP {r.status_code}")

    events = []
    news = r.json()
    for item in news[:30]:
        headline = item.get("headline", "")
        summary = item.get("summary", "")
        text = f"{headline} {summary}".lower()

        # 分类
        event_type = _classify_event(text)
        if not event_type:
            continue

        # 提取相关股票
        related = item.get("related", "").split(",") if item.get("related") else []

        # 时间戳转 HH:MM
        ts = item.get("datetime", 0)
        event_time = ""
        if ts:
            try:
                from datetime import datetime as dt
                event_time = dt.fromtimestamp(ts).strftime("%H:%M")
            except Exception:
                pass

        events.append({
            "date": today,
            "time": event_time,  # HH:MM
            "market": "US",
            "type": event_type,
            "title": headline[:100],
            "codes": related[:5],
            "source": "finnhub",
            "impact": EVENT_TYPES.get(event_type, {}).get("impact", "low"),
        })
    return events


def fetch_us_events_detailed() -> tuple:
    """返回 (events, status): 无凭据 → disabled; 请求失败 → unavailable"""
    if not os.getenv("FINNHUB_KEY", "").strip():
        return [], "disabled"
    try:
        events = _fetch_us_events_core()
        return events, ("ok" if events else "empty")
    except Exception as e:
        print(f"[market_events] finnhub 失败: {e}")
        return [], "unavailable"


def fetch_us_events() -> List[Dict]:
    """从 Finnhub 抓取美股新闻 (失败/无凭据返回 []; 状态区分用 detailed 变体)"""
    events, _ = fetch_us_events_detailed()
    return events


# ─── 美股财报日历抓取 (yfinance) ──────────────────

# 监控的标的池 (流动性好的大盘股 + 热门中概)
_YF_EARNINGS_WATCHLIST = [
    "NVDA", "AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA", "AMD", "INTC", "AVGO",
    "JPM", "GS", "BAC", "XOM", "CVX", "PFE", "JNJ", "NFLX", "ADBE", "CRM",
    "WMT", "DIS", "BA", "CAT", "GE", "NKE", "PYPL", "UBER", "ABNB", "SNOW",
    "ORCL", "IBM", "QCOM", "MU", "TXN", "LRCX", "ASML",
    "BABA", "JD", "PDD", "BIDU", "NIO", "BILI",
]


def _yfinance_disabled_reason() -> Optional[str]:
    """yfinance 源在本机的禁用原因; None 表示可尝试抓取。
    本机 real 模式出站白名单不含 Yahoo 域名 → 永禁 (不发请求, 显式标注);
    库未安装同理禁用 (不安装未验收依赖)。"""
    try:
        import yfinance  # noqa: F401
    except Exception:
        return "yfinance 库未安装 (本机不安装未验收依赖)"
    if os.environ.get("SHIMI_DATA_MODE", "real").strip().lower() == "real":
        return "yfinance/Yahoo 域名不在本机出站白名单 (real 模式永禁)"
    return None


def _fetch_yfinance_earnings_core() -> List[Dict]:
    """yfinance 抓取核心: 库缺失/异常一律抛 (供状态判定)"""
    import yfinance as yf
    from datetime import date, timedelta

    today = date.today()
    cutoff = today + timedelta(days=30)
    events = []

    # 批量获取（比逐个Ticker快10倍+）
    tickers_obj = yf.Tickers(" ".join(_YF_EARNINGS_WATCHLIST))
    for symbol in _YF_EARNINGS_WATCHLIST:
        try:
            t = tickers_obj.tickers.get(symbol)
            if not t:
                continue
            cal = getattr(t, "calendar", None)
            if not cal or "Earnings Date" not in cal:
                continue
            dates = cal["Earnings Date"]
            if not isinstance(dates, list):
                continue
            for d in dates:
                if d and isinstance(d, date) and today <= d <= cutoff:
                    eps_avg = cal.get("Earnings Average", 0) or 0
                    eps_low = cal.get("Earnings Low", 0) or 0
                    eps_high = cal.get("Earnings High", 0) or 0
                    events.append({
                        "date": d.strftime("%Y-%m-%d"),
                        "market": "US",
                        "type": "earnings",
                        "title": f"{symbol} 财报公布 (EPS预估 ${eps_avg:.2f})",
                        "codes": [symbol],
                        "source": "yfinance",
                        "impact": "high",
                        "summary": f"EPS estimate: ${eps_avg:.2f} (range ${eps_low:.2f}-${eps_high:.2f})",
                    })
        except Exception:
            continue
    return events


def fetch_yfinance_earnings_detailed() -> tuple:
    """返回 (events, status): 禁用 → disabled; 请求失败 → unavailable"""
    reason = _yfinance_disabled_reason()
    if reason:
        print(f"[market_events] yfinance 禁用: {reason}")
        return [], "disabled"
    try:
        events = _fetch_yfinance_earnings_core()
        return events, ("ok" if events else "empty")
    except Exception as e:
        print(f"[market_events] yfinance 失败: {e}")
        return [], "unavailable"


def fetch_yfinance_earnings() -> List[Dict]:
    """从 yfinance 抓取未来30天美股财报日历 (失败/禁用返回 []; 状态区分用 detailed 变体)"""
    events, _ = fetch_yfinance_earnings_detailed()
    return events


# ─── 超时保护 ──────────────────────────────────

import concurrent.futures

_FETCH_TIMEOUT = {
    "cninfo": 120,
    "eastmoney": 10,
    "finnhub": 15,
    "yfinance": 120,
    "flash_news": 25,
}


def _safe_fetch(name: str, fn, timeout: int = None) -> tuple:
    """带超时的安全抓取: fn 返回 (events, status); 超时/异常 → ([], "unavailable")

    status 四态 (2026-10-27 验收返修):
      ok          — 成功且有事件
      empty       — 成功但真实空结果 (不以空冒充失败, 也不以失败冒充空)
      unavailable — 请求失败/超时/异常
      disabled    — 凭据缺失或本机策略永禁 (由 detailed 变体直接返回, 不发请求)
    """
    if timeout is None:
        timeout = _FETCH_TIMEOUT.get(name, 10)
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(fn)
            events, status = future.result(timeout=timeout)
            return (events, status)
    except concurrent.futures.TimeoutError:
        print(f"[market_events] {name} 超时 ({timeout}s)")
        return ([], "unavailable")
    except Exception as e:
        print(f"[market_events] {name} 失败: {e}")
        return ([], "unavailable")


# ─── A股新闻快讯 (财联社/东财7×24, 免 key) ─────────────────

def fetch_flash_news_detailed() -> tuple:
    """返回 (events, status, sub_status): 两个子源全 unavailable → 整体 unavailable;
    有事件 → ok; 否则 empty (至少一源响应但无可分类事件, 真实空)"""
    from data.news_fetcher import fetch_market_news_detailed
    try:
        items, sub = fetch_market_news_detailed(40)
    except Exception as e:
        print(f"[market_events] flash_news 失败: {e}")
        return [], "unavailable", {}
    today = datetime.now().strftime("%Y-%m-%d")
    events = []
    for item in items:
        title = (item.get("title") or "").strip()
        content = (item.get("content") or "").strip()
        if not title and not content:
            continue
        text = f"{title} {content}"
        event_type = _classify_event(text)
        if not event_type:
            continue
        t = item.get("time", "") or ""
        events.append({
            "date": t[:10] or today,
            "time": t[11:16] if len(t) > 11 else "",
            "market": "A",
            "type": event_type,
            "title": (title or content)[:100],
            "source": item.get("source", "news"),
            "impact": EVENT_TYPES.get(event_type, {}).get("impact", "low"),
            "summary": content[:120],
        })
    if not items and sub and all(s == "unavailable" for s in sub.values()):
        return [], "unavailable", sub
    return events, ("ok" if events else "empty"), sub


def fetch_flash_news() -> List[Dict]:
    """从财联社电报 + 东财7×24 抓取全市场快讯并分类为事件

    数据源: data/news_fetcher (移植自 a-stock-data, 免 key 真实来源)。
    无法分类为已知事件类型的快讯丢弃 (防噪音), 与 fetch_us_events 同一契约。
    失败返回 [] (不抛); 状态区分用 fetch_flash_news_detailed。
    """
    events = []
    try:
        from data.news_fetcher import fetch_market_news
        today = datetime.now().strftime("%Y-%m-%d")
        for item in fetch_market_news(40):
            title = (item.get("title") or "").strip()
            content = (item.get("content") or "").strip()
            if not title and not content:
                continue
            text = f"{title} {content}"
            event_type = _classify_event(text)
            if not event_type:
                continue
            t = item.get("time", "") or ""
            events.append({
                "date": t[:10] or today,
                "time": t[11:16] if len(t) > 11 else "",
                "market": "A",
                "type": event_type,
                "title": (title or content)[:100],
                "source": item.get("source", "news"),
                "impact": EVENT_TYPES.get(event_type, {}).get("impact", "low"),
                "summary": content[:120],
            })
    except Exception:
        pass
    return events


# ─── A股公告抓取 (东方财富) ──────────────────────

def _fetch_eastmoney_news_core() -> List[Dict]:
    """东财公告抓取核心: 非200/异常一律抛 (供状态判定)"""
    import requests

    url = "https://np-anotice-stock.eastmoney.com/api/security/ann"
    params = {
        "page_size": 20,
        "page_index": 1,
        "ann_type": "A",  # 全部类型
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://data.eastmoney.com/",
    }
    r = requests.get(url, params=params, headers=headers, timeout=10)
    if r.status_code != 200:
        raise RuntimeError(f"eastmoney HTTP {r.status_code}")

    events = []
    data = r.json()
    items = data.get("data", {}).get("list", [])
    if not items:
        return events

    for item in items:
        title = (item.get("title_ch") or item.get("title") or "").strip()
        if not title:
            continue

        codes = item.get("codes", [])
        if not codes:
            continue

        # 公告分类 → 事件类型
        columns = item.get("columns", [])
        col_name = columns[0].get("column_name", "") if columns else ""

        etype = _em_column_to_type(col_name, title)
        impact = _em_impact(col_name)

        stock_codes = []
        for c in codes:
            code = c.get("stock_code", "")
            name = c.get("short_name", "")
            if code:
                stock_codes.append(code)

        if stock_codes:
            # 使用 display_time（精确到分钟），格式化为 MM-DD HH:MM
            raw_time = item.get("display_time", item.get("notice_date", ""))
            event_date = raw_time[:10] if raw_time else ""
            event_time = raw_time[11:16] if len(raw_time) > 11 else ""
            events.append({
                "date": event_date,
                "time": event_time,  # HH:MM
                "market": "A",
                "type": etype,
                "title": title[:100],
                "code": stock_codes[0],
                "codes": stock_codes,
                "source": "eastmoney",
                "impact": impact,
                "summary": f"{col_name} | {';'.join(stock_codes[:5])}",
            })
    return events


def fetch_eastmoney_news_detailed() -> tuple:
    """返回 (events, status): status ∈ ok/empty/unavailable"""
    try:
        events = _fetch_eastmoney_news_core()
        return events, ("ok" if events else "empty")
    except Exception as e:
        print(f"[market_events] eastmoney 失败: {e}")
        return [], "unavailable"


def fetch_eastmoney_news() -> List[Dict]:
    """从东方财富抓取A股最新公告/要闻 (失败返回 []; 状态区分用 detailed 变体)"""
    events, _ = fetch_eastmoney_news_detailed()
    return events


def _em_column_to_type(col_name: str, title: str) -> str:
    """东方财富公告栏目 → 事件类型"""
    col_lower = (col_name + title).lower()
    if any(k in col_lower for k in ["业绩", "预告", "快报", "年报", "季报", "分配"]):
        return "earnings"
    if any(k in col_lower for k in ["重组", "收购", "并购", "增发", "回购"]):
        return "earnings"
    if any(k in col_lower for k in ["合同", "中标", "项目"]):
        return "earnings"
    if any(k in col_lower for k in ["诉讼", "处罚", "监管", "问询"]):
        return "policy"
    if any(k in col_lower for k in ["调研", "投资", "合作"]):
        return "sector"
    return "sector"


def _em_impact(col_name: str) -> str:
    """东方财富公告类型 → 影响程度"""
    high_keywords = ["重组", "收购", "业绩", "处罚", "监管", "中标"]
    medium_keywords = ["合同", "增发", "回购", "调研", "项目"]
    col_lower = col_name.lower()
    if any(k in col_lower for k in high_keywords):
        return "high"
    if any(k in col_lower for k in medium_keywords):
        return "medium"
    return "medium"

def _classify_event(text: str) -> Optional[str]:
    """根据关键词分类事件类型（支持中英文，英文用词边界匹配）"""
    text_lower = text.lower()
    scores = {}
    for etype, config in EVENT_TYPES.items():
        # 中文关键词（子串匹配即可）
        cn_score = sum(1 for kw in config["keywords"] if kw.lower() in text_lower)
        # 英文关键词（词边界匹配防误伤）
        en_score = sum(1 for kw in config.get("en_keywords", [])
                      if _en_word_match(kw.lower(), text_lower))
        total = cn_score + en_score
        if total > 0:
            scores[etype] = total
    return max(scores, key=scores.get) if scores else None


def _en_word_match(keyword: str, text: str) -> bool:
    """英文关键词词边界匹配，防止 'cloud' 误匹配 'clouding'，支持简单复数"""
    import re
    words = keyword.split()
    for w in words:
        # 匹配: word / words / worded (排除 -ing 防止 cloud↔clouding 误伤)
        pattern = r'\b' + re.escape(w) + r'(?:s|es|ed)?\b'
        if re.search(pattern, text):
            return True
    return False


# ─── 事件→标的映射 ─────────────────────────────────

def map_events_to_stocks(events: List[Dict]) -> List[Dict]:
    """将事件映射到受影响的个股

    Returns:
        [{"event": dict, "stocks": [{"code": str, "name": str, "direction": "long/short", "reason": str}]}]
    """
    signals = []

    for event in events:
        etype = event.get("type", "")
        title = event.get("title", "")
        text = f"{title} {event.get('summary', '')}".lower()

        affected_stocks = []

        # 1. 直接从事件中提取代码
        if event.get("code"):
            affected_stocks.append({
                "code": event["code"],
                "name": "",
                "direction": _infer_direction(etype, text),
                "reason": f"{EVENT_TYPES.get(etype, {}).get('name', etype)}事件",
            })

        # 2. 行业映射：事件文本中含行业名才映射（不用全局事件类型关键词，防交叉污染）
        for sector, codes in SECTOR_STOCK_MAP.items():
            matched = False
            # 中文: 行业名本身出现在事件文本中
            if sector in text:
                matched = True
            # 英文: EN_SECTOR_MAP 词边界 → 对应行业名
            if not matched:
                for en_kw, cn_sector in EN_SECTOR_MAP.items():
                    if _en_word_match(en_kw, text) and cn_sector == sector:
                        matched = True
                        break
            if matched:
                for code in codes[:2]:  # 每行业最多2只（更精准）
                    if code not in [s["code"] for s in affected_stocks]:
                        affected_stocks.append({
                            "code": code,
                            "name": "",
                            "direction": _infer_direction(etype, text),
                            "reason": f"{sector}关联",
                        })

        # 3. Finnhub related stocks
        if event.get("codes"):
            for code in event["codes"][:3]:
                affected_stocks.append({
                    "code": code,
                    "name": "",
                    "direction": _infer_direction(etype, text),
                    "reason": "新闻关联标的",
                })

        if affected_stocks:
            # 内部去重：同一事件中同一代码只保留一次
            seen_codes = set()
            unique_stocks = []
            for st in affected_stocks:
                if st["code"] not in seen_codes:
                    seen_codes.add(st["code"])
                    unique_stocks.append(st)

            signals.append({
                "event": {
                    "date": event.get("date"),
                    "time": event.get("time", ""),
                    "type": etype,
                    "title": title[:80],
                    "impact": event.get("impact", "medium"),
                    "duration_days": EVENT_TYPES.get(etype, {}).get("duration_days", 7),
                },
                "stocks": unique_stocks[:5],
            })

    return signals


def _infer_direction(etype: str, text: str) -> str:
    """推断做多/做空方向（增强版：否定句处理 + 金融权重 + 财报特判）"""
    text_lower = text.lower()

    # ── 否定前缀检测 ──
    negation_patterns = [
        r"\bnot\s+\w+", r"\bno\s+\w+", r"\bnever\b", r"\bwithout\b",
        r"\bdespite\b", r"\b虽然\b", r"\b尽管\b", r"\b未能\b", r"\b不及预期\b",
        r"\b低于预期\b", r"\bmiss\b", r"\bbelow\b",
    ]

    # ── 正向词 + 权重 (金融语境) ──
    positive_weighted = {
        # 强正向 (权重3)
        "beat estimates": 3, "beat expectation": 3, "record high": 3,
        "创新高": 3, "超预期": 3, "大幅增长": 3, "暴涨": 3, "surge": 3,
        "breakthrough": 3, "利好": 3, "重大突破": 3,
        # 中正向 (权重2)
        "增长": 2, "growth": 2, "上升": 2, "rise": 2, "gain": 2,
        "upgrad": 2, "raised": 2, "上调": 2, "outperform": 2,
        "补贴": 2, "支持": 2, "subsidy": 2, "support": 2,
        "approve": 2, "通过": 2, "获批": 2,
        # 弱正向 (权重1)
        "advance": 1, "rally": 1, "boost": 1, "recovery": 1,
        "optimism": 1, "回升": 1, "反弹": 1, "稳定": 1,
    }

    # ── 负向词 + 权重 (金融语境) ──
    negative_weighted = {
        # 强负向 (权重3)
        "暴跌": 3, "崩盘": 3, "危机": 3, "crash": 3, "crisis": 3,
        "亏损": 3, "制裁": 3, "sanction": 3, "处罚": 3, "警告": 3,
        "下调": 3, "downgrad": 3, "layoff": 3, "裁员": 3,
        # 中负向 (权重2)
        "下降": 2, "下滑": 2, "decline": 2, "drop": 2, "fall": 2,
        "tension": 2, "conflict": 2, "冲突": 2, "war": 2,
        "penalty": 2, "fine": 2, "罚款": 2, "调查": 2, "investigation": 2,
        "slow": 2, "放缓": 2, "weak": 2, "疲软": 2,
        "tariff": 2, "关税": 2, "trade war": 2,
        # 弱负向 (权重1)
        "risk": 1, "concern": 1, "担忧": 1, "不确定性": 1,
        "volatility": 1, "波动": 1, "caution": 1,
    }

    pos_score = 0
    neg_score = 0
    pos_count = 0
    neg_count = 0

    for kw, weight in positive_weighted.items():
        if kw in text_lower:
            pos_count += 1
            # 检查是否被否定
            negated = any(_check_negation(text_lower, kw, pat) for pat in negation_patterns)
            if negated:
                neg_score += weight
            else:
                pos_score += weight

    for kw, weight in negative_weighted.items():
        if kw in text_lower:
            neg_count += 1
            negated = any(_check_negation(text_lower, kw, pat) for pat in negation_patterns)
            if negated:
                pos_count += 1
                pos_score += weight  # 否定负向 = 正向
            else:
                neg_score += weight

    # ── 财报事件特判 ──
    if etype == "earnings":
        if any(w in text_lower for w in ["beat", "超预期", "上调", "raised"]):
            pos_score += 3
        if any(w in text_lower for w in ["miss", "低于预期", "下滑", "下降"]):
            neg_score += 3

    if pos_score > neg_score:
        return "long"
    elif neg_score > pos_score:
        return "short"
    # 平局: 比较匹配到的关键词数量，更保守地偏short
    if neg_count > pos_count:
        return "short"
    elif pos_count > neg_count:
        return "long"
    return "long"


def _check_negation(text: str, keyword: str, negation_pat: str) -> bool:
    """检查关键词是否被否定词修饰（前后15字符窗口）"""
    import re
    idx = text.find(keyword)
    if idx < 0:
        return False
    window_start = max(0, idx - 20)
    window_end = min(len(text), idx + len(keyword) + 10)
    window = text[window_start:window_end]
    return bool(re.search(negation_pat, window))


# ─── 统一获取 + 生成信号 ──────────────────────────

_SOURCE_STATUS_LABELS = {
    "ok": "正常",
    "empty": "正常·空",
    "unavailable": "不可用",
    "disabled": "禁用",
}


def _source_entry(status: str, count: int, reason: str = None, detail=None) -> Dict:
    """sources_status 条目: status 四态 + count; ok 仅表「抓取成功」(ok/empty),
    不可用/禁用绝不允许记 ok (2026-10-27 验收返修: 此前失败返回 [] 仍记 ok=True)"""
    entry = {
        "status": status,
        "label": _SOURCE_STATUS_LABELS.get(status, status),
        "count": count,
        "ok": status in ("ok", "empty"),
    }
    if reason:
        entry["reason"] = reason
    if detail:
        entry["detail"] = detail
    return entry


def scan_market_events() -> Dict:
    """全市场事件扫描 → 生成交易信号"""
    all_events = []
    sources_status = {}

    # A股事件 - 巨潮资讯
    cn_events, cn_st = _safe_fetch("cninfo", fetch_cn_events_detailed)
    all_events.extend(cn_events)
    sources_status["cninfo"] = _source_entry(cn_st, len(cn_events))

    # A股事件 - 东方财富公告
    em_events, em_st = _safe_fetch("eastmoney", fetch_eastmoney_news_detailed)
    all_events.extend(em_events)
    sources_status["eastmoney"] = _source_entry(em_st, len(em_events))

    # A股新闻快讯 - 财联社/东财7×24 (免 key)
    news_events, news_st, news_sub = fetch_flash_news_detailed()
    all_events.extend(news_events)
    sources_status["flash_news"] = _source_entry(news_st, len(news_events), detail=news_sub)

    # 美股新闻 - Finnhub (无凭据 → disabled, 不发请求)
    if not os.getenv("FINNHUB_KEY", "").strip():
        sources_status["finnhub"] = _source_entry(
            "disabled", 0, reason="FINNHUB_KEY 未配置 (本机不配置付费源密钥)")
    else:
        us_events, fn_st = _safe_fetch("finnhub", fetch_us_events_detailed)
        all_events.extend(us_events)
        sources_status["finnhub"] = _source_entry(fn_st, len(us_events))

    # 美股财报日历 - yfinance (库缺失/real模式白名单外 → disabled, 不发请求)
    yf_reason = _yfinance_disabled_reason()
    if yf_reason:
        sources_status["yfinance"] = _source_entry("disabled", 0, reason=yf_reason)
    else:
        yf_events, yf_st = _safe_fetch("yfinance", fetch_yfinance_earnings_detailed)
        all_events.extend(yf_events)
        sources_status["yfinance"] = _source_entry(yf_st, len(yf_events))

    # 去重
    seen = set()
    unique = []
    for e in all_events:
        key = e.get("title", "")[:60]
        if key not in seen:
            seen.add(key)
            unique.append(e)

    # 保存
    _save_events(unique)

    # 映射到标的（保留 market 字段以便前端区分 A股/美股）
    signals = map_events_to_stocks(unique)
    # 给 signal 的 event 补上 market 字段
    for sig in signals:
        evt_title = sig["event"]["title"]
        original = next((e for e in unique if e.get("title","")[:50] == evt_title[:50]), None)
        if original:
            sig["event"]["market"] = original.get("market", "")
            sig["event"]["source"] = original.get("source", "")
            sig["event"]["time"] = original.get("time", "")

    # ── 跨事件热度统计 + 结论生成 ──
    code_event_count = {}
    code_event_types = {}  # {code: {etype: count, direction: "long"/"short"}}
    # 生成结论文本
    def _type_label_from_title(title: str, sig_type: str) -> str:
        """从标题关键词推断更精细的类型标签"""
        if "摘帽" in title or "撤销" in title:
            return "摘帽"
        if any(k in title for k in ["重组", "收购", "并购", "购买资产"]):
            return "重组"
        if any(k in title for k in ["中标", "合同", "签订"]):
            return "合同"
        if any(k in title for k in ["业绩", "预告", "快报", "年报", "季报", "扭亏"]):
            return "业绩"
        if any(k in title for k in ["监管", "问询", "处罚", "诉讼"]):
            return "监管"
        return {"earnings": "财报", "policy": "政策", "geopolitical": "地缘",
                "sector": "行业", "macro": "宏观", "commodity": "商品"}.get(sig_type, sig_type)

    for sig in signals:
        evt_type = sig["event"].get("type", "其他")
        evt_title = sig["event"].get("title", "")
        type_label = _type_label_from_title(evt_title, evt_type)
        direction = sig["stocks"][0]["direction"] if sig.get("stocks") else "long"
        for st in sig.get("stocks", []):
            code = st.get("code", "")
            code_event_count[code] = code_event_count.get(code, 0) + 1
            if code not in code_event_types:
                code_event_types[code] = {"types": {}, "long": 0, "short": 0}
            code_event_types[code]["types"][type_label] = code_event_types[code]["types"].get(type_label, 0) + 1
            code_event_types[code][direction] = code_event_types[code].get(direction, 0) + 1

    # 生成结论文本
    def _build_conclusion(et: dict) -> str:
        types = et.get("types", {})
        long_n = et.get("long", 0)
        short_n = et.get("short", 0)
        # 事件类型排行
        type_items = sorted(types.items(), key=lambda x: -x[1])[:3]
        type_str = " ".join(f"{t}×{c}" for t, c in type_items)
        # 方向结论
        if long_n > short_n * 2:
            dir_str = "强做多"
        elif long_n > short_n:
            dir_str = "偏多"
        elif short_n > long_n * 2:
            dir_str = "强做空"
        elif short_n > long_n:
            dir_str = "偏空"
        else:
            dir_str = "中性"
        return f"{type_str} → {dir_str}"

    for sig in signals:
        for st in sig.get("stocks", []):
            code = st["code"]
            st["hit_count"] = code_event_count.get(code, 1)
            st["conclusion"] = _build_conclusion(code_event_types.get(code, {"types": {}, "long": 0, "short": 0}))
    # 热点标的摘要 (≥2事件)
    hot_stocks = [(c, n) for c, n in code_event_count.items() if n >= 2]
    hot_stocks.sort(key=lambda x: -x[1])

    # 前端展示：摘帽(2) + 热点标的(2) + 其他cn(4) + 东财(4) + US(4) + 财报(4)
    cn_signals = [s for s in signals if s["event"].get("source") in (None, "", "cninfo")]
    cn_signals.sort(key=lambda s: (0 if s["event"].get("impact") == "high" else 1, s["event"].get("title", "")))
    # 摘帽事件
    st_signals = [s for s in cn_signals if "撤销" in s["event"].get("title", "") or "摘帽" in s["event"].get("title", "")]
    # 热点标的（≥3事件）信号优先
    hot_codes = set(c for c, n in hot_stocks[:5])
    hot_signals = [s for s in cn_signals if s not in st_signals and any(
        st.get("code") in hot_codes for st in s.get("stocks", []))]
    other_cn = [s for s in cn_signals if s not in st_signals and s not in hot_signals]
    cn_display = st_signals[:2] + hot_signals[:2] + other_cn[:4]
    em_signals = [s for s in signals if s["event"].get("source") == "eastmoney"]
    news_signals = [s for s in signals if s["event"].get("source") in ("cls", "eastmoney724")]
    news_signals.sort(key=lambda s: (0 if s["event"].get("impact") == "high" else 1,
                                     s["event"].get("time", "")), reverse=False)
    us_signals = [s for s in signals if s["event"].get("market") == "US" and s["event"].get("source") != "yfinance"]
    yf_signals = [s for s in signals if s["event"].get("source") == "yfinance"]
    display_signals = cn_display + em_signals[:4] + news_signals[:6] + us_signals[:4] + yf_signals[:4]

    # 统计
    long_count = sum(1 for s in signals for st in s["stocks"] if st["direction"] == "long")
    short_count = sum(1 for s in signals for st in s["stocks"] if st["direction"] == "short")

    # 推送告警
    if signals:
        _push_event_alerts(signals)

    return {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "events": unique[:20],
        "signals": display_signals,
        "sources_status": sources_status,
        "hot_stocks": [{"code": c, "count": n} for c, n in hot_stocks[:8]],
        "summary": {
            "total_events": len(unique),
            "affected_stocks": len(signals),
            "long_signals": long_count,
            "short_signals": short_count,
        },
    }


def _push_event_alerts(signals: List[Dict]):
    """将重大事件信号推送到消息队列"""
    try:
        from message_queue import enqueue
        high_impact = [s for s in signals if s["event"].get("impact") == "high"]
        if high_impact:
            lines = [f"📰 市场事件告警 ({len(high_impact)}条高影响)", ""]
            for s in high_impact[:3]:
                lines.append(f"• {s['event']['title']}")
                for st in s["stocks"][:3]:
                    direction = "📈做多" if st["direction"] == "long" else "📉做空"
                    lines.append(f"  {direction} {st['code']}: {st['reason']}")
            enqueue("市场事件告警", "\n".join(lines), priority="high")
    except Exception:
        pass
