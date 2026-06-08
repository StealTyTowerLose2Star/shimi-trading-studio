"""
拾米交易工作室 - 每周复盘引擎
从 services/review.py 提取
"""
import json
from datetime import datetime, timedelta

import pandas as pd

from logger import get_logger

logger = get_logger("services.review_weekly")

from db import get_all_recommendations, save_review_report
from realtime_scorer import get_kline
from data.fetcher import fetch_sectors, fetch_sector_flow
from services.review import (get_current_price, tech_phase, ma_alignment,
                           volume_analysis)

# ─── 每周复盘 ──────────────────────────────────────────

def run_weekly_review() -> dict:
    """每周复盘（周日执行）

    流程：
    1. 获取本月所有推荐
    2. 找翻倍股（最新价 ≥ 推荐价 × 2）
    3. 对每只翻倍股进行技术面、板块面分析
    4. 给出操作建议
    5. 保存报告

    Returns:
        dict: 复盘报告内容
    """
    logger.info("开始每周复盘...")

    recs = get_all_recommendations(limit=200)
    if not recs:
        msg = "⚠️ 无推荐记录，跳过每周复盘"
        logger.info(msg)
        return {"error": msg, "items": [], "summary": {}}

    # 筛选当月推荐（按 generated_at 的月份）
    now = datetime.now()
    current_month = now.strftime("%Y-%m")
    month_recs = [
        r for r in recs
        if r.get('generated_at', '').startswith(current_month)
    ]

    if not month_recs:
        msg = f"⚠️ 本月({current_month})无推荐记录，跳过每周复盘"
        logger.info(msg)
        return {"error": msg, "items": [], "summary": {}}

    logger.info(f"本月推荐 {len(month_recs)} 条，正在查找翻倍股...")

    # 获取板块数据
    sector_flow = fetch_sector_flow() or []
    sectors = fetch_sectors() or []

    # 找出翻倍股
    doubler_items = []
    for rec in month_recs:
        code = rec.get('code', '')
        name = rec.get('name', '')
        rec_price = rec.get('price', 0) or 0

        if not code or rec_price <= 0:
            continue

        current_price = get_current_price(code)
        if current_price is None or current_price <= 0:
            continue

        # 翻倍条件：最新价 >= 推荐价 × 2
        if current_price < rec_price * 2:
            continue

        change_pct = round((current_price - rec_price) / rec_price * 100, 2)
        logger.info(f"发现翻倍股: {name}({code}) 推荐价¥{rec_price} → 现价¥{current_price} ({change_pct:+.2f}%)")

        # 技术分析
        kline = get_kline(code, days=120)
        phase = tech_phase(kline) if kline is not None else "数据不足"
        ma_align = ma_alignment(kline) if kline is not None else "数据不足"
        vol_analysis = volume_analysis(kline) if kline is not None else "数据不足"

        # 板块分析
        sector_analysis = _analyze_sector_for_stock(code, sector_flow)

        # 当时推荐理由 vs 当前走势
        reason_actual = rec.get('reason', '无')
        strategies = rec.get('strategies', '')

        # 当前操作建议
        advice = _generate_doubler_advice(phase, change_pct, vol_analysis, ma_align)

        doubler_items.append({
            "code": code,
            "name": name,
            "rec_price": rec_price,
            "current_price": round(current_price, 2),
            "change_pct": change_pct,
            "rec_reason": reason_actual,
            "strategies": strategies,
            "tech_phase": phase,
            "ma_alignment": ma_align,
            "volume_analysis": vol_analysis,
            "sector_analysis": sector_analysis,
            "advice": advice,
        })

    total_doublers = len(doubler_items)
    content = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "type": "weekly",
        "month": current_month,
        "total_month_recommendations": len(month_recs),
        "doubler_stocks": doubler_items,
        "summary": {
            "total_month_recommendations": len(month_recs),
            "doubler_count": total_doublers,
            "period": f"本月({current_month})翻倍股回顾",
        }
    }

    period_start = f"{current_month}-01"
    period_end = now.strftime("%Y-%m-%d")
    summary_text = (
        f"每周复盘: 本月推荐{len(month_recs)}只 · "
        f"发现翻倍股{total_doublers}只"
    )

    report_id = save_review_report(
        review_type="weekly",
        content=content,
        period_start=period_start,
        period_end=period_end,
        summary=summary_text,
    )

    content["report_id"] = report_id
    logger.info(f"每周复盘完成 (ID={report_id})")
    logger.info(summary_text)
    if total_doublers > 0:
        for d in doubler_items:
            logger.info(f"🏆 {d['name']}({d['code']}) +{d['change_pct']:.2f}% → {d['advice']}")
    return content


def _analyze_sector_for_stock(code: str, sector_flow: list) -> dict:
    """分析股票所在板块强度

    由于无法直接从股票代码反查行业（需要基础信息），
    我们返回板块整体热度分析。
    """
    result = {
        "hot_sectors": [],
        "overall": "未知",
    }
    if not sector_flow:
        return result

    hot = [s for s in sector_flow if s.get('hot')]
    top3 = sector_flow[:3]
    result["hot_sectors"] = [s['name'] for s in hot[:5]]
    result["top_sectors"] = [{'name': s['name'], 'strength': s['strength']} for s in top3]

    if hot:
        result["overall"] = "板块热点活跃 🔥"
    else:
        avg_strength = sum(s.get('strength', 0) for s in sector_flow[:5]) / max(len(sector_flow[:5]), 1)
        if avg_strength > 1:
            result["overall"] = "板块整体偏强"
        elif avg_strength < -1:
            result["overall"] = "板块整体偏弱"
        else:
            result["overall"] = "板块表现中性"

    return result


def _generate_doubler_advice(phase: str, change_pct: float,
                              vol_analysis: str, ma_align: str) -> str:
    """对翻倍股给出操作建议"""
    # 鱼尾 + 放量 → 警惕见顶
    if phase == "鱼尾":
        return "减仓 ⚠️ 已处鱼尾阶段，建议逐步减仓锁定利润"

    # 鱼身 + 多头排列 → 继续持有
    if phase == "鱼身" and "多头" in ma_align:
        if "放量" in vol_analysis:
            return "持有 ✅ 多头趋势延续，量能配合良好"
        else:
            return "持有 ✅ 多头排列完好，缩量整理后有望继续上行"

    # 鱼头 → 空间还大
    if phase == "鱼头":
        if change_pct < 150:
            return "持有 ✅ 处于鱼头阶段，涨幅有限，持有待涨"
        else:
            return "持有 ✅ 潜力仍在，注意回踩加仓机会"

    # 空头信号
    if "空头" in ma_align:
        return "清仓 ❌ 均线空头排列，建议清仓离场"

    if "死叉" in ma_align:
        return "减仓 ⚠️ 短期死叉信号，建议减仓观望"

    return "持有 ✅ 趋势尚可，继续观察"
