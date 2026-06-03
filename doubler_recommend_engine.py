#!/usr/bin/env python3
"""
当月翻倍潜力股推荐引擎
基于历史113只翻倍股特征构建评分模型
"""
import tushare as ts
import pandas as pd
import time
import json
import sys
from datetime import datetime, timedelta
from collections import defaultdict

sys.path.insert(0, '/root/shi-mi-dashboard')
from config import TUSHARE_TOKEN

pro = ts.pro_api(TUSHARE_TOKEN)

# ============================================================
# 历史翻倍股特征统计 → 评分权重
# ============================================================
# 启动价分布: <10元:26%, 10-20元:27%, 20-50元:31%, 50-100元:12%, >100元:5%
PRICE_SCORE = {
    "ultra_low": 30,   # <10
    "low": 25,         # 10-20
    "mid": 20,         # 20-50
    "mid_high": 10,    # 50-100
    "high": 5,         # >100
}

# 热门行业 (按历史翻倍股行业排名)
HOT_INDUSTRIES = [
    "软件服务", "元器件", "电气设备", "专用机械", "化工原料",
    "机械基件", "半导体", "化学制药", "影视音像", "汽车配件",
    "通信设备", "生物制药", "小金属", "航空", "医疗保健",
    "建筑工程", "染料涂料", "医药商业", "机床制造", "工程机械",
    "火力发电", "环境保护",
]

# 流通市值偏好: 中小盘为主
MV_SCORE = {
    "micro": 25,    # <20亿
    "small": 20,    # 20-50亿
    "mid": 15,      # 50-100亿
    "large": 8,     # 100-300亿
    "xl": 3,        # >300亿
}

def price_score(close):
    """启动价评分"""
    if close < 10: return PRICE_SCORE["ultra_low"]
    elif close < 20: return PRICE_SCORE["low"]
    elif close < 50: return PRICE_SCORE["mid"]
    elif close < 100: return PRICE_SCORE["mid_high"]
    else: return PRICE_SCORE["high"]

def industry_score(industry):
    """行业热度评分"""
    if industry in HOT_INDUSTRIES[:7]: return 20   # TOP7 高权重
    elif industry in HOT_INDUSTRIES: return 15       # 上榜行业
    else: return 8                                    # 其他

def mv_score(circ_mv_yi):
    """流通市值评分"""
    if circ_mv_yi < 20: return MV_SCORE["micro"]
    elif circ_mv_yi < 50: return MV_SCORE["small"]
    elif circ_mv_yi < 100: return MV_SCORE["mid"]
    elif circ_mv_yi < 300: return MV_SCORE["large"]
    else: return MV_SCORE["xl"]

def momentum_score(pct_chg, vol_ratio):
    """动量评分：结合涨幅和量比"""
    score = 0
    if pct_chg >= 9.5: score += 12      # 涨停
    elif pct_chg >= 5: score += 8
    elif pct_chg >= 3: score += 5
    elif pct_chg >= 0: score += 3
    else: score += 0                    # 下跌不加分
    
    if vol_ratio and vol_ratio >= 3: score += 8     # 放量
    elif vol_ratio and vol_ratio >= 2: score += 5
    elif vol_ratio and vol_ratio >= 1.5: score += 3
    else: score += 1
    
    return score

def turnover_score(turnover):
    """换手率评分 - 翻倍股平均换手23.4%"""
    if turnover is None: return 3
    if turnover >= 25: return 10
    elif turnover >= 15: return 8
    elif turnover >= 10: return 5
    elif turnover >= 5: return 3
    else: return 1

def board_score(code):
    """板块加分"""
    if code.startswith('6'): return 5   # 沪市主板(47%)
    elif code.startswith('3'): return 3 # 创业板(27%)
    elif code.startswith('0'): return 2 # 深市主板(18%)
    elif code.startswith('9'): return 1 # 北交所(8%)
    else: return 0

def main():
    print("=" * 60)
    print("🚀 拾米交易工作室 — 当月翻倍潜力股推荐引擎")
    print("=" * 60)
    print(f"⏰ 分析时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    
    # Step 1: 获取今日全市场数据
    print("\n📡 Step 1: 获取今日全市场行情...")
    today = "20260602"
    
    daily = pro.daily(trade_date=today,
                      fields="ts_code,open,high,low,close,pct_chg,amount,vol")
    if daily is None or len(daily) == 0:
        print("❌ 今日数据为空")
        return
    
    print(f"   全市场: {len(daily)} 只股票")
    
    # Step 2: 获取换手率/量比
    print("\n📡 Step 2: 获取量能数据...")
    basic = pro.daily_basic(trade_date=today,
                           fields="ts_code,total_mv,circ_mv,turnover_rate,volume_ratio")
    
    # Step 3: 获取股票信息
    print("\n📡 Step 3: 获取股票基本信息...")
    stock_info = pro.stock_basic(exchange="", list_status="L",
                                 fields="ts_code,name,industry")
    info_map = {}
    for _, r in stock_info.iterrows():
        info_map[r["ts_code"]] = {
            "name": r["name"],
            "industry": r.get("industry", "未知"),
        }
    print(f"   股票信息: {len(info_map)} 只")
    
    # Step 4: 合并数据并过滤
    print("\n📡 Step 4: 合并数据...")
    basic_map = {}
    if basic is not None and len(basic) > 0:
        for _, r in basic.iterrows():
            basic_map[r["ts_code"]] = r
    
    # Step 5: 评分
    print("\n📊 Step 5: 多维度评分...")
    candidates = []
    
    for _, row in daily.iterrows():
        code = row["ts_code"]
        close = float(row["close"])
        pct_chg = float(row["pct_chg"])
        vol = float(row["vol"])
        amount = float(row["amount"])
        
        # 过滤: ST, 新股, 停牌
        if "ST" in code: continue
        if close <= 0 or vol <= 0: continue
        
        info = info_map.get(code, {"name": "?", "industry": "未知"})
        basic_row = basic_map.get(code, None)
        
        circ_mv = float(basic_row["circ_mv"]) / 1e4 if basic_row is not None and basic_row["circ_mv"] is not None else 0
        total_mv = float(basic_row["total_mv"]) / 1e4 if basic_row is not None and basic_row["total_mv"] is not None else 0
        turnover = float(basic_row["turnover_rate"]) if basic_row is not None and basic_row["turnover_rate"] is not None else 0
        vol_ratio = float(basic_row["volume_ratio"]) if basic_row is not None and basic_row["volume_ratio"] is not None else 0
        
        short_code = code.replace(".SZ","").replace(".SH","").replace(".BJ","")
        
        # 多维度评分
        ps = price_score(close)
        ind_s = industry_score(info["industry"])
        mv_s = mv_score(circ_mv) if circ_mv > 0 else 5
        mom_s = momentum_score(pct_chg, vol_ratio)
        turn_s = turnover_score(turnover)
        board_s = board_score(short_code)
        
        total_score = ps + ind_s + mv_s + mom_s + turn_s + board_s
        
        candidates.append({
            "code": short_code,
            "ts_code": code,
            "name": info["name"],
            "industry": info["industry"],
            "close": close,
            "pct_chg": round(pct_chg, 2),
            "circ_mv_yi": round(circ_mv, 2),
            "total_mv_yi": round(total_mv, 2),
            "turnover": round(turnover, 2),
            "vol_ratio": round(vol_ratio, 2) if vol_ratio else 0,
            "amount_yi": round(amount / 1e5, 2),
            "score": total_score,
            "score_breakdown": {
                "price": ps, "industry": ind_s, "market_cap": mv_s,
                "momentum": mom_s, "turnover": turn_s, "board": board_s,
            }
        })
    
    # 排序并输出
    candidates.sort(key=lambda x: -x["score"])
    
    print(f"\n{'='*60}")
    print("🏆 TOP30 翻倍潜力股排名")
    print(f"{'='*60}")
    
    top30 = candidates[:30]
    for i, c in enumerate(top30):
        sb = c["score_breakdown"]
        print(f"\n  #{i+1:2d}  {c['code']} {c['name']:<8s} | 总分:{c['score']:3d}")
        print(f"       行业:{c['industry']:<8s} | 现价:¥{c['close']:.2f} | "
              f"涨幅:{c['pct_chg']:+.2f}% | 换手:{c['turnover']:.1f}% | 量比:{c['vol_ratio']:.1f}")
        print(f"       流通:{c['circ_mv_yi']:.1f}亿 | 成交:{c['amount_yi']:.1f}亿")
        print(f"       得分: 价格{sb['price']} + 行业{sb['industry']} + 市值{sb['market_cap']} "
              f"+ 动量{sb['momentum']} + 换手{sb['turnover']} + 板块{sb['board']}")
    
    # 行业热度
    print(f"\n{'='*60}")
    print("🔥 当前热门行业评分 (基于翻倍股行业分布)")
    print(f"{'='*60}")
    
    industry_scores = defaultdict(lambda: {"count": 0, "total_score": 0, "top_stocks": []})
    for c in top30:
        ind = c["industry"]
        industry_scores[ind]["count"] += 1
        industry_scores[ind]["total_score"] += c["score"]
        if len(industry_scores[ind]["top_stocks"]) < 3:
            industry_scores[ind]["top_stocks"].append(f"{c['code']} {c['name']} {c['score']}分")
    
    sorted_ind = sorted(industry_scores.items(), key=lambda x: -x[1]["total_score"])
    for ind, data in sorted_ind[:10]:
        print(f"  {ind}: {data['count']}只入选 | "
              f"代表: {', '.join(data['top_stocks'][:2])}")
    
    # 浓缩推荐
    print(f"\n{'='*60}")
    print("💎 核心推荐池 (评分≥75, 优选10只)")
    print(f"{'='*60}")
    
    elite = [c for c in top30 if c["score"] >= 70][:10]
    if len(elite) < 5:
        elite = top30[:8]
    
    for i, c in enumerate(elite):
        print(f"  #{i+1} {c['code']} {c['name']:<8s} | ¥{c['close']:.2f} | "
              f"{c['industry']} | 评分{c['score']} | 流通{c['circ_mv_yi']:.1f}亿")
    
    # 保存结果
    output = {
        "scan_time": datetime.now().isoformat(),
        "trade_date": today,
        "total_scanned": len(candidates),
        "top30": top30,
        "elite_picks": elite,
    }
    with open("/root/shi-mi-dashboard/current_month_picks.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    
    print(f"\n💾 结果已保存: current_month_picks.json")
    print(f"✅ 分析完成")

if __name__ == "__main__":
    main()
