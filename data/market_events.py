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

def fetch_cn_events() -> List[Dict]:
    """从巨潮资讯抓取A股公告/政策事件"""
    events = []
    try:
        from data.cninfo_fetcher import fetch_monthly_catalysts
        today = datetime.now().strftime("%Y-%m-%d")
        catalysts = fetch_monthly_catalysts(datetime.now().strftime("%Y%m"))

        if isinstance(catalysts, dict):
            # cninfo_fetcher 返回 {"events": [...], "total": N, "period": "..."}
            evt_list = catalysts.get("events", [])
            for item in evt_list:
                if isinstance(item, dict):
                    code = item.get("sec_code", "")
                    # 补全6位代码格式
                    if len(code) == 5:
                        code = "0" + code
                    events.append({
                        "date": item.get("date", today),
                        "market": "A",
                        "type": _cninfo_type_to_event_type(item.get("type", "")),
                        "title": item.get("title", "公告事件"),
                        "code": code,
                        "source": "cninfo",
                        "impact": "medium",
                    })
    except Exception:
        pass
    return events


def _cninfo_type_to_event_type(cninfo_type: str) -> str:
    """巨潮资讯类型 → 事件类型映射"""
    mapping = {
        "C3_重大合同": "earnings",
        "C5_资产重组": "earnings",
        "C7_业绩超预期": "earnings",
    }
    return mapping.get(cninfo_type, "sector")


# ─── 美股事件抓取 (Finnhub) ─────────────────────────

def fetch_us_events() -> List[Dict]:
    """从 Finnhub 抓取美股新闻"""
    events = []
    try:
        import requests
        finnhub_key = os.getenv("FINNHUB_KEY", "")
        if not finnhub_key:
            return events

        today = datetime.now().strftime("%Y-%m-%d")
        url = f"https://finnhub.io/api/v1/news?category=general&token={finnhub_key}"
        r = requests.get(url, timeout=10)
        if r.status_code != 200:
            return events

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
    except Exception:
        pass
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


def fetch_yfinance_earnings() -> List[Dict]:
    """从 yfinance 抓取未来30天美股财报日历"""
    events = []
    try:
        import yfinance as yf
        from datetime import date, timedelta

        today = date.today()
        cutoff = today + timedelta(days=30)

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
    except Exception:
        pass
    return events


# ─── 超时保护 ──────────────────────────────────

import concurrent.futures

_FETCH_TIMEOUT = {
    "cninfo": 20,
    "eastmoney": 10,
    "finnhub": 15,
    "yfinance": 120,
}


def _safe_fetch(name: str, fn, timeout: int = None) -> tuple:
    """带超时的安全抓取，返回 (events, ok)"""
    if timeout is None:
        timeout = _FETCH_TIMEOUT.get(name, 10)
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(fn)
            events = future.result(timeout=timeout)
            return (events, True)
    except concurrent.futures.TimeoutError:
        print(f"[market_events] {name} 超时 ({timeout}s)")
        return ([], False)
    except Exception as e:
        print(f"[market_events] {name} 失败: {e}")
        return ([], False)


# ─── A股公告抓取 (东方财富) ──────────────────────

def fetch_eastmoney_news() -> List[Dict]:
    """从东方财富抓取A股最新公告/要闻"""
    events = []
    try:
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
            return events

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
    except Exception:
        pass
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

        # 2. 行业映射（中英文关键词 → 板块 → 股票）
        for sector, codes in SECTOR_STOCK_MAP.items():
            matched = False
            # 中文: 板块名 or 事件类型关键词
            if sector in text or any(kw in text for kw in EVENT_TYPES.get(etype, {}).get("keywords", [])):
                matched = True
            # 英文: EN_SECTOR_MAP 词边界匹配
            if not matched:
                for en_kw, cn_sector in EN_SECTOR_MAP.items():
                    if _en_word_match(en_kw, text) and cn_sector == sector:
                        matched = True
                        break
            if matched:
                for code in codes[:3]:  # 每行业最多3只
                    if code not in [s["code"] for s in affected_stocks]:
                        affected_stocks.append({
                            "code": code,
                            "name": "",
                            "direction": _infer_direction(etype, text),
                            "reason": f"{sector}{'利好' if '利好' in title or 'gain' in text or 'rise' in text else '影响'}",
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
            signals.append({
                "event": {
                    "date": event.get("date"),
                    "time": event.get("time", ""),  # HH:MM, 东方财富精确到分钟
                    "type": etype,
                    "title": title[:80],
                    "impact": event.get("impact", "medium"),
                    "duration_days": EVENT_TYPES.get(etype, {}).get("duration_days", 7),
                },
                "stocks": affected_stocks[:5],
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

def scan_market_events() -> Dict:
    """全市场事件扫描 → 生成交易信号"""
    all_events = []
    sources_status = {}

    # A股事件 - 巨潮资讯
    cn_events, cn_ok = _safe_fetch("cninfo", fetch_cn_events)
    all_events.extend(cn_events)
    sources_status["cninfo"] = {"ok": cn_ok, "count": len(cn_events)}

    # A股事件 - 东方财富公告
    em_events, em_ok = _safe_fetch("eastmoney", fetch_eastmoney_news)
    all_events.extend(em_events)
    sources_status["eastmoney"] = {"ok": em_ok, "count": len(em_events)}

    # 美股新闻 - Finnhub
    us_events, fn_ok = _safe_fetch("finnhub", fetch_us_events)
    all_events.extend(us_events)
    sources_status["finnhub"] = {"ok": fn_ok, "count": len(us_events)}

    # 美股财报日历 - yfinance
    yf_events, yf_ok = _safe_fetch("yfinance", fetch_yfinance_earnings)
    all_events.extend(yf_events)
    sources_status["yfinance"] = {"ok": yf_ok, "count": len(yf_events)}

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

    # 前端展示：CN巨潮8 + 东财4 + US新闻4 + 财报4（确保四大数据源都有露出）
    cn_signals = [s for s in signals if s["event"].get("source") in (None, "", "cninfo")]
    em_signals = [s for s in signals if s["event"].get("source") == "eastmoney"]
    us_signals = [s for s in signals if s["event"].get("market") == "US" and s["event"].get("source") != "yfinance"]
    yf_signals = [s for s in signals if s["event"].get("source") == "yfinance"]
    display_signals = cn_signals[:8] + em_signals[:4] + us_signals[:4] + yf_signals[:4]

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
