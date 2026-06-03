"""
拾米交易工作室 - 动态概念发现引擎 (C6 v2)
从市场数据中自动发现新兴概念

核心算法改进:
  v1问题: 市场普涨时所有行业都被合并成一个大集群
  v2方案: 行业超配比率归一化 + 模块度聚类

设计原则:
  - 不预设概念名称, 从数据中自动发现
  - 行业超配 = 该行业在大涨股中的占比远超其市场占比
  - 模块度 = 两行业共同超配的程度 (剔除偶然共现)
"""
import time, requests, re
from datetime import datetime, timedelta
from collections import defaultdict, Counter
from cache import cache_or_fetch


def _get_hot_stocks(trade_date=None, threshold=5.0):
    import tushare as ts, config
    pro = ts.pro_api(config.TUSHARE_TOKEN)
    if trade_date is None:
        trade_date = datetime.now().strftime("%Y%m%d")
    daily = pro.daily(trade_date=trade_date,
                      fields="ts_code,close,pct_chg,amount,vol")
    if daily is None or len(daily) == 0:
        return None
    return daily[daily["pct_chg"] >= threshold].copy()


def _get_industry_map():
    import tushare as ts, config
    pro = ts.pro_api(config.TUSHARE_TOKEN)
    basic = pro.stock_basic(exchange="", list_status="L",
                           fields="ts_code,name,industry")
    result = {}
    for _, r in basic.iterrows():
        result[r["ts_code"]] = {
            "name": r["name"],
            "industry": r.get("industry") or "未知",
        }
    return result


def detect_cross_industry_clusters(trade_date=None):
    """
    跨行业联动检测 v2 — 基于行业超配比率

    核心指标: 超配比率 = (行业在大涨股中占比) / (行业在全市场中占比)

    如果某行业超配比率 > 2.0, 说明该行业被"异常选中"
    多个超配行业同时出现 → 跨行业概念正在发酵
    """
    hot = _get_hot_stocks(trade_date, threshold=5.0)
    if hot is None or len(hot) < 20:
        return {"clusters": [], "stock_scores": {}}

    ind_map = _get_industry_map()
    total_market = len(ind_map)  # ~5525
    total_hot = len(hot)

    # 全市场行业分布(基准)
    market_ind_dist = Counter()
    for info in ind_map.values():
        market_ind_dist[info.get("industry", "未知")] += 1

    # 大涨股行业分布
    hot_ind_stocks = defaultdict(list)
    for _, row in hot.iterrows():
        code = row["ts_code"]
        info = ind_map.get(code, {})
        industry = info.get("industry", "未知")
        short = str(code).replace(".SZ","").replace(".SH","").replace(".BJ","")
        hot_ind_stocks[industry].append({
            "code": short, "ts_code": code,
            "name": info.get("name", "?"),
            "pct_chg": float(row["pct_chg"]),
            "close": float(row["close"]),
        })

    # === 计算超配比率 ===
    overrep = {}
    for ind, hot_stocks in hot_ind_stocks.items():
        hot_pct = len(hot_stocks) / total_hot
        market_pct = market_ind_dist.get(ind, 1) / total_market
        ratio = hot_pct / max(market_pct, 0.001)
        if len(hot_stocks) >= 3 and ratio >= 2.0:
            overrep[ind] = {
                "stocks": hot_stocks,
                "count": len(hot_stocks),
                "ratio": round(ratio, 1),
                "hot_pct": round(hot_pct * 100, 1),
                "market_pct": round(market_pct * 100, 2),
            }

    if len(overrep) < 3:
        return {"clusters": [], "stock_scores": {}}

    # === 超配行业两两联动强度 ===
    inds = list(overrep.keys())
    edges = []
    for i in range(len(inds)):
        for j in range(i + 1, len(inds)):
            a, b = inds[i], inds[j]
            cnt_a = overrep[a]["count"]
            cnt_b = overrep[b]["count"]
            ratio_ab = (overrep[a]["ratio"] + overrep[b]["ratio"]) / 2
            # 联动强度 = 规模×超配均值
            strength = min(cnt_a, cnt_b) * ratio_ab / 2
            if strength >= 4:
                edges.append((a, b, round(strength, 1)))

    edges.sort(key=lambda x: -x[2])

    # === 聚类: 只合并最强联动的行业 ===
    parent = {ind: ind for ind in inds}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    # 只取TOP 40%的强边缘做合并
    threshold_idx = max(1, int(len(edges) * 0.4))
    for ind_a, ind_b, strength in edges[:threshold_idx]:
        union(ind_a, ind_b)

    # 分组
    clusters_raw = defaultdict(list)
    for ind in inds:
        root = find(ind)
        clusters_raw[root].append(ind)

    # 筛选: 至少2个超配行业 + 8只个股
    clusters = []
    for root, members in clusters_raw.items():
        if len(members) < 2:
            continue
        all_stocks = []
        total_gain = 0
        for ind in members:
            ss = overrep[ind]["stocks"]
            all_stocks.extend(ss)
            total_gain += sum(s["pct_chg"] for s in ss)

        if len(all_stocks) < 8:
            continue

        avg_gain = total_gain / len(all_stocks)
        clusters.append({
            "industries": sorted(members),
            "overrep_ratios": {ind: overrep[ind]["ratio"] for ind in members},
            "stock_count": len(all_stocks),
            "avg_gain": round(avg_gain, 1),
            "stocks": sorted(all_stocks, key=lambda x: -x["pct_chg"])[:25],
            "auto_name": f"超配集群({'+'.join(members[:2])})",
        })

    clusters.sort(key=lambda x: -x["stock_count"])

    # 生成个股C6得分
    stock_scores = {}
    for cluster in clusters:
        # D7: 行业数×超配均值 → 8-14分
        ratio_sum = sum(cluster["overrep_ratios"].values())
        d7 = min(8 + ratio_sum / 3, 14)
        for s in cluster["stocks"]:
            if s["code"] not in stock_scores:
                stock_scores[s["code"]] = {
                    "d7": round(d7, 1),
                    "concept": cluster["auto_name"],
                    "industries": cluster["industries"],
                    "stock_count": cluster["stock_count"],
                }

    return {"clusters": clusters, "stock_scores": stock_scores}


# ═══════════════════════════════════════════════
# NLP关键词提取
# ═══════════════════════════════════════════════
def _search_cninfo(keyword, days=7):
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    data = {
        "pageNum": "1", "pageSize": "10",
        "column": "szse", "tabName": "fulltext",
        "plate": "", "stock": "",
        "searchkey": keyword,
        "secid": "", "category": "", "trade": "",
        "seDate": f"{start}~{end}",
    }
    try:
        r = requests.post("http://www.cninfo.com.cn/new/hisAnnouncement/query",
                         data=data, timeout=10,
                         headers={"User-Agent": "Mozilla/5.0"})
        r.encoding = "utf-8"
        return (r.json().get("announcements") or [])
    except:
        return []


def _extract_keywords(titles):
    stopwords = {"关于", "公司", "公告", "的", "及", "与", "及其",
                 "年度", "季度", "报告", "董事会", "监事会", "股东",
                 "大会", "决议", "修订", "章程", "变更", "完成",
                 "进展", "情况", "说明", "提示性", "2025", "2026", "2024",
                 "有限", "股份", "集团", "科技", "技术", "有限公", "司"}
    freq = Counter()
    for title in titles:
        segs = re.split(r'[（）、，。；：\s·]', title)
        for seg in segs:
            seg = seg.strip()
            if 2 <= len(seg) <= 8 and seg not in stopwords:
                freq[seg] += 1
    return freq


def name_concept_clusters(clusters, max_search=2):
    """自动命名: 从集群成分股公告中提取共性关键词"""
    for cluster in clusters[:max_search]:
        titles = []
        for s in cluster["stocks"][:5]:
            anns = _search_cninfo(s["name"], days=7)
            for ann in anns[:5]:
                t = ann.get("announcementTitle", "")
                if t:
                    titles.append(t)
            time.sleep(0.1)
        if titles:
            freq = _extract_keywords(titles)
            top = [w for w, c in freq.most_common(10) if c >= 2]
            cluster["auto_name"] = "·".join(top[:2]) if top else cluster["auto_name"]
    return clusters


# ═══════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════
def scan_concept_heat():
    result = detect_cross_industry_clusters()
    clusters = result.get("clusters", [])
    if clusters:
        try:
            clusters = name_concept_clusters(clusters, max_search=2)
        except Exception as e:
            print(f"[C6] NLP naming skipped: {e}")
    return {"stock_scores": result.get("stock_scores", {}), "clusters": clusters}


def fetch_concept_catalyst_scores():
    return cache_or_fetch("concept_catalyst_scores", scan_concept_heat, 300)
