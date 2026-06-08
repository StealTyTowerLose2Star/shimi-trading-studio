"""
拾米交易工作室 - 复盘引擎
独立可执行的复盘分析模块（不依赖 Flask）
"""
import json
import re
import time
from datetime import datetime, timedelta

import pandas as pd

from logger import get_logger

from db import (
    get_recommendations,
    get_all_recommendations,
    save_review_report,
)
from realtime_scorer import get_kline
from data.fetcher import fetch_sectors, fetch_sector_flow

logger = get_logger("services.review")

# Re-export weekly review for backward compatibility
# (removed due to circular import — import from services.review_weekly directly)


# ─── 工具函数 ──────────────────────────────────────────

def parse_price_from_label(label: str) -> float:
    """从 '¥15.20 (-5.0%)' 格式中提取数值 15.20"""
    if not label or not isinstance(label, str):
        return 0.0
    m = re.search(r'¥([\d.]+)', label)
    if m:
        return float(m.group(1))
    return 0.0


def get_current_price(code: str) -> float:
    """获取个股最新收盘价"""
    try:
        kline = get_kline(code, days=10)
        if kline is not None and len(kline) > 0:
            return float(kline['close'].iloc[-1])
    except Exception:
        pass
    return None


def calc_ma20(code: str) -> float:
    """计算个股 MA20（20日均线），需至少 20 个交易日数据"""
    try:
        kline = get_kline(code, days=60)
        if kline is not None and len(kline) >= 20:
            return float(kline['close'].rolling(20).mean().iloc[-1])
    except Exception:
        pass
    return None


def tech_phase(kline: pd.DataFrame) -> str:
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


def ma_alignment(kline: pd.DataFrame) -> str:
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


def volume_analysis(kline: pd.DataFrame) -> str:
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


def analyze_failure_reason(code: str, name: str) -> str:
    """分析"不及预期"的原因：大盘影响/个股弱势/板块退潮"""
    reasons = []
    try:
        # 1. 大盘影响 — 检查三大指数最近5日表现
        indices = get_index_performance()
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


def get_index_performance() -> dict:
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
    logger.info("开始每日复盘...")

    recs = get_recommendations(days_ago=3)
    if not recs:
        msg = "⚠️ 3天前无推荐记录，跳过每日复盘"
        logger.info(msg)
        return {"error": msg, "items": [], "summary": {}}

    logger.info(f"共找到 {len(recs)} 条推荐记录")

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
        current_price = get_current_price(code)
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
        sl_price = parse_price_from_label(sl_label)
        t1_price = parse_price_from_label(t1_label)

        _hit_sl = sl_price > 0 and current_price <= sl_price
        _hit_t1 = t1_price > 0 and current_price >= t1_price
        _below_ma20 = False

        # MA20检查
        ma20 = calc_ma20(code)
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
            fail_reason = analyze_failure_reason(code, name)

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
    logger.info(f"每日复盘完成 (ID={report_id})")
    logger.info(summary_text)
    return content




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
        from services.review_weekly import run_weekly_review
        result = run_weekly_review()

    logger.info("报告摘要:")
    logger.info(json.dumps(result.get("summary", result), ensure_ascii=False, indent=2))
