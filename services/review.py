"""
拾米交易工作室 - 复盘引擎
独立可执行的复盘分析模块（不依赖 Flask）

功能：
1. run_daily_review() — 每日复盘（收盘后执行）
2. run_weekly_review() — 每周复盘（周日执行）
"""

import json
import re
import time
from datetime import datetime, timedelta

import pandas as pd

from db import (
    get_recommendations,
    get_all_recommendations,
    save_review_report,
)
from realtime_scorer import get_kline
from data.fetcher import fetch_sectors, fetch_sector_flow


# ─── 工具函数 ──────────────────────────────────────────

def _parse_price_from_label(label: str) -> float:
    """从 '¥15.20 (-5.0%)' 格式中提取数值 15.20"""
    if not label or not isinstance(label, str):
        return 0.0
    m = re.search(r'¥([\d.]+)', label)
    if m:
        return float(m.group(1))
    return 0.0


def _get_current_price(code: str) -> float:
    """获取个股最新收盘价"""
    try:
        kline = get_kline(code, days=10)
        if kline is not None and len(kline) > 0:
            return float(kline['close'].iloc[-1])
    except Exception:
        pass
    return None


def _calc_ma20(code: str) -> float:
    """计算个股 MA20（20日均线），需至少 20 个交易日数据"""
    try:
        kline = get_kline(code, days=60)
        if kline is not None and len(kline) >= 20:
            return float(kline['close'].rolling(20).mean().iloc[-1])
    except Exception:
        pass
    return None


def _tech_phase(kline: pd.DataFrame) -> str:
    """判断股票当前技术阶段：鱼头/鱼身/鱼尾"""
    if kline is None or len(kline) < 30:
        return "未知"
    close = kline['close']
    ma5 = close.rolling(5).mean()
    ma10 = close.rolling(10).mean()
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()

    c5, c10, c20, c60 = ma5.iloc[-1], ma10.iloc[-1], ma20.iloc[-1], ma60.iloc[-1]
    if pd.isna(c60):
        return "鱼头"  # 上市不久，刚启动

    # 多头排列 + 价格远离 MA20（涨幅>20%）→ 鱼尾
    if c5 > c10 > c20 > c60:
        dist = (close.iloc[-1] - c20) / c20 * 100
        if dist > 20:
            return "鱼尾"

    # MA5 > MA10 > MA20 多头排列初期 → 鱼身
    if c5 > c10 > c20:
        return "鱼身"

    # 均线刚金叉或还在底部 → 鱼头
    if c20 > c60 and c10 > c20:
        return "鱼头"

    return "鱼身"


def _ma_alignment(kline: pd.DataFrame) -> str:
    """MA排列描述"""
    if kline is None or len(kline) < 20:
        return "数据不足"
    close = kline['close']
    ma5 = close.rolling(5).mean().iloc[-1]
    ma10 = close.rolling(10).mean().iloc[-1]
    ma20 = close.rolling(20).mean().iloc[-1]
    if pd.isna(ma10) or pd.isna(ma20):
        return "数据不足"
    if ma5 > ma10 > ma20:
        return "多头排列 📈"
    elif ma5 < ma10 < ma20:
        return "空头排列 📉"
    elif ma5 > ma10 and ma10 < ma20:
        return "短期金叉 ⤴️"
    elif ma5 < ma10 and ma10 > ma20:
        return "短期死叉 ⤵️"
    else:
        return "震荡整理 ➡️"


def _volume_analysis(kline: pd.DataFrame) -> str:
    """成交量变化分析"""
    if kline is None or len(kline) < 10:
        return "数据不足"
    vol = kline['vol']
    avg_vol_5 = vol.tail(5).mean()
    avg_vol_20 = vol.tail(20).mean() if len(vol) >= 20 else vol.mean()
    ratio = avg_vol_5 / avg_vol_20 if avg_vol_20 > 0 else 1
    if ratio > 1.5:
        return "放量 ↑"
    elif ratio < 0.7:
        return "缩量 ↓"
    else:
        return "量能正常 →"


def _analyze_failure_reason(code: str, name: str) -> str:
    """分析"不及预期"的原因：大盘影响/个股弱势/板块退潮"""
    reasons = []
    try:
        # 1. 大盘影响 — 检查三大指数最近5日表现
        indices = _get_index_performance()
        if indices:
            avg_idx_chg = sum(indices.values()) / len(indices)
            if avg_idx_chg < -3:
                reasons.append(f"大盘拖累(指数平均{avg_idx_chg:.1f}%)")
            elif avg_idx_chg < -1:
                reasons.append(f"大盘偏弱(指数平均{avg_idx_chg:.1f}%)")
    except Exception:
        pass

    try:
        # 2. 板块退潮 — 检查该股票所属板块强度
        sectors = fetch_sector_flow()
        if sectors and isinstance(sectors, list):
            # 检查板块是否在热点榜前15名以外或排名靠后
            top_sector_names = [s['name'] for s in sectors[:5]]
            bottom_sector_names = [s['name'] for s in sectors[-5:]]
            # 我们需要股票基础信息来找行业
            reason_parts = []
            for s in sectors:
                if s['strength'] < -1:
                    reason_parts.append(f"{s['name']}偏弱({s['strength']})")
            if reason_parts:
                reasons.append("板块退潮: " + "; ".join(reason_parts[:2]))
    except Exception:
        pass

    # 3. 个股弱势（如果前面没有足够原因）
    try:
        kline = get_kline(code, days=30)
        if kline is not None and len(kline) >= 5:
            recent_chg = (float(kline['close'].iloc[-1]) - float(kline['close'].iloc[-5])) / float(kline['close'].iloc[-5]) * 100
            if recent_chg < -10:
                reasons.append(f"个股持续走弱(近5日跌幅{recent_chg:.1f}%)")
            elif recent_chg < -5:
                reasons.append(f"个股回调(近5日跌幅{recent_chg:.1f}%)")
    except Exception:
        pass

    if not reasons:
        reasons.append("多重因素综合影响")

    return "；".join(reasons)


def _get_index_performance() -> dict:
    """获取三大指数近5日涨跌幅"""
    result = {}
    try:
        from data.fetcher import fetch_indices
        indices = fetch_indices()
        for idx in indices:
            result[idx['name']] = idx.get('change', 0)
    except Exception:
        pass
    return result


# ─── 每日复盘 ──────────────────────────────────────────

def run_daily_review() -> dict:
    """每日复盘（收盘后执行）

    流程：
    1. 获取3天前的推荐
    2. 获取每个推荐股票最新价
    3. 计算涨跌幅、止损/止盈触发、MA20跌破
    4. 分类：符合预期 / 超预期 / 不及预期
    5. 不及预期分析原因
    6. 保存报告

    Returns:
        dict: 复盘报告内容
    """
    print("📊 开始每日复盘...")

    recs = get_recommendations(days_ago=3)
    if not recs:
        msg = "⚠️ 3天前无推荐记录，跳过每日复盘"
        print(msg)
        return {"error": msg, "items": [], "summary": {}}

    print(f"📋 共找到 {len(recs)} 条推荐记录")

    items = []
    total_pnl = 0.0
    count_meet = 0
    count_exceed = 0
    count_fail = 0
    hit_stop_loss = 0
    hit_target = 0
    below_ma20 = 0

    for rec in recs:
        code = rec.get('code', '')
        name = rec.get('name', '')
        rec_price = rec.get('price', 0) or 0
        sl_label = rec.get('stop_loss', '')
        t1_label = rec.get('target_1', '')

        if not code or rec_price <= 0:
            continue

        # 获取最新价
        current_price = _get_current_price(code)
        if current_price is None or current_price <= 0:
            items.append({
                "code": code, "name": name,
                "rec_price": rec_price, "current_price": None,
                "change_pct": None, "category": "数据不足",
                "note": "无法获取最新行情"
            })
            continue

        # 涨跌幅
        change_pct = round((current_price - rec_price) / rec_price * 100, 2)

        # 止损/止盈检查
        sl_price = _parse_price_from_label(sl_label)
        t1_price = _parse_price_from_label(t1_label)

        _hit_sl = sl_price > 0 and current_price <= sl_price
        _hit_t1 = t1_price > 0 and current_price >= t1_price
        _below_ma20 = False

        # MA20检查
        ma20 = _calc_ma20(code)
        if ma20 is not None:
            _below_ma20 = current_price < ma20

        # 分类
        if change_pct > 10:
            category = "超预期 🎉"
            count_exceed += 1
        elif change_pct < -5:
            category = "不及预期 ⚠️"
            count_fail += 1
        else:
            category = "符合预期 ✅"
            count_meet += 1

        total_pnl += change_pct
        if _hit_sl:
            hit_stop_loss += 1
        if _hit_t1:
            hit_target += 1
        if _below_ma20:
            below_ma20 += 1

        # 不及预期原因分析
        fail_reason = None
        if change_pct < -5:
            fail_reason = _analyze_failure_reason(code, name)

        item = {
            "code": code,
            "name": name,
            "rec_price": rec_price,
            "current_price": round(current_price, 2),
            "change_pct": change_pct,
            "category": category,
            "hit_stop_loss": _hit_sl,
            "hit_target": _hit_t1,
            "below_ma20": _below_ma20,
        }
        if fail_reason:
            item["fail_reason"] = fail_reason
        if sl_price > 0:
            item["stop_loss"] = round(sl_price, 2)
        if t1_price > 0:
            item["target_1"] = round(t1_price, 2)
        if ma20 is not None:
            item["ma20"] = round(ma20, 2)

        items.append(item)

    avg_pnl = round(total_pnl / len(items), 2) if items else 0
    total = len(items)

    summary = {
        "total_recommendations": total,
        "avg_change_pct": avg_pnl,
        "exceed_expectations": count_exceed,
        "met_expectations": count_meet,
        "below_expectations": count_fail,
        "hit_stop_loss": hit_stop_loss,
        "hit_target": hit_target,
        "below_ma20": below_ma20,
        "period": "3天前推荐回顾",
    }

    content = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "type": "daily",
        "items": items,
        "summary": summary,
    }

    period_end = datetime.now().strftime("%Y-%m-%d")
    period_start = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
    summary_text = (
        f"每日复盘: {total}只推荐 · 平均涨跌幅{avg_pnl:+.2f}% · "
        f"超预期{count_exceed}只 · 符合预期{count_meet}只 · "
        f"不及预期{count_fail}只 · 触发止损{hit_stop_loss}只"
    )

    report_id = save_review_report(
        review_type="daily",
        content=content,
        period_start=period_start,
        period_end=period_end,
        summary=summary_text,
    )

    content["report_id"] = report_id
    print(f"✅ 每日复盘完成 (ID={report_id})")
    print(f"   {summary_text}")
    return content


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
    print("📊 开始每周复盘...")

    recs = get_all_recommendations(limit=200)
    if not recs:
        msg = "⚠️ 无推荐记录，跳过每周复盘"
        print(msg)
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
        print(msg)
        return {"error": msg, "items": [], "summary": {}}

    print(f"📋 本月推荐 {len(month_recs)} 条，正在查找翻倍股...")

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

        current_price = _get_current_price(code)
        if current_price is None or current_price <= 0:
            continue

        # 翻倍条件：最新价 >= 推荐价 × 2
        if current_price < rec_price * 2:
            continue

        change_pct = round((current_price - rec_price) / rec_price * 100, 2)
        print(f"  🔍 发现翻倍股: {name}({code}) 推荐价¥{rec_price} → 现价¥{current_price} ({change_pct:+.2f}%)")

        # 技术分析
        kline = get_kline(code, days=120)
        phase = _tech_phase(kline) if kline is not None else "数据不足"
        ma_align = _ma_alignment(kline) if kline is not None else "数据不足"
        vol_analysis = _volume_analysis(kline) if kline is not None else "数据不足"

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
    print(f"✅ 每周复盘完成 (ID={report_id})")
    print(f"   {summary_text}")
    if total_doublers > 0:
        for d in doubler_items:
            print(f"   🏆 {d['name']}({d['code']}) +{d['change_pct']:.2f}% → {d['advice']}")
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


# ─── 独立执行入口 ──────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="复盘引擎")
    parser.add_argument("--type", choices=["daily", "weekly"], default="daily",
                        help="复盘类型 (默认: daily)")
    args = parser.parse_args()

    if args.type == "daily":
        result = run_daily_review()
    else:
        result = run_weekly_review()

    print("\n📄 报告摘要:")
    print(json.dumps(result.get("summary", result), ensure_ascii=False, indent=2))
