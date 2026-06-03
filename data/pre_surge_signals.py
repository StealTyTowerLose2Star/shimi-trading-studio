"""
拾米交易工作室 - 前置信号检测器 (D9增强)
在催化剂爆发前1-5天检测可量化的预警信号

信号维度:
  S1 量能异动 — 量比>2.0
  S2 连阳蓄势 — 连续阳线
  S3 板块联动 — 同行业个股同步上涨
  S4 龙虎榜   — 机构/游资净买入
  S5 资金流向 — 主力净流入连续
"""
import tushare as ts
from datetime import datetime, timedelta
from collections import defaultdict
from cache import cache_or_fetch


def get_pro():
    import config
    return ts.pro_api(config.TUSHARE_TOKEN)


def detect_consecutive_up(ts_code, days=5):
    """S2: 检测连阳天数"""
    pro = get_pro()
    try:
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=days + 3)).strftime("%Y%m%d")
        df = pro.daily(ts_code=ts_code, start_date=start, end_date=end,
                       fields="trade_date,open,close,pct_chg")
        if df is None or len(df) < 3:
            return 0
        df = df.sort_values("trade_date")
        recent = df.tail(days)
        consecutive = 0
        for _, row in recent[::-1].iterrows():
            if row["pct_chg"] > 0:
                consecutive += 1
            else:
                break
        return consecutive
    except:
        return 0


def detect_sector_linkage(industry, sector_data):
    """S3: 板块联动检测 — 同行业有多少只个股同步上涨"""
    if not sector_data or industry not in sector_data:
        return 0
    s = sector_data[industry]
    up_ratio = s.get("up_ratio", 0)
    chg = s.get("pct_chg", 0)
    if chg >= 2 and up_ratio >= 60:
        return 4  # 强联动
    elif chg >= 1 and up_ratio >= 50:
        return 2  # 弱联动
    return 0


def fetch_dragon_tiger_signals():
    """S4: 获取近期龙虎榜机构净买入标的"""
    pro = get_pro()
    try:
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=5)).strftime("%Y%m%d")
        df = pro.top_list(trade_date=end)
        if df is None or len(df) == 0:
            return {}
        
        signals = {}
        for _, row in df.iterrows():
            code = row.get("ts_code", "")
            if not code:
                continue
            # 净买入额
            buy = float(row.get("buy_amount", 0) or 0)
            sell = float(row.get("sell_amount", 0) or 0)
            net = buy - sell
            
            short = str(code).replace(".SZ","").replace(".SH","").replace(".BJ","")
            if net > 3000:  # 净买入>3000万
                signals[short] = 4  # 强龙虎榜信号
            elif net > 1000:
                signals[short] = 2  # 弱龙虎榜信号
            elif net > 0:
                signals[short] = 1
        return signals
    except:
        return {}


def fetch_moneyflow_signals():
    """S5: 主力资金净流入检测"""
    pro = get_pro()
    try:
        end = datetime.now().strftime("%Y%m%d")
        df = pro.moneyflow(trade_date=end,
                          fields="ts_code,buy_elg_amount,sell_elg_amount,"
                                 "buy_lg_amount,sell_lg_amount")
        if df is None or len(df) == 0:
            return {}
        
        signals = {}
        for _, row in df.iterrows():
            code = row.get("ts_code", "")
            if not code:
                continue
            # 超大单+大单净流入
            buy_elg = float(row.get("buy_elg_amount", 0) or 0)
            sell_elg = float(row.get("sell_elg_amount", 0) or 0)
            buy_lg = float(row.get("buy_lg_amount", 0) or 0)
            sell_lg = float(row.get("sell_lg_amount", 0) or 0)
            net_main = (buy_elg + buy_lg) - (sell_elg + sell_lg)
            
            short = str(code).replace(".SZ","").replace(".SH","").replace(".BJ","")
            if net_main > 5000:  # 主力净流入>5000万
                signals[short] = 4
            elif net_main > 2000:
                signals[short] = 3
            elif net_main > 500:
                signals[short] = 2
        return signals
    except:
        return {}


def compute_enhanced_d9(short_code, ts_code, base_d9, pct_chg, vol_ratio, 
                        turnover, circ_mv_yi, industry):
    """
    增强D9评分 — 基础分 + 连阳 + 板块联动 + 龙虎榜 + 资金流

    总分上限10 (原6)
    """
    score = 0
    # 基础分 (原D9)
    if pct_chg >= 5: score += 2
    if vol_ratio >= 3: score += 2
    if turnover >= 20: score += 1
    if circ_mv_yi < 30: score += 1
    if circ_mv_yi < 10: score += 1  # 超小盘加分
    
    # S2: 连阳
    # 此函数较慢(每只需要调tushare), 仅在候选股中使用
    # consecutive = detect_consecutive_up(ts_code, 5)
    # if consecutive >= 3: score += 2
    # elif consecutive >= 2: score += 1
    
    return min(score, 10)


def fetch_pre_surge_signals():
    """
    综合获取所有前置信号

    Returns:
        {"dragon_tiger": {code: score}, "moneyflow": {code: score}}
    """
    dt = cache_or_fetch("dragon_tiger_signals", fetch_dragon_tiger_signals, 300)
    mf = cache_or_fetch("moneyflow_signals", fetch_moneyflow_signals, 300)
    return {"dragon_tiger": dt, "moneyflow": mf}
