"""
拾米交易工作室 - 政策催化剂扫描器 (C1)
基于 cninfo 公告 + 政策关键词库匹配

覆盖行业: 电力/能源、军工、基建/建材、半导体、数字经济
"""
import time, requests
from datetime import datetime, timedelta
from collections import defaultdict
from cache import cache_or_fetch


# ═══════════════════════════════════════════════
# 政策关键词库
# ═══════════════════════════════════════════════
POLICY_THEMES = {
    "电力改革": {
        "keywords": ["电力改革", "电力市场", "新型电力系统", "容量电价",
                     "特高压", "智能电网", "虚拟电厂", "绿色电力"],
        "d7_base": 16,
    },
    "国企改革": {
        "keywords": ["国企改革", "央企重组", "市值管理", "混合所有制",
                     "国资改革", "央企整合", "国有资本"],
        "d7_base": 18,
    },
    "国防军工": {
        "keywords": ["国防", "军工", "武器装备", "军队现代化", "军民融合",
                     "国防预算", "军贸出口"],
        "d7_base": 15,
    },
    "基建投资": {
        "keywords": ["基础设施", "重大工程", "城中村改造", "保障性住房",
                     "城市更新", "新型城镇化", "西部大开发"],
        "d7_base": 14,
    },
    "新能源政策": {
        "keywords": ["光伏", "风电", "储能", "氢能", "碳中和", "碳达峰",
                     "清洁能源", "可再生能源"],
        "d7_base": 14,
    },
    "半导体国产化": {
        "keywords": ["集成电路", "半导体产业", "芯片自给", "国产替代",
                     "先进封装", "半导体材料"],
        "d7_base": 14,
    },
    "数字经济": {
        "keywords": ["数据要素", "数据资产", "数字经济", "数字政府",
                     "信创产业", "数字化转型", "东数西算"],
        "d7_base": 13,
    },
    "房地产政策": {
        "keywords": ["房地产调控", "楼市政策", "购房补贴", "限购松绑",
                     "止跌回稳", "保交楼"],
        "d7_base": 13,
    },
    "低空经济": {
        "keywords": ["低空经济", "eVTOL", "无人机", "通用航空",
                     "低空空域", "飞行汽车"],
        "d7_base": 12,
    },
}


def _search_cninfo(keyword, start_date, end_date, size=15):
    """搜索cninfo公告"""
    data = {
        "pageNum": "1", "pageSize": str(size),
        "column": "szse", "tabName": "fulltext",
        "plate": "", "stock": "",
        "searchkey": keyword,
        "secid": "", "category": "", "trade": "",
        "seDate": f"{start_date}~{end_date}",
    }
    try:
        r = requests.post("http://www.cninfo.com.cn/new/hisAnnouncement/query",
                         data=data, timeout=10,
                         headers={"User-Agent": "Mozilla/5.0"})
        r.encoding = "utf-8"
        j = r.json()
        return j.get("announcements") or []
    except Exception:
        return []


def scan_policy_catalysts(days=14):
    """
    从cninfo扫描政策关键词 → 返回C1催化剂评分

    Args:
        days: 扫描最近N天的公告

    Returns:
        {"stock_scores": {code: {"d7": float, "policy_themes": [str]}},
         "themes_found": [{"theme": str, "stocks": int, "d7_base": int}]}
    """
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    stock_hits = defaultdict(lambda: {"d7": 0, "themes": set()})
    themes_found = []

    for theme, config in POLICY_THEMES.items():
        # 每主题搜1个核心关键词以控制API调用量
        kw = config["keywords"][0]
        anns = _search_cninfo(kw, start, end, size=15)
        if not anns:
            continue

        hit_stocks = set()
        for ann in anns:
            code = ann.get("secCode", "")
            title = ann.get("announcementTitle", "")
            if not code:
                continue

            # 确认标题中有关键词
            title_match = any(k in title for k in config["keywords"])
            if not title_match:
                continue

            hit_stocks.add(code)
            stock_hits[code]["d7"] = max(stock_hits[code]["d7"], config["d7_base"])
            stock_hits[code]["themes"].add(theme)

        if hit_stocks:
            themes_found.append({
                "theme": theme,
                "stocks": len(hit_stocks),
                "d7_base": config["d7_base"],
                "keyword": kw,
            })

        time.sleep(0.15)

    # Convert to plain dict
    scores = {}
    for code, info in stock_hits.items():
        scores[code] = {
            "d7": info["d7"],
            "policy_themes": list(info["themes"]),
        }

    return {
        "stock_scores": scores,
        "themes_found": themes_found,
        "method": "cninfo_policy_kw",
        "scan_period": f"{start}~{end}",
    }


def fetch_policy_catalyst_scores():
    """获取C1政策催化剂得分 (缓存10分钟)"""
    return cache_or_fetch("policy_catalyst_scores",
                         lambda: scan_policy_catalysts(days=14),
                         600)
