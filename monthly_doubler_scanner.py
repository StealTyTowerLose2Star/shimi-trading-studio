#!/usr/bin/env python3
"""
月度翻倍股扫描引擎
扫描2025年至今每月涨幅≥100%的个股，分析共性特征
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

# 缓存
CACHE = {}
CACHE_TIME = {}

def cache_get(key):
    if key in CACHE and time.time() - CACHE_TIME.get(key, 0) < 3600:
        return CACHE[key]
    return None

def cache_set(key, val):
    CACHE[key] = val
    CACHE_TIME[key] = time.time()

def get_month_boundaries(start_month="202501", end_month="202606"):
    """获取每个月首尾交易日"""
    cal = pro.trade_cal(start_date=f"{start_month}01", end_date=f"{end_month}02", is_open="1")
    cal = cal.sort_values("cal_date")
    dates = cal["cal_date"].tolist()
    
    months = {}
    for d in dates:
        m = d[:6]
        if m not in months:
            months[m] = {"first": d, "last": d}
        else:
            months[m]["last"] = d
    return months

def get_daily_safe(trade_date, retry=3):
    """安全获取某日全市场数据"""
    key = f"daily_{trade_date}"
    cached = cache_get(key)
    if cached is not None:
        return cached
    
    for attempt in range(retry):
        try:
            df = pro.daily(trade_date=trade_date, 
                          fields="ts_code,close,pct_chg,amount,vol")
            if df is not None and not isinstance(df, dict) and len(df) > 0:
                cache_set(key, df)
                return df
        except Exception as e:
            print(f"  retry {attempt+1}: {e}")
            time.sleep(1)
    return None

def get_stock_basic():
    """获取股票基本信息（代码→名称/行业）"""
    cached = cache_get("stock_basic")
    if cached:
        return cached
    df = pro.stock_basic(exchange="", list_status="L", 
                         fields="ts_code,name,industry,market")
    result = {}
    for _, r in df.iterrows():
        result[r["ts_code"]] = {
            "name": r["name"], 
            "industry": r.get("industry", "未知"),
            "market": r.get("market", "")
        }
    cache_set("stock_basic", result)
    return result

def scan_monthly_doublers():
    """主扫描逻辑"""
    print("=" * 60)
    print("月度翻倍股扫描引擎 v1.0")
    print("=" * 60)
    
    months = get_month_boundaries()
    print(f"\n📅 扫描 {len(months)} 个月份: {list(months.keys())[0]} ~ {list(months.keys())[-1]}")
    
    stock_info = get_stock_basic()
    print(f"📊 股票基本信息: {len(stock_info)} 只")
    
    all_doublers = []  # (month, code, name, industry, first_close, last_close, return_pct)
    
    for i, (month_key, bounds) in enumerate(sorted(months.items())):
        first_date = bounds["first"]
        last_date = bounds["last"]
        
        # 同月同日跳过
        if first_date == last_date:
            continue
        
        print(f"\n🔍 [{i+1}/{len(months)}] {month_key}: {first_date} → {last_date}")
        
        # 获取首日和末日数据
        df_first = get_daily_safe(first_date)
        df_last = get_daily_safe(last_date)
        
        if df_first is None or df_last is None:
            print(f"  ⚠️ 数据缺失，跳过")
            continue
        
        # 构建价格映射
        first_price = {}
        for _, r in df_first.iterrows():
            first_price[r["ts_code"]] = float(r["close"])
        
        last_price = {}
        for _, r in df_last.iterrows():
            last_price[r["ts_code"]] = float(r["close"])
        
        # 计算月度回报
        month_doublers = []
        for code, close_last in last_price.items():
            close_first = first_price.get(code)
            if close_first and close_first > 0:
                ret = (close_last / close_first - 1) * 100
                if ret >= 100:
                    info = stock_info.get(code, {"name": "?", "industry": "未知"})
                    month_doublers.append({
                        "month": month_key,
                        "code": code.replace(".SZ","").replace(".SH","").replace(".BJ",""),
                        "ts_code": code,
                        "name": info["name"],
                        "industry": info["industry"],
                        "first_close": round(close_first, 2),
                        "last_close": round(close_last, 2),
                        "return_pct": round(ret, 1),
                    })
        
        if month_doublers:
            month_doublers.sort(key=lambda x: -x["return_pct"])
            for d in month_doublers:
                print(f"  🚀 {d['code']} {d['name']:<8s} | ¥{d['first_close']:.2f}→¥{d['last_close']:.2f} | +{d['return_pct']:.1f}% | {d['industry']}")
            all_doublers.extend(month_doublers)
        else:
            print(f"  📉 本月无翻倍股")
        
        # 避免被限频
        time.sleep(0.5)
    
    # 汇总统计
    print("\n" + "=" * 60)
    print("📊 汇总统计")
    print("=" * 60)
    print(f"总计翻倍股: {len(all_doublers)} 只")
    
    if all_doublers:
        df = pd.DataFrame(all_doublers)
        
        # 按月份统计
        print(f"\n📅 按月份分布:")
        month_counts = df.groupby("month").size().sort_index()
        for m, c in month_counts.items():
            top = df[df["month"]==m].iloc[0]
            print(f"  {m}: {c}只  最强: {top['code']} {top['name']} +{top['return_pct']}%")
        
        # 按行业统计
        print(f"\n🏭 热门行业 TOP10:")
        ind_counts = df["industry"].value_counts().head(10)
        for ind, c in ind_counts.items():
            print(f"  {ind}: {c}只")
        
        # 涨幅分布
        print(f"\n📈 涨幅分布:")
        bins = [(100,150), (150,200), (200,300), (300,500), (500,10000)]
        for lo, hi in bins:
            cnt = len(df[(df["return_pct"]>=lo) & (df["return_pct"]<hi)])
            if cnt > 0:
                print(f"  {lo}%-{hi}%: {cnt}只")
        
        # 导出JSON
        output = {
            "scan_time": datetime.now().isoformat(),
            "total_doublers": len(all_doublers),
            "doublers": all_doublers,
            "by_month": {m: int(c) for m, c in month_counts.items()},
            "by_industry": {ind: int(c) for ind, c in ind_counts.head(15).items()},
        }
        
        out_path = "/root/shi-mi-dashboard/monthly_doublers.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        print(f"\n💾 结果已保存: {out_path}")
        
        return df
    return None

if __name__ == "__main__":
    scan_monthly_doublers()
