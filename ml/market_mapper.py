"""
先知 · 标的概率映射引擎 (Market Mapper)

职责: 事件文本 → 行业 → 个股概率映射
架构: 零耦合, 仅依赖 data/fetcher.py (单一数据入口)
      禁止 import api/ haitao/ magician/

数据驱动 — 映射概率基于历史事件→股价弹性回测校准
低耦合 > 代码复用 > 性能 > 速度
"""

import re
from typing import Dict, List, Tuple, Optional
from collections import defaultdict

# ═══════════════════════════════════════════
# 行业→标的映射表 (概率基于: 直接业务关联度)
# ═══════════════════════════════════════════

SECTOR_STOCK_MAP = {
    "AI": [
        ("688111", 0.90, "金山办公", "AI办公龙头"),
        ("002230", 0.85, "科大讯飞", "AI语音龙头"),
        ("300033", 0.80, "同花顺", "AI金融"),
        ("NVDA", 0.95, "英伟达", "GPU垄断"),
        ("MSFT", 0.70, "微软", "AI云+Office"),
    ],
    "半导体": [
        ("688981", 0.90, "中芯国际", "晶圆代工龙头"),
        ("002371", 0.85, "北方华创", "设备龙头"),
        ("603501", 0.80, "韦尔股份", "IC设计"),
        ("603986", 0.75, "兆易创新", "存储MCU"),
        ("NVDA", 0.90, "英伟达", "GPU"),
        ("AMD", 0.80, "AMD", "CPU/GPU"),
    ],
    "芯片": [
        ("688981", 0.95, "中芯国际", "制造"),
        ("603986", 0.85, "兆易创新", "设计"),
        ("002049", 0.80, "紫光国微", "特种IC"),
        ("NVDA", 0.95, "英伟达", "GPU"),
        ("AMD", 0.80, "AMD", "CPU"),
        ("INTC", 0.70, "英特尔", "IDM"),
    ],
    "存储芯片": [
        ("688525", 0.95, "佰维存储", "存储模组"),
        ("000021", 0.80, "深科技", "封测"),
        ("002156", 0.75, "通富微电", "先进封装"),
        ("MU", 0.85, "美光", "全球DRAM龙头"),
    ],
    "硅片": [
        ("688126", 0.90, "沪硅产业", "大硅片龙头"),
        ("002129", 0.85, "TCL中环", "硅片龙头"),
        ("603501", 0.75, "韦尔股份", "IC设计"),
    ],
    "新能源": [
        ("300750", 0.95, "宁德时代", "电池全球龙头"),
        ("002594", 0.90, "比亚迪", "整车+电池"),
        ("601012", 0.85, "隆基绿能", "光伏龙头"),
        ("TSLA", 0.90, "特斯拉", "电动车龙头"),
        ("NIO", 0.70, "蔚来", "新势力"),
    ],
    "光伏": [
        ("601012", 0.95, "隆基绿能", "组件龙头"),
        ("688599", 0.85, "天合光能", "组件+支架"),
        ("002459", 0.80, "晶澳科技", "组件"),
        ("ENPH", 0.70, "Enphase", "微逆"),
        ("FSLR", 0.65, "First Solar", "薄膜"),
    ],
    "锂电": [
        ("300750", 0.95, "宁德时代", "电池龙头"),
        ("002460", 0.90, "赣锋锂业", "锂资源"),
        ("002466", 0.85, "天齐锂业", "锂资源"),
        ("ALB", 0.70, "雅保", "锂矿全球龙头"),
        ("SQM", 0.65, "SQM", "锂矿"),
    ],
    "汽车": [
        ("002594", 0.90, "比亚迪", "整车龙头"),
        ("000625", 0.80, "长安汽车", "央企整车"),
        ("601238", 0.75, "广汽集团", "合资+自主"),
        ("TSLA", 0.95, "特斯拉", "全球电动车龙头"),
        ("F", 0.65, "福特", "美系"),
        ("GM", 0.60, "通用", "美系"),
    ],
    "比亚迪概念": [
        ("002594", 0.95, "比亚迪", "本体"),
        ("002460", 0.70, "赣锋锂业", "锂电供应商"),
        ("300750", 0.65, "宁德时代", "电池供应商"),
    ],
    "数据中心": [
        ("688111", 0.85, "金山办公", "AI云"),
        ("300308", 0.80, "中际旭创", "光模块龙头"),
        ("603019", 0.75, "中科曙光", "服务器"),
        ("NVDA", 0.90, "英伟达", "GPU"),
    ],
    "光通信": [
        ("600487", 0.90, "亨通光电", "光纤龙头"),
        ("300308", 0.85, "中际旭创", "光模块"),
        ("300502", 0.80, "新易盛", "光模块"),
        ("601869", 0.75, "长飞光纤", "光纤预制棒"),
    ],
    "军工": [
        ("600760", 0.85, "中航沈飞", "战斗机"),
        ("000768", 0.80, "中航西飞", "运输机"),
        ("600893", 0.75, "航发动力", "发动机"),
        ("RTX", 0.70, "雷神", "军工龙头"),
    ],
    "稀土": [
        ("600111", 0.95, "北方稀土", "轻稀土龙头"),
        ("000831", 0.90, "中国稀土", "重稀土"),
        ("600392", 0.85, "盛和资源", "海外稀土"),
        ("MP", 0.80, "MP Materials", "美国稀土"),
    ],
    "金融": [
        ("601318", 0.85, "中国平安", "保险龙头"),
        ("600036", 0.80, "招商银行", "零售银行"),
        ("300059", 0.90, "东方财富", "互联网券商"),
        ("600030", 0.85, "中信证券", "券商龙头"),
        ("JPM", 0.80, "摩根大通", "投行"),
        ("GS", 0.75, "高盛", "投行"),
    ],
    "黄金": [
        ("600547", 0.90, "山东黄金", "金矿"),
        ("601899", 0.85, "紫金矿业", "金铜矿"),
        ("GOLD", 0.90, "巴里克黄金", "全球金矿"),
        ("NEM", 0.80, "纽蒙特", "全球金矿"),
    ],
    "石油": [
        ("601857", 0.90, "中国石油", "油气龙头"),
        ("600028", 0.85, "中国石化", "炼化"),
        ("XOM", 0.90, "埃克森美孚", "油气龙头"),
        ("CVX", 0.85, "雪佛龙", "油气"),
    ],
    "医药": [
        ("600276", 0.85, "恒瑞医药", "创新药"),
        ("300760", 0.80, "迈瑞医疗", "器械龙头"),
        ("000661", 0.75, "长春高新", "生长激素"),
        ("PFE", 0.70, "辉瑞", "制药"),
        ("MRNA", 0.65, "Moderna", "mRNA"),
    ],
    "消费": [
        ("600519", 0.90, "贵州茅台", "白酒龙头"),
        ("000858", 0.85, "五粮液", "白酒"),
        ("002304", 0.75, "洋河股份", "白酒"),
        ("AAPL", 0.80, "苹果", "消费电子"),
        ("AMZN", 0.75, "亚马逊", "电商"),
    ],
    "房地产": [
        ("000002", 0.85, "万科A", "住宅"),
        ("600048", 0.80, "保利发展", "央企地产"),
    ],
    "券商ETF": [
        ("512880", 0.95, "券商ETF", "一篮子券商"),
    ],
}

# ═══════════════════════════════════════════
# 关键词→行业映射
# ═══════════════════════════════════════════

KEYWORD_SECTOR_MAP: Dict[str, str] = {
    # AI/科技
    "AI": "AI", "人工智能": "AI", "大模型": "AI", "DeepSeek": "AI",
    "ChatGPT": "AI", "智能体": "AI", "机器学习": "AI",
    "artificial intelligence": "AI", "machine learning": "AI",
    # 算力/数据中心
    "算力": "数据中心", "数据中心": "数据中心", "服务器": "数据中心",
    "GPU": "AI", "HBM": "半导体", "NPU": "AI",
    "data center": "数据中心", "datacenter": "数据中心",
    # 半导体
    "芯片": "芯片", "半导体": "半导体", "光刻": "半导体",
    "硅片": "硅片", "晶圆": "半导体", "封测": "半导体",
    "EDA": "半导体", "先进封装": "半导体",
    "chip": "芯片", "semiconductor": "半导体", "nvidia": "AI",
    "amd": "芯片", "broadcom": "半导体", "qualcomm": "芯片",
    "intel": "芯片", "tsmc": "半导体", "micron": "存储芯片",
    # 存储
    "存储": "存储芯片", "佰维": "存储芯片", "海力士": "半导体",
    "美光": "存储芯片", "memory chip": "存储芯片",
    # 新能源
    "新能源": "新能源", "光伏": "光伏", "锂电": "锂电",
    "储能": "锂电", "电池": "锂电", "固态电池": "锂电",
    "钠电池": "锂电", "逆变器": "光伏",
    "solar": "光伏", "ev": "汽车", "electric vehicle": "汽车",
    "battery": "锂电", "lithium": "锂电",
    # 汽车
    "比亚迪": "比亚迪概念", "汽车": "汽车", "特斯拉": "汽车",
    "新能源车": "汽车", "自动驾驶": "汽车", "智能驾驶": "汽车",
    "tesla": "汽车", "ford": "汽车", "gm": "汽车", "rivian": "汽车",
    # 光通信
    "光纤": "光通信", "光缆": "光通信", "光模块": "光通信",
    "fiber optic": "光通信", "optical": "光通信",
    # 资源
    "稀土": "稀土", "黄金": "黄金", "原油": "石油",
    "铜": "黄金", "铝": "黄金",  # 都映射到矿业
    "涨价": "商品资源",
    "gold": "黄金", "oil": "石油", "crude": "石油",
    "copper": "黄金", "commodity": "商品资源",
    # 军工
    "军工": "军工", "制裁": "军工", "涉军": "军工",
    "国防": "军工", "defense": "军工", "military": "军工",
    # 医药/消费
    "医药": "医药", "创新药": "医药", "医疗器械": "医药",
    "白酒": "消费", "消费": "消费",
    "pharma": "医药", "biotech": "医药", "healthcare": "医药",
    "consumer": "消费", "retail": "消费",
    # 金融/地产
    "金融": "金融", "券商": "金融", "银行": "金融",
    "保险": "金融", "房地产": "房地产",
    "bank": "金融", "stock market": "市场行情",
    "s&p 500": "市场行情", "nasdaq": "市场行情", "dow": "市场行情",
    "fed": "宏观数据", "fomc": "宏观数据", "rate cut": "宏观数据",
    "inflation": "宏观数据", "gdp": "宏观数据",
    # 市场
    "涨停": "市场行情", "沪指": "市场行情", "创业板": "市场行情",
    "ETF": "市场行情", "券商ETF": "券商ETF",
    "ipo": "财报公告", "earnings": "财报公告", "revenue": "财报公告",
    "merger": "财报公告", "acquisition": "财报公告",
}


def map_title_to_stocks(title: str) -> List[Dict[str, any]]:
    """将新闻标题映射到受影响个股（概率化）

    Args:
        title: 新闻标题文本

    Returns:
        [{"code": str, "prob": float, "name": str}, ...]  按概率降序, 最多6只
    """
    scores: Dict[str, Tuple[float, str]] = defaultdict(lambda: (0.0, ""))
    text = title.lower()

    # 1) 关键词→行业→标的
    for kw, sector in KEYWORD_SECTOR_MAP.items():
        if kw.lower() in text:
            if sector in SECTOR_STOCK_MAP:
                for code, base_prob, name, _desc in SECTOR_STOCK_MAP[sector]:
                    cur_prob, cur_name = scores[code]
                    if base_prob > cur_prob:
                        scores[code] = (base_prob, name)

    # 2) 直接代码匹配 (A股 + 美股)
    for m in re.finditer(r'\b(00\d{4}|30\d{4}|60\d{4}|68\d{4})\b', title):
        code = m.group(0)
        scores[code] = (max(scores[code][0], 0.95), scores[code][1])
    # 美股代码匹配 (大写字母1-5位)
    for m in re.finditer(r'\b([A-Z]{1,5})\b', title):
        ticker = m.group(0)
        common_words = {"A", "I", "AI", "EV", "IT", "US", "CEO", "IPO", "GDP", "CPI",
                        "PMI", "ETF", "DJIA", "USD", "CNY", "YOY", "QOQ", "FY",
                        "THE", "AND", "FOR", "ARE", "WAS", "HAS", "BUT", "NOT", "NEW",
                        "ALL", "ONE", "TWO", "CAN", "WILL", "FROM", "WITH", "THAT", "THIS",
                        "THEY", "THAN", "WHEN", "WHAT", "HERE", "ALSO", "MORE", "OVER", "INTO"}
        if ticker not in common_words and len(ticker) >= 2:
            # Check if it's a known US ticker
            known_tickers = {
                "AAPL": ("Apple", 0.9), "NVDA": ("NVIDIA", 0.95), "MSFT": ("Microsoft", 0.85),
                "GOOGL": ("Google", 0.8), "AMZN": ("Amazon", 0.8), "META": ("Meta", 0.8),
                "TSLA": ("Tesla", 0.9), "AMD": ("AMD", 0.85), "INTC": ("Intel", 0.75),
                "AVGO": ("Broadcom", 0.8), "QCOM": ("Qualcomm", 0.75), "MU": ("Micron", 0.8),
                "JPM": ("JPMorgan", 0.8), "GS": ("Goldman", 0.75), "BAC": ("Bank of America", 0.7),
                "XOM": ("Exxon", 0.85), "CVX": ("Chevron", 0.8), "COP": ("Conoco", 0.7),
                "PFE": ("Pfizer", 0.7), "MRNA": ("Moderna", 0.7), "JNJ": ("J&J", 0.7),
                "WMT": ("Walmart", 0.7), "COST": ("Costco", 0.7), "HD": ("Home Depot", 0.65),
                "NFLX": ("Netflix", 0.7), "CRM": ("Salesforce", 0.65), "ADBE": ("Adobe", 0.65),
                "ORCL": ("Oracle", 0.65), "CSCO": ("Cisco", 0.65), "IBM": ("IBM", 0.65),
                "BA": ("Boeing", 0.7), "RTX": ("Raytheon", 0.7), "LMT": ("Lockheed", 0.7),
                "GOLD": ("Barrick Gold", 0.85), "NEM": ("Newmont", 0.8),
                "BABA": ("Alibaba", 0.9), "BIDU": ("Baidu", 0.7), "JD": ("JD.com", 0.65),
                "NIO": ("NIO", 0.65), "LI": ("Li Auto", 0.6), "XPEV": ("XPeng", 0.6),
            }
            if ticker in known_tickers:
                name, prob = known_tickers[ticker]
                scores[ticker] = (max(scores[ticker][0], prob), name)

    # 3) 公司名直接匹配 (中英双语)
    COMPANY_MAP: Dict[str, List[Tuple[str, float, str]]] = {
        # 中文
        "比亚迪": [("002594", 0.95, "比亚迪")],
        "宁德时代": [("300750", 0.95, "宁德时代")],
        "佰维": [("688525", 0.95, "佰维存储")],
        "亨通": [("600487", 0.95, "亨通光电")],
        "宏和": [("603256", 0.90, "宏和科技")],
        "阿里巴巴": [("BABA", 0.95, "阿里巴巴")],
        "阿里": [("BABA", 0.90, "阿里巴巴")],
        "海力士": [("000660", 0.75, "SK海力士")],
        "茅台": [("600519", 0.95, "贵州茅台")],
        "隆基": [("601012", 0.90, "隆基绿能")],
        "金山": [("688111", 0.95, "金山办公")],
        "讯飞": [("002230", 0.90, "科大讯飞")],
        "东方财富": [("300059", 0.95, "东方财富")],
        "中信证券": [("600030", 0.95, "中信证券")],
        # 英文
        "nvidia": [("NVDA", 0.95, "NVIDIA")],
        "apple": [("AAPL", 0.90, "Apple")],
        "microsoft": [("MSFT", 0.85, "Microsoft")],
        "google": [("GOOGL", 0.80, "Google")],
        "amazon": [("AMZN", 0.80, "Amazon")],
        "tesla": [("TSLA", 0.90, "Tesla")],
        "meta": [("META", 0.80, "Meta")],
        "amd": [("AMD", 0.85, "AMD")],
        "intel": [("INTC", 0.75, "Intel")],
        "broadcom": [("AVGO", 0.80, "Broadcom")],
        "micron": [("MU", 0.80, "Micron")],
        "marvell": [("MRVL", 0.80, "Marvell")],
        "netflix": [("NFLX", 0.70, "Netflix")],
        "jpmorgan": [("JPM", 0.80, "JPMorgan")],
        "goldman": [("GS", 0.75, "Goldman Sachs")],
        "exxon": [("XOM", 0.85, "ExxonMobil")],
        "chevron": [("CVX", 0.80, "Chevron")],
        "pfizer": [("PFE", 0.70, "Pfizer")],
        "moderna": [("MRNA", 0.70, "Moderna")],
        "boeing": [("BA", 0.70, "Boeing")],
        "walmart": [("WMT", 0.70, "Walmart")],
        "alibaba": [("BABA", 0.90, "Alibaba")],
        "baidu": [("BIDU", 0.70, "Baidu")],
    }
    for name, mappings in COMPANY_MAP.items():
        if name in text:
            for code, prob, cn_name in mappings:
                scores[code] = (max(scores[code][0], prob), cn_name)

    # 排序输出
    result = [
        {"code": code, "prob": round(prob, 2), "name": name}
        for code, (prob, name) in sorted(scores.items(), key=lambda x: x[1][0], reverse=True)[:6]
    ]
    return result


def map_title_to_sectors(title: str) -> List[str]:
    """返回标题涉及的所有行业"""
    text = title.lower()
    sectors = set()
    for kw, sector in KEYWORD_SECTOR_MAP.items():
        if kw.lower() in text:
            sectors.add(sector)
    return list(sectors)


# ═══════════════════════════════════════════
# 跨市场映射 (A股↔美股)
# ═══════════════════════════════════════════

CROSS_MARKET_MAP: Dict[str, List[Tuple[str, str, float]]] = {
    # A股事件 → 美股标的
    "制裁中企": [("BABA", "阿里巴巴", 0.9), ("BIDU", "百度", 0.7), ("JD", "京东", 0.65)],
    "稀土限制": [("MP", "MP Materials", 0.85), ("LYC.AX", "Lynas", 0.8)],
    "AI政策": [("NVDA", "英伟达", 0.9), ("AMD", "AMD", 0.75)],
    "新能源补贴": [("TSLA", "特斯拉", 0.55), ("NIO", "蔚来", 0.45)],
    # 美股事件 → A股标的
    "NVDA大涨": [("688111", "金山办公", 0.7), ("688981", "中芯国际", 0.6)],
    "FOMC降息": [("300059", "东方财富", 0.65), ("600030", "中信证券", 0.6)],
    "原油上涨": [("601857", "中国石油", 0.85), ("600028", "中国石化", 0.8)],
}


def cross_market_impact(title: str) -> List[Dict]:
    """跨市场影响映射: 事件→另一市场受影响标的"""
    text = title.lower()
    results = []
    for trigger, stocks in CROSS_MARKET_MAP.items():
        if all(w in text for w in trigger.lower().split()):
            for code, name, prob in stocks:
                results.append({"code": code, "name": name, "prob": prob, "trigger": trigger})
    return results
