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
        "impact": "high",
        "duration_days": 30,
    },
    "earnings": {
        "name": "财报窗口",
        "keywords": ["业绩预告", "年报", "季报", "营收", "净利润", "EPS"],
        "impact": "high",
        "duration_days": 14,
    },
    "macro": {
        "name": "宏观数据",
        "keywords": ["GDP", "CPI", "PPI", "PMI", "社融", "M2", "进出口", "外汇储备",
                    "非农", "FOMC", "美联储", "利率决议"],
        "impact": "medium",
        "duration_days": 7,
    },
    "geopolitical": {
        "name": "地缘事件",
        "keywords": ["制裁", "关税", "贸易战", "冲突", "协议", "脱钩"],
        "impact": "high",
        "duration_days": 60,
    },
    "sector": {
        "name": "行业动态",
        "keywords": ["光伏", "新能源", "半导体", "AI", "人工智能", "医药", "消费",
                    "房地产", "汽车", "芯片", "锂电", "储能"],
        "impact": "medium",
        "duration_days": 14,
    },
    "commodity": {
        "name": "商品价格",
        "keywords": ["原油", "黄金", "铜", "铝", "锂", "螺纹钢", "铁矿石", "天然气"],
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
            for code, info in catalysts.items():
                events.append({
                    "date": today,
                    "market": "A",
                    "type": "sector",
                    "title": info.get("event", "公告事件"),
                    "code": code,
                    "source": "cninfo",
                    "impact": "medium",
                })
    except Exception:
        pass
    return events


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
            
            events.append({
                "date": today,
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


# ─── 事件分类 ─────────────────────────────────────

def _classify_event(text: str) -> Optional[str]:
    """根据关键词分类事件类型"""
    text_lower = text.lower()
    scores = {}
    for etype, config in EVENT_TYPES.items():
        score = sum(1 for kw in config["keywords"] if kw.lower() in text_lower)
        if score > 0:
            scores[etype] = score
    return max(scores, key=scores.get) if scores else None


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

        # 2. 行业映射
        for sector, codes in SECTOR_STOCK_MAP.items():
            if sector in text or any(kw in text for kw in EVENT_TYPES.get(etype, {}).get("keywords", [])):
                for code in codes[:3]:  # 每行业最多3只
                    if code not in [s["code"] for s in affected_stocks]:
                        affected_stocks.append({
                            "code": code,
                            "name": "",
                            "direction": _infer_direction(etype, text),
                            "reason": f"{sector}{'利好' if '利好' in title or '增长' in title else '影响'}",
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
                    "type": etype,
                    "title": title[:80],
                    "impact": event.get("impact", "medium"),
                    "duration_days": EVENT_TYPES.get(etype, {}).get("duration_days", 7),
                },
                "stocks": affected_stocks[:5],
            })

    return signals


def _infer_direction(etype: str, text: str) -> str:
    """推断做多/做空方向"""
    negative_words = ["下降", "下滑", "亏损", "制裁", "处罚", "警告", "暴跌", "危机",
                     "decline", "drop", "sanction", "penalty", "crash", "crisis"]
    positive_words = ["增长", "突破", "利好", "补贴", "支持", "上升", "创新高",
                     "growth", "breakthrough", "subsidy", "support", "record high"]

    neg = sum(1 for w in negative_words if w in text)
    pos = sum(1 for w in positive_words if w in text)

    if neg > pos:
        return "short"
    elif pos > neg:
        return "long"
    return "long"  # 默认做多


# ─── 统一获取 + 生成信号 ──────────────────────────

def scan_market_events() -> Dict:
    """全市场事件扫描 → 生成交易信号

    Returns:
        {
            "timestamp": str,
            "events": [event_dict],
            "signals": [{"event": dict, "stocks": list}],
            "summary": {"total_events": int, "long_signals": int, "short_signals": int}
        }
    """
    all_events = []

    # A股事件
    cn_events = fetch_cn_events()
    all_events.extend(cn_events)

    # 美股事件
    us_events = fetch_us_events()
    all_events.extend(us_events)

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

    # 映射到标的
    signals = map_events_to_stocks(unique)

    # 统计
    long_count = sum(1 for s in signals for st in s["stocks"] if st["direction"] == "long")
    short_count = sum(1 for s in signals for st in s["stocks"] if st["direction"] == "short")

    # 推送告警
    if signals:
        _push_event_alerts(signals)

    return {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "events": unique[:20],
        "signals": signals[:10],
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
