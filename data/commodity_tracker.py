"""
拾米交易工作室 - 大宗商品景气追踪器 (C2)
基于已有点板块数据 + 商品→行业映射，检测商品价格驱动的板块异动

覆盖: 黄金(四川黄金)、煤炭(豫能控股/华电系)、铜/稀土等
"""
from collections import defaultdict
from cache import cache_or_fetch


# ═══════════════════════════════════════════════
# 商品 → A股行业映射
# ═══════════════════════════════════════════════
COMMODITY_SECTORS = {
    "黄金": {
        "industries": ["黄金", "贵金属"],
        "d7_base": 16,
        "trigger": "金价持续上涨+避险情绪",
    },
    "铜": {
        "industries": ["铜"],
        "d7_base": 13,
        "trigger": "铜价上涨+供需紧张",
    },
    "煤炭": {
        "industries": ["煤炭开采"],
        "d7_base": 14,
        "trigger": "煤价反转/下跌利好火电",
    },
    "火电": {
        "industries": ["火力发电"],
        "d7_base": 15,
        "trigger": "煤价下跌→火电利润修复",
    },
    "小金属": {
        "industries": ["小金属", "铅锌", "矿物制品"],
        "d7_base": 13,
        "trigger": "稀土/钨/钼等战略金属涨价",
    },
    "石油": {
        "industries": ["石油开采", "石油加工"],
        "d7_base": 12,
        "trigger": "原油价格波动",
    },
    "铝": {
        "industries": ["铝"],
        "d7_base": 12,
        "trigger": "铝价上涨+供需",
    },
    "化工": {
        "industries": ["化工原料", "染料涂料", "塑料"],
        "d7_base": 11,
        "trigger": "化工品涨价周期",
    },
}


def scan_commodity_catalysts():
    """
    基于板块数据检测商品价格驱动的行业异动

    逻辑: 如果商品相关板块(黄金/煤炭/铜等)涨幅>2%且上涨占比>60%
          则认为该板块受商品价格驱动，产生C2催化剂

    Returns:
        {"stock_scores": {code: {"d7": float, "commodity": str, "sector_chg": float}}}
    """
    from data.fetcher import fetch_sectors, get_stock_basic

    sectors = fetch_sectors()
    if not sectors or not isinstance(sectors, list):
        return {"stock_scores": {}}

    stock_scores = {}

    for sector in sectors:
        if not isinstance(sector, dict):
            continue

        name = sector.get("name", "")
        chg = float(sector.get("change", sector.get("pct_chg", 0)))
        up_count = int(sector.get("up_count", 0))
        down_count = int(sector.get("down_count", 0))
        total = up_count + down_count
        up_ratio = (up_count / total * 100) if total > 0 else 0

        # 找匹配的商品映射
        matched = None
        for commodity, config in COMMODITY_SECTORS.items():
            if name in config["industries"]:
                matched = (commodity, config)
                break

        if not matched:
            continue

        commodity, config = matched

        # 只有当板块异常强势时才触发
        if chg >= 1.5 and up_ratio >= 55:
            d7 = min(config["d7_base"] * (1 + chg / 8), 20)

            # 获取行业成分股
            basic = get_stock_basic()
            if isinstance(basic, dict):
                for ts_code, info in basic.items():
                    if not isinstance(info, dict):
                        continue
                    if info.get("industry") == name:
                        short = str(ts_code).replace(".SZ","").replace(".SH","").replace(".BJ","")
                        if short not in stock_scores or stock_scores[short]["d7"] < d7:
                            stock_scores[short] = {
                                "d7": round(d7, 1),
                                "commodity": commodity,
                                "sector": name,
                                "sector_chg": round(chg, 2),
                                "up_ratio": round(up_ratio, 1),
                            }

    return {"stock_scores": stock_scores}


def fetch_commodity_catalyst_scores():
    """获取C2商品催化剂得分 (缓存5分钟)"""
    return cache_or_fetch("commodity_catalyst_scores",
                         scan_commodity_catalysts,
                         300)
