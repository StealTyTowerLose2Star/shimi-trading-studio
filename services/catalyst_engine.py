"""
拾米交易工作室 - 催化剂评分引擎
将巨潮资讯公告分类并转化为 D7/D8 评分

催化剂7类 (C1-C7):
  C1 政策文件   — policy_scanner.py (后续)
  C2 行业景气   — commodity_tracker.py (后续)
  C3 重大合同   — cninfo_fetcher.py ✅
  C5 资产重组   — cninfo_fetcher.py ✅
  C7 业绩超预期 — cninfo_fetcher.py ✅
  C4 技术突破   — (后续)
  C6 题材轮动   — (后续)
"""
import re
import time
from datetime import datetime, timedelta
from collections import defaultdict
from cache import cache_or_fetch, cache_set, cache_delete

# 导入数据层
from data.cninfo_fetcher import fetch_monthly_catalysts


# ═══════════════════════════════════════════════
# 公告标题 → 催化剂强度细粒度评分
# ═══════════════════════════════════════════════
def _score_contract(title, company):
    """
    C3 重大合同评分
    基础14分，根据关键词上调
    """
    score = 14
    title_lower = title.lower()
    
    # 金额暗示
    if any(w in title for w in ["亿元", "亿美金", "亿美元"]):
        score += 4
    elif any(w in title for w in ["千万", "万元"]) and "亿" not in title:
        score += 1
    
    # 客户背景
    if any(w in title for w in ["军方", "部队", "国防", "军工"]):
        score += 4  # 军工订单溢价
    elif any(w in title for w in ["海外", "国际", "出口", "一带一路"]):
        score += 2
    
    # 合同类型
    if "战略合作" in title:
        score -= 2  # 框架协议含金量低
    if "中标" in title:
        score += 1
    
    # 上限20
    return min(score, 20)


def _score_restructure(title, company):
    """
    C5 资产重组评分
    基础16分，根据重组类型调整
    """
    score = 16
    
    if "借壳" in title:
        score = 20
    elif "发行股份购买资产" in title or "资产注入" in title:
        score = 18
    elif "重大资产出售" in title:
        score = 14
    elif "收购" in title:
        if any(w in title for w in ["%","股权","控股权"]):
            score = 17
        else:
            score = 14
    
    # ST相关风控减分
    if "ST" in company or "*ST" in company:
        score -= 4
    
    return min(score, 20)


def _score_earnings(title, company):
    """
    C7 业绩超预期评分
    基础12分，根据预增幅度上调
    """
    score = 12
    
    # 提取预增百分比
    pct_matches = re.findall(r'(\d+)%', title)
    for pct_str in pct_matches:
        pct = int(pct_str)
        if "下降" in title or "减少" in title or "亏损" in title:
            score = 0  # 业绩下降 → 不是催化剂
            break
        if pct >= 500:
            score = 20
        elif pct >= 200:
            score = 18
        elif pct >= 100:
            score = 16
        elif pct >= 50:
            score = 14
        elif pct >= 30:
            score = 12
    
    # 扭亏为盈 → 高弹性
    if "扭亏为盈" in title:
        score = 16
    
    # 业绩预告 vs 快报 (快报确定性更高)
    if "业绩快报" in title:
        score += 1
    
    # ST相关
    if "ST" in company or "*ST" in company:
        score -= 2
    
    return max(0, min(score, 20))


# ═══════════════════════════════════════════════
# 催化剂新鲜度衰减
# ═══════════════════════════════════════════════
HALF_LIFE = {
    "C3_重大合同": 5,
    "C5_资产重组": 10,
    "C7_业绩超预期": 7,
}


def calc_freshness(event_date_str, event_type, current_date=None):
    """计算催化剂新鲜度 0-1"""
    if current_date is None:
        current_date = datetime.now()
    elif isinstance(current_date, str):
        current_date = datetime.strptime(current_date, "%Y-%m-%d")
    
    event_date = datetime.strptime(event_date_str, "%Y-%m-%d")
    days = (current_date - event_date).days
    if days < 0:
        days = 0
    
    hl = HALF_LIFE.get(event_type, 7)
    return 2 ** (-days / hl)


# ═══════════════════════════════════════════════
# 催化剂强度分级 (D8)
# ═══════════════════════════════════════════════
def calc_strength_level(d7_score):
    """D7评分 → D8强度等级"""
    if d7_score >= 18:
        return {"level": "S", "score": 10, "label": "核弹级"}
    elif d7_score >= 15:
        return {"level": "A", "score": 7, "label": "重量级"}
    elif d7_score >= 12:
        return {"level": "B", "score": 5, "label": "中等级"}
    else:
        return {"level": "C", "score": 2, "label": "轻量级"}


# ═══════════════════════════════════════════════
# 主入口：扫描月度催化剂并关联个股评分
# ═══════════════════════════════════════════════
def scan_monthly_catalyst_scores(year_month, trade_date=None):
    """
    扫描指定月份的催化剂事件，返回每只个股的D7/D8得分

    Args:
        year_month: str, "202503"
        trade_date: str, 交易日 "YYYY-MM-DD" 用于衰减计算

    Returns:
        dict: {
            "stock_scores": {
                "000001": {"d7": 16, "d8": 7, "events": [...], "top_event_type": "C3"},
                ...
            },
            "total_events": int,
            "period": str,
        }
    """
    # 获取该月 + 前1月催化剂（保证新鲜度覆盖）
    y = int(year_month[:4])
    m = int(year_month[4:6])
    prev_m = m - 1
    prev_y = y
    if prev_m == 0:
        prev_m = 12
        prev_y -= 1
    
    all_events = []
    for ym in [f"{prev_y}{prev_m:02d}", year_month]:
        try:
            result = fetch_monthly_catalysts(ym)
            if result and "events" in result:
                all_events.extend(result["events"])
        except Exception as e:
            print(f"[catalyst] fetch {ym} error: {e}")
    
    # 逐事件评分
    stock_events = defaultdict(list)
    
    for evt in all_events:
        sec_code = evt["sec_code"]
        cat_type = evt["type"]
        title = evt["title"]
        company = evt["company"]
        
        # D7 精细化评分
        if cat_type == "C3_重大合同":
            d7_raw = _score_contract(title, company)
        elif cat_type == "C5_资产重组":
            d7_raw = _score_restructure(title, company)
        elif cat_type == "C7_业绩超预期":
            d7_raw = _score_earnings(title, company)
        else:
            d7_raw = evt.get("score_base", 10)
        
        # 新鲜度衰减
        freshness = calc_freshness(evt["date"], cat_type, trade_date)
        d7 = round(d7_raw * freshness, 1)
        
        if d7 < 4:  # 过于陈旧或弱信号跳过
            continue
        
        stock_events[sec_code].append({
            **evt,
            "d7": d7,
            "d7_raw": d7_raw,
            "freshness": round(freshness, 2),
        })
    
    # 汇总每只个股的D7/D8
    stock_scores = {}
    for code, events in stock_events.items():
        events.sort(key=lambda x: -x["d7"])
        best = events[0]
        
        # D7: 取最佳催化剂得分
        d7 = best["d7"]
        
        # D8: 强度等级
        strength = calc_strength_level(best["d7_raw"])
        d8 = strength["score"]
        
        # 多催化剂共振检测
        cat_types = set(e["type"] for e in events)
        if len(cat_types) >= 2:
            d8 += 2  # 多催化剂叠加加分
        if len(events) >= 3:
            d8 += 1
        
        stock_scores[code] = {
            "d7": round(min(d7, 20), 1),
            "d8": min(d8, 10),
            "events": events[:5],  # 最多保留5条
            "top_event_type": best["type"],
            "event_count": len(events),
            "resonance": len(cat_types) >= 2,
        }
    
    return {
        "stock_scores": stock_scores,
        "total_events": len(all_events),
        "active_stocks": len(stock_scores),
        "period": year_month,
    }


def scan_all_catalysts(year_month=None, trade_date=None):
    """
    综合扫描 C1(政策) + C3(合同) + C5(重组) + C7(业绩) 催化剂

    在 scan_monthly_catalyst_scores 基础上叠加 C1 政策扫描
    """
    # C3/C5/C7: cninfo公告催化剂
    result = scan_monthly_catalyst_scores(year_month, trade_date)
    stock_scores = result.get("stock_scores", {})

    # C1: 政策催化剂 (最近14天)
    try:
        from data.policy_scanner import fetch_policy_catalyst_scores
        policy_result = fetch_policy_catalyst_scores()
        policy_scores = policy_result.get("stock_scores", {})

        for code, ps in policy_scores.items():
            if code in stock_scores:
                existing = stock_scores[code]
                existing["d7"] = round(max(existing["d7"], ps["d7"] * 0.8), 1)
                existing["resonance"] = True
                existing["d8"] = min(existing.get("d8", 0) + 3, 10)
                if "C1_政策驱动" not in str(existing.get("top_event_type", "")):
                    existing["catalyst_types"] = existing.get("catalyst_types", [existing["top_event_type"]])
                    existing["catalyst_types"].append("C1_政策驱动")
            else:
                stock_scores[code] = {
                    "d7": ps["d7"], "d8": 7,
                    "events": [], "top_event_type": "C1_政策驱动",
                    "event_count": 1, "resonance": False,
                    "policy_themes": ps.get("policy_themes", []),
                }
    except Exception as e:
        print(f"[catalyst] C1 policy scan skipped: {e}")

    # C2: 商品景气催化剂
    try:
        from data.commodity_tracker import fetch_commodity_catalyst_scores
        comm_result = fetch_commodity_catalyst_scores()
        comm_scores = comm_result.get("stock_scores", {})

        for code, cs in comm_scores.items():
            if code in stock_scores:
                existing = stock_scores[code]
                existing["d7"] = round(max(existing["d7"], cs["d7"]), 1)
                existing["resonance"] = True
                existing["d8"] = min(existing.get("d8", 0) + 2, 10)
            else:
                stock_scores[code] = {
                    "d7": cs["d7"], "d8": 5,
                    "events": [], "top_event_type": "C2_商品景气",
                    "event_count": 1, "resonance": False,
                    "commodity": cs.get("commodity", ""),
                }
    except Exception as e:
        print(f"[catalyst] C2 commodity scan skipped: {e}")

    # C6: 概念板块热度
    try:
        from data.concept_heat_tracker import fetch_concept_catalyst_scores
        concept_result = fetch_concept_catalyst_scores()
        concept_scores = concept_result.get("stock_scores", {})

        for code, hs in concept_scores.items():
            if code in stock_scores:
                existing = stock_scores[code]
                existing["d7"] = round(max(existing["d7"], hs["d7"]), 1)
                existing["resonance"] = True
                existing["d8"] = min(existing.get("d8", 0) + 2, 10)
            else:
                stock_scores[code] = {
                    "d7": hs["d7"], "d8": 4,
                    "events": [], "top_event_type": "C6_概念热度",
                    "event_count": 1, "resonance": False,
                    "concept": hs.get("concept", ""),
                }
    except Exception as e:
        print(f"[catalyst] C6 concept scan skipped: {e}")

    result["stock_scores"] = stock_scores
    result["active_stocks"] = len(stock_scores)
    return result
