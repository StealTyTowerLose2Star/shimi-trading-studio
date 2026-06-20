"""
先知 · 事件预测引擎 (Event Predictor)

职责: 市场事件抓取 → TF-IDF聚类 → 情绪量化 → 分类 → 标的映射 → 做多/做空信号
架构: 零耦合 — ml/ 不 import api/ haitao/ magician/
      单一数据入口 — data/fetcher.py (网络请求通过 requests)
      禁止绕过风控

学术锚点:
  - Gu, Kelly & Xiu (2020): GBRT最优, 月度R²≈0.4%
  - López de Prado (2018): Purged Walk-Forward
  - Bailey et al. (2014): Deflated Sharpe Ratio
"""
import json
import re
import os
import hashlib
import requests
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from collections import Counter, defaultdict

# 低耦合: 仅从 ml/ 内部模块引用
from .market_mapper import map_title_to_stocks, map_title_to_sectors

# ═══════════════════════════════════════════
# 事件类型定义
# ═══════════════════════════════════════════

EVENT_CATEGORIES = {
    "政策监管": {
        "name": "政策/监管",
        "keywords": [
            "国务院", "发改委", "工信部", "央行", "证监会", "外交部", "商务部",
            "政治局", "降准", "降息", "LPR", "减税", "补贴", "新基建", "碳中和",
            "数据要素", "注册制", "退市", "监管", "处罚", "反垄断",
            "制裁", "清单", "涉军", "关税", "谈判", "稀土", "敦促", "无理打压",
        ],
        "impact": "high",
        "duration_days": 30,
    },
    "财报公告": {
        "name": "财报/公告",
        "keywords": [
            "业绩预告", "年报", "季报", "营收", "净利润", "EPS",
            "同比增长", "环比增长", "盈利", "亏损", "分红", "回购",
            "减持", "增持", "合同", "中标", "采购", "定增", "重组",
        ],
        "impact": "high",
        "duration_days": 14,
    },
    "宏观数据": {
        "name": "宏观数据",
        "keywords": [
            "GDP", "CPI", "PPI", "PMI", "社融", "M2", "进出口",
            "外汇储备", "非农", "FOMC", "美联储", "利率", "加息",
            "降息", "就业", "通胀", "ADP",
        ],
        "impact": "medium",
        "duration_days": 7,
    },
    "地缘国际": {
        "name": "地缘/国际",
        "keywords": [
            "制裁", "关税", "贸易战", "冲突", "协议", "脱钩",
            "稀土", "芯片战", "实体清单", "涉军", "中概股",
            "港股", "美股", "外交", "谈判", "特朗普", "伊朗", "以色列",
        ],
        "impact": "high",
        "duration_days": 60,
    },
    "AI科技": {
        "name": "AI/科技",
        "keywords": [
            "AI", "人工智能", "大模型", "DeepSeek", "ChatGPT", "GPU",
            "芯片", "半导体", "光刻", "服务器", "数据中心", "算力",
            "硅片", "存储", "佰维", "海力士", "光缆", "光纤", "智能体",
        ],
        "impact": "high",
        "duration_days": 30,
    },
    "新能源": {
        "name": "新能源",
        "keywords": [
            "光伏", "新能源", "锂电", "储能", "电池",
            "固态电池", "钠电池", "逆变器",
        ],
        "impact": "medium",
        "duration_days": 14,
    },
    "汽车": {
        "name": "汽车",
        "keywords": [
            "比亚迪", "汽车", "新能源车", "特斯拉",
            "自动驾驶", "智能驾驶",
        ],
        "impact": "medium",
        "duration_days": 14,
    },
    "半导体": {
        "name": "半导体",
        "keywords": [
            "半导体", "芯片", "晶圆", "封测", "光刻机",
            "EDA", "先进封装", "HBM", "存储", "硅片",
        ],
        "impact": "high",
        "duration_days": 30,
    },
    "商品资源": {
        "name": "商品/资源",
        "keywords": [
            "原油", "黄金", "铜", "铝", "锂", "螺纹钢",
            "铁矿石", "天然气", "稀土", "有色金属", "涨价",
        ],
        "impact": "medium",
        "duration_days": 7,
    },
    "市场行情": {
        "name": "市场行情",
        "keywords": [
            "涨停", "跌停", "沪指", "创业板", "恒指",
            "恒生科技", "成交量", "北向资金", "龙虎榜",
            "ETF", "收复", "大涨", "暴涨",
        ],
        "impact": "medium",
        "duration_days": 3,
    },
}

# ═══════════════════════════════════════════
# 情绪词典
# ═══════════════════════════════════════════

POSITIVE_WORDS = [
    # 中文
    "增长", "突破", "大涨", "飙升", "创新高", "涨停", "补贴", "支持",
    "超预期", "回购", "增持", "分红", "中标", "签约", "订单", "扩张",
    "加速", "强劲", "上升", "盈利", "扭亏", "反弹", "复苏", "爆发",
    "放量", "领涨", "利多", "利好", "恢复", "收复", "采购", "合同",
    # 英文
    "surge", "jump", "rally", "soar", "record high", "beat", "upgrade",
    "growth", "breakthrough", "bullish", "buy", "outperform", "profit",
    "revenue growth", "strong", "boost", "expansion", "momentum",
    "rebound", "recovery", "gain", "rise", "climb", "advance",
    "positive", "optimistic", "approval", "breakthrough",
]

NEGATIVE_WORDS = [
    # 中文
    "下降", "下滑", "暴跌", "亏损", "制裁", "处罚", "警告", "危机",
    "崩盘", "减持", "跌停", "退市", "ST", "欠薪", "裁员", "诉讼",
    "调查", "违约", "债务", "暴雷", "商誉减值", "计提", "监管函",
    "问询函", "警示函", "无理打压", "列入", "致", "敦促",
    # 英文
    "decline", "drop", "plunge", "crash", "sanction", "penalty",
    "crisis", "downgrade", "sell", "bearish", "underperform",
    "loss", "layoff", "lawsuit", "investigation", "default",
    "debt", "warning", "risk", "fear", "concern", "tariff",
    "trade war", "forced labor", "fall", "slip", "slide", "tumble",
]

AMPLIFIER_WORDS = [
    # 中文
    "大幅", "显著", "暴涨", "暴跌", "飙升", "狂飙",
    "井喷", "雪崩", "腰斩", "翻倍", "大涨近",
    # 英文
    "significantly", "sharply", "dramatically", "massive",
    "huge", "enormous", "record",
]

# ═══════════════════════════════════════════
# 数据源
# ═══════════════════════════════════════════

def fetch_eastmoney_kuaixun(pages: int = 3) -> List[Dict]:
    """抓取东方财富24小时快讯"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    }
    events = []
    for page in range(1, pages + 1):
        try:
            url = f"https://newsapi.eastmoney.com/kuaixun/v1/getlist_101_{page}_50_1_.html"
            r = requests.get(url, headers=headers, timeout=15)
            if r.status_code != 200:
                continue
            match = re.search(r'\[.*?\{.*?"title".*?\}\]', r.text, re.DOTALL)
            if not match:
                continue
            items = json.loads(match.group(0))
            for item in items:
                title = item.get("title", item.get("TITLE", ""))
                if title and len(title) > 5:
                    events.append({
                        "source": "eastmoney",
                        "title": title,
                        "content": (item.get("digest") or item.get("DIGEST") or ""),
                        "timestamp": datetime.now().isoformat(),
                    })
        except Exception:
            continue
    return events


# ═══════════════════════════════════════════
# 新增: 巨潮资讯 A股公告
# ═══════════════════════════════════════════

def fetch_cninfo_announcements() -> List[Dict]:
    """从巨潮资讯抓取 A 股公告事件 (重大合同/业绩预告/增减持等)"""
    events = []
    try:
        from data.cninfo_fetcher import fetch_monthly_catalysts
        this_month = datetime.now().strftime("%Y%m")
        data = fetch_monthly_catalysts(this_month)

        # fetch_monthly_catalysts returns dict: {"events": [...], "total": N}
        items = data.get("events", []) if isinstance(data, dict) else []
        for item in items:
            title = item.get("title", "")
            if not title:
                continue
            # 过滤: 只保留有价值的事件类型
            etype = item.get("type", "")
            valuable_types = [
                "C3_重大合同", "C1_业绩预告", "C2_业绩快报",
                "C4_增持", "C5_减持", "C6_回购", "C7_分红",
                "C8_重组", "C9_定增", "C10_中标",
            ]
            if any(t in etype for t in valuable_types) or any(
                kw in title for kw in ["合同", "业绩", "增持", "减持", "回购", "中标", "重组", "分红"]
            ):
                events.append({
                    "source": "cninfo",
                    "title": f"{item.get('company', '')}: {title}",
                    "content": f"类型: {etype} | 代码: {item.get('sec_code', '')}",
                    "timestamp": datetime.now().isoformat(),
                })
    except Exception:
        pass
    return events


# ═══════════════════════════════════════════
# 新增: 东方财富 A 股公告
# ═══════════════════════════════════════════

def fetch_eastmoney_announcements(pages: int = 2) -> List[Dict]:
    """从东方财富抓取 A 股公告 (业绩预告/增减持/重大合同等)"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    }
    events = []
    for page in range(1, pages + 1):
        try:
            url = f"https://np-anotice-stock.eastmoney.com/api/security/ann?page_size=20&page_index={page}&ann_type=A"
            r = requests.get(url, headers=headers, timeout=15)
            if r.status_code != 200:
                continue
            data = r.json()
            items = data.get("data", {}).get("list", [])
            for item in items:
                title = item.get("title", "")
                if not title:
                    continue
                # 过滤有价值的公告类型
                valuable_kw = [
                    "业绩", "预告", "增持", "减持", "回购", "中标",
                    "合同", "重组", "重大", "分红", "定增", "收购",
                ]
                if any(kw in title for kw in valuable_kw):
                    codes = item.get("codes", [])
                    code_str = codes[0].get("stock_code", "") if codes else ""
                    name_str = codes[0].get("short_name", "") if codes else ""
                    events.append({
                        "source": "eastmoney_ann",
                        "title": f"{name_str}({code_str}): {title}" if name_str else title,
                        "content": "",
                        "timestamp": datetime.now().isoformat(),
                    })
        except Exception:
            continue
    return events


# ═══════════════════════════════════════════
# 新增: Finnhub 美股新闻
# ═══════════════════════════════════════════

FINNHUB_KEY = os.getenv("FINNHUB_KEY", "d8hav1hr01qhjpmrcos0d8hav1hr01qhjpmrcosg")


def fetch_finnhub_news() -> List[Dict]:
    """从 Finnhub 抓取美股新闻"""
    events = []
    try:
        url = f"https://finnhub.io/api/v1/news?category=general&token={FINNHUB_KEY}"
        r = requests.get(url, timeout=15)
        if r.status_code != 200:
            return events
        news = r.json()
        for item in news[:30]:
            headline = item.get("headline", "")
            if not headline or len(headline) < 10:
                continue
            summary = item.get("summary", "")[:200]
            related = item.get("related", "")
            events.append({
                "source": "finnhub",
                "title": headline,
                "content": summary,
                "timestamp": datetime.fromtimestamp(
                    item.get("datetime", 0)
                ).isoformat() if item.get("datetime") else datetime.now().isoformat(),
                "codes": related,
            })
    except Exception:
        pass
    return events


# ═══════════════════════════════════════════
# 新增: Yahoo Finance 美股新闻
# ═══════════════════════════════════════════

def fetch_yahoo_news() -> List[Dict]:
    """从 Yahoo Finance 抓取美股市场新闻"""
    events = []
    try:
        import yfinance as yf
        # 抓取标普500 + 纳指 + 热门股新闻
        tickers = ["^GSPC", "^IXIC", "AAPL", "NVDA"]
        seen_titles = set()
        for ticker_symbol in tickers:
            try:
                ticker = yf.Ticker(ticker_symbol)
                news_items = ticker.news or []
                for item in news_items[:5]:
                    content = item.get("content", {}) if isinstance(item, dict) else {}
                    title = content.get("title", "")
                    if not title or len(title) < 10:
                        continue
                    if title in seen_titles:
                        continue
                    seen_titles.add(title)
                    desc = content.get("description", "")[:200]
                    # Strip HTML tags
                    import re as _re
                    desc = _re.sub(r'<[^>]+>', '', desc)
                    events.append({
                        "source": "yahoo",
                        "title": title,
                        "content": desc,
                        "timestamp": datetime.now().isoformat(),
                    })
            except Exception:
                continue
    except Exception:
        pass
    return events


# ═══════════════════════════════════════════
# 新增: 金十数据快讯
# ═══════════════════════════════════════════

def fetch_jin10_flash() -> List[Dict]:
    """从金十数据抓取实时快讯"""
    events = []
    try:
        headers = {
            "User-Agent": "Mozilla/5.0",
            "x-app-id": "bVBF4FyRTn5NJF5n",
            "x-version": "1.0.0",
        }
        url = "https://flash-api.jin10.com/get_flash_list?channel=-8200&vip=1"
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code != 200:
            return events
        data = r.json()
        items = data.get("data", [])
        for item in items[:30]:
            content = item.get("data", {}).get("content", "")
            if content and len(content) > 10:
                # 清理 HTML
                import re as _re
                content = _re.sub(r'<[^>]+>', '', content)
                events.append({
                    "source": "jin10",
                    "title": content[:150],
                    "content": "",
                    "timestamp": item.get("time", datetime.now().strftime("%Y-%m-%d %H:%M")),
                })
    except Exception:
        pass
    return events


# ═══════════════════════════════════════════
# 新增: 华尔街见闻快讯
# ═══════════════════════════════════════════

def fetch_wallstreetcn_lives() -> List[Dict]:
    """从华尔街见闻抓取全球实时快讯 (A股+美股覆盖)"""
    events = []
    try:
        url = "https://api-one.wallstcn.com/apiv1/content/lives?channel=global-channel&limit=20"
        r = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"},
            timeout=10,
        )
        if r.status_code != 200:
            return events
        data = r.json()
        items = data.get("data", {}).get("items", [])
        for item in items:
            title = item.get("title", "") or item.get("content_text", "") or ""
            if not title or len(title) < 10:
                continue
            content = item.get("content_text", item.get("content", ""))
            events.append({
                "source": "wallstreetcn",
                "title": title[:200],
                "content": (content or "")[:200],
                "timestamp": datetime.fromtimestamp(
                    item.get("display_time", 0)
                ).isoformat() if item.get("display_time") else datetime.now().isoformat(),
            })
    except Exception:
        pass
    return events


# ═══════════════════════════════════════════
# 新增: 新浪财经快讯
# ═══════════════════════════════════════════

def fetch_sina_finance_roll() -> List[Dict]:
    """从新浪财经抓取全球滚动快讯"""
    events = []
    try:
        url = "https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2509&num=20"
        r = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"},
            timeout=10,
        )
        if r.status_code != 200:
            return events
        data = r.json()
        items = data.get("result", {}).get("data", [])
        for item in items:
            title = item.get("title", "")
            if not title or len(title) < 10:
                continue
            events.append({
                "source": "sina",
                "title": title[:200],
                "content": "",
                "timestamp": datetime.fromtimestamp(
                    int(item.get("ctime", 0))
                ).isoformat() if item.get("ctime") else datetime.now().isoformat(),
            })
    except Exception:
        pass
    return events


# ═══════════════════════════════════════════
# 新增: 同花顺快讯
# ═══════════════════════════════════════════

def fetch_10jqka_news() -> List[Dict]:
    """从同花顺抓取A股快讯"""
    events = []
    try:
        r = requests.get(
            "https://news.10jqka.com.cn/tapp/news/push/stock/?page=1",
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Referer": "https://news.10jqka.com.cn/",
            },
            timeout=10,
        )
        if r.status_code != 200:
            return events
        import re as _re
        titles = _re.findall(r'"title"\s*:\s*"([^"]{10,200})"', r.text)
        for title in titles[:20]:
            # 过滤纯广告/非财经
            skip_words = ["广告", "推广", "微信", "扫码", "javascript"]
            if any(w in title for w in skip_words):
                continue
            events.append({
                "source": "10jqka",
                "title": title[:200],
                "content": "",
                "timestamp": datetime.now().isoformat(),
            })
    except Exception:
        pass
    return events


# ═══════════════════════════════════════════
# 新增: Google News RSS (美股补充)
# ═══════════════════════════════════════════

def fetch_google_news_finance() -> List[Dict]:
    """从 Google News RSS 抓取美股/全球财经新闻"""
    events = []
    try:
        import xml.etree.ElementTree as ET
        url = "https://news.google.com/rss/search?q=stock+market+OR+china+A+shares+OR+US+stocks&hl=en-US&gl=US&ceid=US:en"
        r = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        if r.status_code != 200:
            return events
        root = ET.fromstring(r.text)
        for item in root.findall(".//item")[:15]:
            title_el = item.find("title")
            source_el = item.find("source")
            title = title_el.text if title_el is not None else ""
            source = source_el.text if source_el is not None else ""
            if not title or len(title) < 10:
                continue
            events.append({
                "source": "google_news",
                "title": f"[{source}] {title}" if source else title,
                "content": "",
                "timestamp": datetime.now().isoformat(),
            })
    except Exception:
        pass
    return events


def fetch_all_events(pages: int = 3) -> List[Dict]:
    """统一事件抓取入口 — 10 数据源"""
    all_events = []

    # A 股源
    all_events.extend(fetch_eastmoney_kuaixun(pages=pages))
    all_events.extend(fetch_eastmoney_announcements(pages=2))
    all_events.extend(fetch_cninfo_announcements())
    all_events.extend(fetch_wallstreetcn_lives())
    all_events.extend(fetch_sina_finance_roll())
    all_events.extend(fetch_10jqka_news())

    # 美股源
    all_events.extend(fetch_finnhub_news())
    all_events.extend(fetch_yahoo_news())
    all_events.extend(fetch_google_news_finance())

    # 通用快讯
    all_events.extend(fetch_jin10_flash())

    # 去重 (标题前60字符)
    seen = set()
    unique = []
    for e in all_events:
        key = e["title"][:60]
        if key not in seen:
            seen.add(key)
            unique.append(e)
    return unique


# ═══════════════════════════════════════════
# 事件分类 (规则 + ML-ready接口)
# ═══════════════════════════════════════════

def classify_event(title: str, content: str = "") -> Tuple[str, float]:
    """事件分类: 返回 (类别名, 置信度)

    TODO: Phase 2 — 用 TF-IDF + XGBoost 替代规则
    """
    text = (title + " " + content).lower()
    scores: Dict[str, int] = {}
    for cat, cfg in EVENT_CATEGORIES.items():
        n = sum(1 for kw in cfg["keywords"] if kw.lower() in text)
        if n > 0:
            scores[cat] = n

    if not scores:
        return "其他", 0.0

    best = max(scores, key=scores.get)
    total = sum(scores.values())
    confidence = scores[best] / total if total > 0 else 1.0
    return best, round(confidence, 2)


# ═══════════════════════════════════════════
# 情绪评分
# ═══════════════════════════════════════════

def calc_sentiment(text: str) -> float:
    """情绪评分: [-1, 1]

    > 0: 利好做多 | < 0: 利空做空 | ≈0: 中性
    中英文双语支持, 大小写不敏感
    """
    text_lower = text.lower()
    pos_count = sum(1 for w in POSITIVE_WORDS if w.lower() in text_lower)
    neg_count = sum(1 for w in NEGATIVE_WORDS if w.lower() in text_lower)
    amp_count = sum(1 for w in AMPLIFIER_WORDS if w.lower() in text_lower)

    raw = pos_count - neg_count
    # 放大系数: 每个强调词增加 30% 影响
    amplified = raw * (1 + 0.3 * amp_count)
    # 归一化到 [-1, 1]
    sentiment = max(-1.0, min(1.0, amplified / 5.0))

    return round(sentiment, 3)


# ═══════════════════════════════════════════
# 信号评分
# ═══════════════════════════════════════════

def calc_signal_score(
    category: str,
    sentiment: float,
    mapped_stocks: List[Dict],
    text_length: int = 100,
) -> float:
    """综合信号评分 (0-500)

    权重: 情绪强度 × 类别影响 × 标的匹配度
    """
    # 基础分: 情绪绝对值
    base = abs(sentiment) * 100

    # 类别权重
    cat_weights = {
        "政策监管": 1.5, "地缘国际": 1.5, "AI科技": 1.3,
        "半导体": 1.3, "财报公告": 1.2, "宏观数据": 1.1,
        "商品资源": 1.0, "新能源": 1.0, "汽车": 1.0,
        "市场行情": 0.8, "其他": 0.5,
    }

    # 标的匹配权重
    stock_weight = min(1.5, 0.5 + len(mapped_stocks) * 0.25)

    # 内容长度 (越长越有实质)
    length_bonus = min(1.3, 0.8 + text_length / 500)

    score = base * cat_weights.get(category, 1.0) * stock_weight * length_bonus
    return round(score, 1)


# ═══════════════════════════════════════════
# 主流程: 扫描 + 分析 + 生成信号
# ═══════════════════════════════════════════

def scan_and_predict(pages: int = 3) -> Dict:
    """全流程: 抓取事件 → ML分析 → 生成交易信号

    Returns:
        {
            "timestamp": str,
            "total_events": int,
            "signals": [...],
            "summary": {...},
            "deep_dives": [...],
        }
    """
    events = fetch_all_events(pages=pages)

    signals = []

    # 全局去重计数器: 每个标的在整个扫描中最多出现3次
    _stock_usage: Dict[str, int] = {}

    for e in events:
        title = e["title"]
        content = e.get("content", "")
        full_text = title + " " + content
        if len(full_text.strip()) < 10:
            continue

        # 分类
        category, cat_conf = classify_event(title, content)

        # 情绪
        sentiment = calc_sentiment(full_text)

        # 跳过中性 (情绪为0)
        if sentiment == 0:
            continue

        # 标的映射
        stocks = map_title_to_stocks(title)

        # Finnhub codes
        codes_raw = e.get("codes", "")
        if codes_raw:
            for ticker in codes_raw.split(","):
                ticker = ticker.strip().upper()
                if ticker and len(ticker) >= 1:
                    if not any(s["code"] == ticker for s in stocks):
                        stocks.append({"code": ticker, "prob": 0.85, "name": ticker})

        # ─── 去重: 过滤已超量使用的标的 (每标的全扫描最多3次) ───
        direction = "long" if sentiment > 0 else "short"
        filtered = []
        for st in stocks:
            key = st["code"]
            used = _stock_usage.get(key, 0)
            if used < 3:
                filtered.append(st)
                _stock_usage[key] = used + 1
        stocks = filtered

        if not stocks:
            continue

        # 置信度
        abs_sent = abs(sentiment)
        if abs_sent > 0.4:
            confidence = "high"
        elif abs_sent > 0.2:
            confidence = "medium"
        else:
            confidence = "low"

        # 信号分
        score = calc_signal_score(category, sentiment, stocks, len(full_text))

        signals.append({
            "title": title,
            "category": category,
            "direction": direction,
            "confidence": confidence,
            "sentiment": sentiment,
            "score": score,
            "stocks": stocks[:4],
            "source": e.get("source", ""),
        })

    # 按信号分排序
    signals.sort(key=lambda x: x["score"], reverse=True)

    # 统计
    long_sigs = [s for s in signals if s["direction"] == "long"]
    short_sigs = [s for s in signals if s["direction"] == "short"]
    high_conf = [s for s in signals if s["confidence"] == "high"]
    cat_dist = Counter(s["category"] for s in signals)

    summary = {
        "total_events": len(events),
        "total_signals": len(signals),
        "long_count": len(long_sigs),
        "short_count": len(short_sigs),
        "high_confidence": len(high_conf),
        "avg_score": round(sum(s["score"] for s in signals) / max(1, len(signals)), 1),
        "category_distribution": dict(cat_dist.most_common()),
    }

    # 高价值定向分析
    deep_dives = _generate_deep_dives(signals)

    return {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "total_events": len(events),
        "signals": signals,
        "summary": summary,
        "deep_dives": deep_dives,
    }


# ═══════════════════════════════════════════
# 定向深度分析
# ═══════════════════════════════════════════

TRIGGER_RULES = [
    {
        "keywords": ["DeepSeek", "数据中心", "GW"],
        "signal": "做多AI算力/光模块",
        "logic": "DeepSeek建GW级数据中心→算力链暴增→光模块(300308/300502)+AI芯片(NVDA)+服务器(603019)",
        "stocks": ["300308", "300502", "NVDA", "603019"],
        "direction": "long",
        "duration": "60天",
    },
    {
        "keywords": ["佰维存储", "18.608亿"],
        "signal": "做多存储芯片",
        "logic": "18.6亿美元订单→存储景气确认→佰维存储(688525)+封测(000021)+HBM(002156)",
        "stocks": ["688525", "000021", "002156"],
        "direction": "long",
        "duration": "30天",
    },
    {
        "keywords": ["涉军", "清单", "比亚迪"],
        "signal": "空中概/多国产替代",
        "logic": "涉军清单→情绪打压BABA+BYD→中长期利好国产替代688981+002371",
        "stocks": ["BABA(空)", "BYD(空)", "688981", "002371"],
        "direction": "short+long",
        "duration": "7天+60天",
    },
    {
        "keywords": ["硅片", "AI", "4倍"],
        "signal": "做多半导体硅片",
        "logic": "AI服务器耗硅4倍→结构性需求→沪硅(688126)+中环(002129)+韦尔(603501)",
        "stocks": ["688126", "002129", "603501"],
        "direction": "long",
        "duration": "90天",
    },
    {
        "keywords": ["光纤", "涨价"],
        "signal": "做多光通信全链",
        "logic": "日系涨价+美需强→光纤景气→亨通(600487)+长飞(601869)+中际旭创(300308)",
        "stocks": ["600487", "601869", "300308", "300502"],
        "direction": "long",
        "duration": "60天",
    },
    {
        "keywords": ["稀土", "恢复"],
        "signal": "做多稀土板块",
        "logic": "稀土供应紧张→价格上行→北方稀土(600111)+五矿(000831)+盛和(600392)",
        "stocks": ["600111", "000831", "600392"],
        "direction": "long",
        "duration": "30天",
    },
    {
        "keywords": ["沪指", "收复", "4000"],
        "signal": "做多券商",
        "logic": "沪指4000→情绪驱动→东方财富(300059)+中信证券(600030)+券商ETF(512880)",
        "stocks": ["300059", "600030", "512880"],
        "direction": "long",
        "duration": "14天",
    },
    {
        "keywords": ["AI智能体", "爆发"],
        "signal": "做多AI应用",
        "logic": "AI智能体人才抢→应用爆发→金山办公(688111)+科大讯飞(002230)+同花顺(300033)",
        "stocks": ["688111", "002230", "300033"],
        "direction": "long",
        "duration": "60天",
    },
    {
        "keywords": ["美国", "光缆", "强劲"],
        "signal": "做多光通信",
        "logic": "美国光缆需求强劲→利好A股光通信链→亨通光电(600487)+长飞光纤(601869)",
        "stocks": ["600487", "601869", "300308"],
        "direction": "long",
        "duration": "60天",
    },
]


def _generate_deep_dives(signals: List[Dict]) -> List[Dict]:
    """匹配高价值事件 → 输出定向深度分析"""
    results = []
    seen_titles = set()
    for sig in signals:
        for rule in TRIGGER_RULES:
            kw_all = all(kw in sig["title"] for kw in rule["keywords"])
            if kw_all and sig["title"][:80] not in seen_titles:
                results.append({
                    "title": sig["title"],
                    "signal": rule["signal"],
                    "logic": rule["logic"],
                    "stocks": rule["stocks"],
                    "direction": rule["direction"],
                    "duration": rule["duration"],
                    "original_score": sig["score"],
                })
                seen_titles.add(sig["title"][:80])
    return results


# ═══════════════════════════════════════════
# 缓存
# ═══════════════════════════════════════════

_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "ml")
_CACHE_FILE = os.path.join(_CACHE_DIR, "prophet_signals.json")


def save_signals(result: Dict) -> str:
    """保存信号到缓存文件"""
    os.makedirs(_CACHE_DIR, exist_ok=True)
    with open(_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    return _CACHE_FILE


def load_cached_signals(max_age_minutes: int = 30) -> Optional[Dict]:
    """加载缓存的信号（不超过 max_age_minutes 分钟）"""
    if not os.path.exists(_CACHE_FILE):
        return None
    try:
        with open(_CACHE_FILE, "r") as f:
            data = json.load(f)
        ts = data.get("timestamp", "")
        if ts:
            t = datetime.strptime(ts, "%Y-%m-%d %H:%M")
            if (datetime.now() - t).total_seconds() > max_age_minutes * 60:
                return None
        return data
    except Exception:
        return None
