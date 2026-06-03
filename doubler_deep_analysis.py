#!/usr/bin/env python3
"""
翻倍股深度特征分析引擎
分析维度: 板块分布、启动价区间、市值、量能、连板特征
"""
import tushare as ts
import pandas as pd
import time
import json
import sys
from datetime import datetime, timedelta
from collections import Counter, defaultdict

sys.path.insert(0, '/root/shi-mi-dashboard')
from config import TUSHARE_TOKEN

pro = ts.pro_api(TUSHARE_TOKEN)

# 加载扫描结果
with open("/root/shi-mi-dashboard/monthly_doublers.json", "r") as f:
    data = json.load(f)

doublers = data["doublers"]
print(f"📊 分析 {len(doublers)} 只翻倍股...")

# 缓存
KLINE_CACHE = {}
KLINE_CACHE_TIME = {}

def get_kline(ts_code, days=90):
    key = f"{ts_code}_{days}"
    if key in KLINE_CACHE and time.time() - KLINE_CACHE_TIME.get(key,0) < 600:
        return KLINE_CACHE[key]
    
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=days+30)).strftime("%Y%m%d")
    
    try:
        df = pro.daily(ts_code=ts_code, start_date=start, end_date=end,
                       fields="trade_date,open,high,low,close,vol,amount,pct_chg")
        if df is not None and not isinstance(df, dict) and len(df) > 0:
            KLINE_CACHE[key] = df
            KLINE_CACHE_TIME[key] = time.time()
            return df
    except:
        pass
    return None

def get_daily_basic_safe(trade_date):
    """获取某日全市场市值/换手率数据"""
    try:
        df = pro.daily_basic(trade_date=trade_date,
                            fields="ts_code,total_mv,circ_mv,turnover_rate,volume_ratio")
        if df is not None and not isinstance(df, dict) and len(df) > 0:
            return df
    except:
        pass
    return None

# ============================================================
# 分析1: 板块分布
# ============================================================
print("\n" + "="*60)
print("📊 分析1: 板块分布")
print("="*60)

board_counts = Counter()
for d in doublers:
    code = d["code"]
    prefix = code[0]
    if prefix == '6':
        board = "主板(沪市60)"
    elif prefix == '0':
        board = "主板(深市00)"
    elif prefix == '3':
        board = "创业板(30)"
    elif prefix == '9' and len(code) == 6:
        board = "北交所(92)"
    elif prefix == '8' and len(code) == 6:
        board = "科创板(68)"
    elif prefix == '0' and code.startswith('00'):
        board = "中小板(00)"
    else:
        board = f"其他({prefix})"
    board_counts[board] += 1

for board, cnt in board_counts.most_common():
    pct = cnt/len(doublers)*100
    print(f"  {board}: {cnt}只 ({pct:.1f}%)")

# ============================================================
# 分析2: 启动价区间分布
# ============================================================
print("\n" + "="*60)
print("📊 分析2: 启动价区间分布")
print("="*60)

price_bins = [
    ("超低价(<10元)", lambda p: p < 10),
    ("低价(10-20元)", lambda p: 10 <= p < 20),
    ("中价(20-50元)", lambda p: 20 <= p < 50),
    ("中高价(50-100元)", lambda p: 50 <= p < 100),
    ("高价(100-500元)", lambda p: 100 <= p < 500),
    ("超高价(≥500元)", lambda p: p >= 500),
]

price_counts = Counter()
for d in doublers:
    p = d["first_close"]
    for label, fn in price_bins:
        if fn(p):
            price_counts[label] += 1
            break

for label, cnt in price_counts.most_common():
    pct = cnt/len(doublers)*100
    print(f"  {label}: {cnt}只 ({pct:.1f}%)")

# ============================================================
# 分析3: 连板特征 (需要K线数据)
# ============================================================
print("\n" + "="*60)
print("📊 分析3: 连板特征（抽样分析前20只）")
print("="*60)

sample = sorted(doublers, key=lambda x: -x["return_pct"])[:20]

consecutive_stats = []
for i, d in enumerate(sample):
    ts_code = d["ts_code"]
    kline = get_kline(ts_code, days=90)
    if kline is None:
        continue
    
    # 找到翻倍月的K线
    month = d["month"]
    month_kline = kline[kline["trade_date"].str.startswith(month)]
    if len(month_kline) == 0:
        continue
    
    # 统计涨停天数 (pct_chg >= 9.5%)
    limit_up_days = len(month_kline[month_kline["pct_chg"] >= 9.5])
    max_consecutive = 0
    current_run = 0
    for _, row in month_kline.iterrows():
        if row["pct_chg"] >= 9.5:
            current_run += 1
            max_consecutive = max(max_consecutive, current_run)
        else:
            current_run = 0
    
    # 月前一周的量能
    pre_month_end = datetime.strptime(month + "01", "%Y%m%d") - timedelta(days=1)
    pre_date = pre_month_end.strftime("%Y%m%d")
    pre_kline = kline[kline["trade_date"] <= pre_date].tail(5)
    
    avg_pre_vol = 0
    if len(pre_kline) > 0:
        avg_pre_vol = pre_kline["vol"].mean()
    
    month_avg_vol = month_kline["vol"].mean()
    vol_expand = month_avg_vol / avg_pre_vol if avg_pre_vol > 0 else 999
    
    consecutive_stats.append({
        "code": d["code"], "name": d["name"],
        "return": d["return_pct"],
        "limit_up_days": limit_up_days,
        "max_consecutive": max_consecutive,
        "trading_days": len(month_kline),
        "vol_expand": round(vol_expand, 1),
    })
    
    if i < 10:
        print(f"  {d['code']} {d['name']:<8s} +{d['return_pct']}% | "
              f"涨停{limit_up_days}天 | 最大连板{max_consecutive} | 量能放大{vol_expand:.1f}倍")

# 汇总
if consecutive_stats:
    avg_limit_up = sum(s["limit_up_days"] for s in consecutive_stats) / len(consecutive_stats)
    avg_consecutive = sum(s["max_consecutive"] for s in consecutive_stats) / len(consecutive_stats)
    avg_vol = sum(s["vol_expand"] for s in consecutive_stats) / len(consecutive_stats)
    print(f"\n  平均涨停天数: {avg_limit_up:.1f}天")
    print(f"  平均最大连板: {avg_consecutive:.1f}天")
    print(f"  平均量能放大: {avg_vol:.1f}倍")

# ============================================================
# 分析4: 启动时市值
# ============================================================
print("\n" + "="*60)
print("📊 分析4: 启动市值分析（抽样）")
print("="*60)

mv_sample = doublers[:30]  # 抽样
mv_stats = []

for i, d in enumerate(mv_sample):
    month = d["month"]
    # 获取月首日的市值
    # Find first trading day
    cal = pro.trade_cal(start_date=f"{month}01", end_date=f"{month}28", is_open="1")
    if cal is None or len(cal) == 0:
        continue
    first_day = cal.iloc[0]["cal_date"]
    
    basic = get_daily_basic_safe(first_day)
    if basic is None:
        continue
    
    row = basic[basic["ts_code"] == d["ts_code"]]
    if len(row) == 0:
        continue
    
    total_mv = float(row.iloc[0]["total_mv"])  # 万元
    circ_mv = float(row.iloc[0]["circ_mv"])     # 万元
    turnover = float(row.iloc[0]["turnover_rate"]) if row.iloc[0]["turnover_rate"] is not None else 0
    
    mv_stats.append({
        "code": d["code"], "name": d["name"],
        "total_mv_yi": round(total_mv / 1e4, 1),  # 亿
        "circ_mv_yi": round(circ_mv / 1e4, 1),
        "turnover": round(turnover, 2),
    })
    
    if i < 15:
        print(f"  {d['code']} {d['name']:<8s} | 总市值{total_mv/1e4:.1f}亿 | 流通{circ_mv/1e4:.1f}亿 | 换手{turnover:.1f}%")
    
    time.sleep(0.2)

if mv_stats:
    avg_total = sum(s["total_mv_yi"] for s in mv_stats) / len(mv_stats)
    avg_circ = sum(s["circ_mv_yi"] for s in mv_stats) / len(mv_stats)
    avg_turn = sum(s["turnover"] for s in mv_stats) / len(mv_stats)
    
    # 市值区间分布
    small = sum(1 for s in mv_stats if s["circ_mv_yi"] < 20)
    mid_small = sum(1 for s in mv_stats if 20 <= s["circ_mv_yi"] < 50)
    mid = sum(1 for s in mv_stats if 50 <= s["circ_mv_yi"] < 100)
    large = sum(1 for s in mv_stats if s["circ_mv_yi"] >= 100)
    
    total_n = len(mv_stats)
    print(f"\n  平均总市值: {avg_total:.1f}亿")
    print(f"  平均流通市值: {avg_circ:.1f}亿")
    print(f"  平均换手率: {avg_turn:.1f}%")
    print(f"\n  流通市值分布:")
    print(f"    小盘(<20亿): {small}只 ({small/total_n*100:.0f}%)")
    print(f"    中小盘(20-50亿): {mid_small}只 ({mid_small/total_n*100:.0f}%)")
    print(f"    中盘(50-100亿): {mid}只 ({mid/total_n*100:.0f}%)")
    print(f"    大盘(≥100亿): {large}只 ({large/total_n*100:.0f}%)")

# ============================================================
# 分析5: 翻倍路径分析
# ============================================================
print("\n" + "="*60)
print("📊 分析5: 翻倍路径分析（一字板 vs 换手板）")
print("="*60)

path_stats = {"一字连板": 0, "换手连板": 0, "趋势上涨": 0, "震荡上行": 0}

path_sample = doublers[:25]
for i, d in enumerate(path_sample):
    kline = get_kline(d["ts_code"], days=90)
    if kline is None:
        continue
    month = d["month"]
    mk = kline[kline["trade_date"].str.startswith(month)]
    if len(mk) == 0:
        continue
    
    # 统计一字板 (open≈close 且 pct_chg≥9.5)
    one_word = 0
    limit_up = 0
    for _, r in mk.iterrows():
        if r["pct_chg"] >= 9.5:
            limit_up += 1
            if (r["close"] - r["open"]) / r["open"] < 0.005:
                one_word += 1
    
    if limit_up == 0:
        path_stats["趋势上涨"] += 1
    elif one_word >= limit_up * 0.5:
        path_stats["一字连板"] += 1
    elif limit_up >= 3:
        path_stats["换手连板"] += 1
    else:
        path_stats["震荡上行"] += 1

for path, cnt in path_stats.items():
    pct = cnt/sum(path_stats.values())*100 if sum(path_stats.values()) > 0 else 0
    print(f"  {path}: {cnt}只 ({pct:.0f}%)")

print("\n✅ 深度分析完成")
