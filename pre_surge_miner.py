#!/usr/bin/env python3
"""
魔法师 - 启动前特征挖掘器
回测113只历史翻倍股，提取翻倍月之前20个交易日的微观特征
"""
import json, sys, time
from datetime import datetime, timedelta
from collections import defaultdict
import numpy as np

sys.path.insert(0, '/root/shimi-trading-studio')
from dotenv import load_dotenv; load_dotenv()
import config
import tushare as ts
pro = ts.pro_api(config.TUSHARE_TOKEN)

with open('/root/shimi-trading-studio/monthly_doublers.json') as f:
    data = json.load(f)

doublers = data['doublers']
print(f"=== 启动前特征挖掘：{len(doublers)}只翻倍股 ===")
print(f"{'='*60}")

pre_surge_features = []
failures = []

for i, d in enumerate(doublers):
    ts_code = d['ts_code']
    month = d['month']
    first_close = d['first_close']
    name = d['name']

    # Get first trading day of doubling month
    try:
        cal = pro.trade_cal(start_date=f"{month}01", end_date=f"{month}28", is_open="1")
        if cal is None or len(cal) == 0:
            failures.append(f"{ts_code} {name}: no calendar for {month}")
            continue
        cal = cal.sort_values('cal_date')
        first_trade_date = cal.iloc[0]['cal_date']
    except Exception as e:
        failures.append(f"{ts_code} {name}: calendar error {e}")
        continue

    # Get 60 calendar days before (≈40 trading days)
    start_dt = datetime.strptime(first_trade_date, '%Y%m%d') - timedelta(days=60)
    start_date = start_dt.strftime('%Y%m%d')

    try:
        df = pro.daily(ts_code=ts_code, start_date=start_date, end_date=first_trade_date,
                       fields='trade_date,open,high,low,close,vol,amount,pct_chg')
        if df is None or len(df) < 10:
            failures.append(f"{ts_code} {name}: only {len(df) if df is not None else 0} days")
            continue
        df = df.sort_values('trade_date')
    except Exception as e:
        failures.append(f"{ts_code} {name}: kline error {e}")
        continue

    pre_window = df.tail(20).copy()
    if len(pre_window) < 5:
        failures.append(f"{ts_code} {name}: pre_window too short ({len(pre_window)})")
        continue

    closes = pre_window['close'].values.astype(float)
    vols = pre_window['vol'].values.astype(float)
    pct_chgs = pre_window['pct_chg'].values.astype(float)
    highs = pre_window['high'].values.astype(float)
    lows = pre_window['low'].values.astype(float)

    nw = len(closes)
    half = max(nw // 2, 3)

    # Feature 1: Price compression
    first_half_c = closes[:half]
    second_half_c = closes[-half:]
    range_first = (first_half_c.max() - first_half_c.min()) / first_half_c.mean() * 100 if first_half_c.mean() > 0 else 0
    range_second = (second_half_c.max() - second_half_c.min()) / second_half_c.mean() * 100 if second_half_c.mean() > 0 else 0
    compression_ratio = range_second / range_first if range_first > 0 else 1

    # Feature 2: Pre-return
    pre_return = (closes[-1] / closes[0] - 1) * 100

    # Feature 3: Volume dry-up then expansion
    seg = max(nw // 5, 1)
    vol_first = vols[:seg].mean()
    vol_mid = vols[nw//3:2*nw//3].mean()
    vol_last = vols[-seg:].mean()
    vol_dry_up = vol_mid / vol_first if vol_first > 0 else 1
    vol_expand = vol_last / vol_mid if vol_mid > 0 else 1

    # Feature 4: ATR contraction
    trs = []
    for j in range(1, nw):
        tr = max(highs[j] - lows[j], abs(highs[j] - closes[j-1]), abs(lows[j] - closes[j-1]))
        trs.append(tr)
    atr_first = np.mean(trs[:half]) / closes[:half].mean() * 100 if closes[:half].mean() > 0 else 0
    atr_second = np.mean(trs[-half:]) / closes[-half:].mean() * 100 if closes[-half:].mean() > 0 else 0
    atr_contraction = atr_second / atr_first if atr_first > 0 else 1

    # Feature 5: Up-day ratio
    up_days = sum(1 for p in pct_chgs if p > 0)
    up_ratio = up_days / nw * 100

    # Feature 6: Max drawdown
    max_dd = 0.0
    peak = closes[0]
    for c in closes:
        if c > peak:
            peak = c
        dd = (c / peak - 1) * 100
        if dd < max_dd:
            max_dd = dd

    # Feature 7: Price position in range
    c_min, c_max = closes.min(), closes.max()
    price_position = (closes[-1] - c_min) / (c_max - c_min) * 100 if c_max > c_min else 50

    # Feature 8: Last 3-day momentum
    last3 = min(3, nw)
    last3_return = (closes[-1] / closes[-last3] - 1) * 100 if nw >= last3 else 0

    # Feature 9: Daily range compression
    daily_ranges = [(highs[j] - lows[j]) / closes[j] * 100 for j in range(nw)]
    range_first_half = np.mean(daily_ranges[:half])
    range_second_half = np.mean(daily_ranges[-half:])
    daily_range_compression = range_second_half / range_first_half if range_first_half > 0 else 1

    pre_surge_features.append({
        'code': d['code'], 'name': name, 'month': month,
        'industry': d['industry'],
        'first_close': first_close, 'return_pct': d['return_pct'],
        'pre_return': round(pre_return, 1),
        'compression_ratio': round(compression_ratio, 2),
        'vol_dry_up': round(vol_dry_up, 2),
        'vol_expand': round(vol_expand, 2),
        'atr_contraction': round(atr_contraction, 2),
        'daily_range_compression': round(daily_range_compression, 2),
        'up_ratio': round(up_ratio, 1),
        'max_dd': round(max_dd, 1),
        'price_position': round(price_position, 1),
        'last3_return': round(last3_return, 1),
    })

    if (i + 1) % 20 == 0:
        print(f"  进度: {i+1}/{len(doublers)}...")
    time.sleep(0.05)  # Rate limit

# === AGGREGATE ANALYSIS ===
features = pre_surge_features
n = len(features)

if n == 0:
    print("ERROR: No features extracted!")
    print("\nFailures:")
    for f in failures[:10]:
        print(f"  {f}")
    sys.exit(1)

print(f"\n{'='*60}")
print(f"📊 聚合分析：{n}只翻倍股启动前20日特征")
print(f"{'='*60}")

# --- Price Compression ---
comp_vals = [f['compression_ratio'] for f in features]
strong = sum(1 for v in comp_vals if v < 0.7)
moderate = sum(1 for v in comp_vals if 0.7 <= v < 1.0)
wide = sum(1 for v in comp_vals if v >= 1.0)
print(f"\n【价格压缩 (后段波幅/前段波幅)】")
print(f"  强压缩 (<0.7): {strong}只 ({strong/n*100:.0f}%) ← 翻倍前价格波动收窄")
print(f"  中度 (0.7-1.0): {moderate}只 ({moderate/n*100:.0f}%)")
print(f"  扩张 (>1.0):   {wide}只 ({wide/n*100:.0f}%)")
print(f"  均值: {np.mean(comp_vals):.2f}  中位数: {np.median(comp_vals):.2f}")

# --- Volume Pattern ---
vd = [f['vol_dry_up'] for f in features]
ve = [f['vol_expand'] for f in features]
quiet = sum(1 for v in vd if v < 0.8)
expanding = sum(1 for v in ve if v > 1.5)
coiled = sum(1 for i in range(n) if vd[i] < 0.8 and ve[i] > 1.2)
print(f"\n【量能模式】")
print(f"  缩量期 (<0.8):       {quiet}只 ({quiet/n*100:.0f}%)")
print(f"  末期放量 (>1.5):     {expanding}只 ({expanding/n*100:.0f}%)")
print(f"  缩量→放量(弹簧):     {coiled}只 ({coiled/n*100:.0f}%) ← 关键信号!")
print(f"  缩量均值: {np.mean(vd):.2f}  放量均值: {np.mean(ve):.2f}")

# --- ATR Contraction ---
atr = [f['atr_contraction'] for f in features]
atr_sq = sum(1 for v in atr if v < 0.8)
print(f"\n【ATR收缩 (后段/前段)】")
print(f"  收缩 (<0.8): {atr_sq}只 ({atr_sq/n*100:.0f}%) ← 波动率收窄")
print(f"  均值: {np.mean(atr):.2f}  中位数: {np.median(atr):.2f}")

# --- Daily Range Compression ---
dr = [f['daily_range_compression'] for f in features]
dr_sq = sum(1 for v in dr if v < 0.8)
print(f"\n【日内振幅收缩 (后段/前段)】")
print(f"  收缩 (<0.8): {dr_sq}只 ({dr_sq/n*100:.0f}%)")
print(f"  均值: {np.mean(dr):.2f}")

# --- Pre-Return ---
pr = [f['pre_return'] for f in features]
neg = sum(1 for v in pr if v < -5)
flat = sum(1 for v in pr if -5 <= v <= 10)
rising = sum(1 for v in pr if v > 10)
print(f"\n【启动前20日涨跌幅】")
print(f"  下跌(>5%):    {neg}只 ({neg/n*100:.0f}%)")
print(f"  横盘(-5~+10%): {flat}只 ({flat/n*100:.0f}%) ← 静默蓄力")
print(f"  上涨(>10%):    {rising}只 ({rising/n*100:.0f}%)")
print(f"  均值: {np.mean(pr):.1f}%  中位数: {np.median(pr):.1f}%")

# --- Price Position ---
pp = [f['price_position'] for f in features]
low = sum(1 for v in pp if v < 30)
mid = sum(1 for v in pp if 30 <= v <= 70)
high_pos = sum(1 for v in pp if v > 70)
print(f"\n【启动前价格位置 (在20日区间内)】")
print(f"  低位(<30%): {low}只 ({low/n*100:.0f}%)")
print(f"  中位:       {mid}只 ({mid/n*100:.0f}%)")
print(f"  高位(>70%): {high_pos}只 ({high_pos/n*100:.0f}%)")
print(f"  均值: {np.mean(pp):.1f}%")

# --- Up Ratio ---
up_r = [f['up_ratio'] for f in features]
print(f"\n【阳线占比】均值: {np.mean(up_r):.1f}%")

# --- Max DD ---
dd = [f['max_dd'] for f in features]
print(f"\n【最大回撤】均值: {np.mean(dd):.1f}%  中位数: {np.median(dd):.1f}%")

# --- Last 3 ---
l3 = [f['last3_return'] for f in features]
l3_up = sum(1 for v in l3 if v > 0)
print(f"\n【最后3日动量】均值: {np.mean(l3):.1f}%")
print(f"  上涨: {l3_up}只 ({l3_up/n*100:.0f}%)")

# === COMPOSITE PATTERN ===
# How many stocks showed 3+ compression signals
composite = 0
for f in features:
    signals = 0
    if f['compression_ratio'] < 0.9: signals += 1
    if f['atr_contraction'] < 0.9: signals += 1
    if f['daily_range_compression'] < 0.9: signals += 1
    if signals >= 2:
        composite += 1
print(f"\n【复合压缩信号 (2+/3项)】: {composite}只 ({composite/n*100:.0f}%)")

# === THE IDEAL PRE-SURGE PATTERN ===
ideal = 0
for f in features:
    score = 0
    if f['compression_ratio'] < 0.8: score += 1
    if f['atr_contraction'] < 0.8: score += 1
    if -5 <= f['pre_return'] <= 15: score += 1  # sideways before surge
    if f['price_position'] < 70: score += 1  # not at top of range
    if f['vol_dry_up'] < 0.9: score += 1  # volume drying up
    if score >= 4:
        ideal += 1
print(f"【理想启动前模式 (4+/5项)】: {ideal}只 ({ideal/n*100:.0f}%)")

# Save for later use
with open('/root/shimi-trading-studio/pre_surge_features.json', 'w') as f:
    json.dump({
        'scan_time': datetime.now().isoformat(),
        'total_analyzed': n,
        'features': pre_surge_features,
        'aggregate': {
            'price_compression_mean': round(np.mean(comp_vals), 2),
            'price_compression_median': round(np.median(comp_vals), 2),
            'atr_contraction_mean': round(np.mean(atr), 2),
            'vol_coiled_spring_pct': round(coiled/n*100, 1),
            'pre_flat_ratio': round(flat/n*100, 1),
            'composite_compression_pct': round(composite/n*100, 1),
            'ideal_pattern_pct': round(ideal/n*100, 1),
        }
    }, f, ensure_ascii=False, indent=2)

print(f"\n✅ 特征数据已保存到 pre_surge_features.json")
if failures:
    print(f"\n⚠️  {len(failures)} 只失败:")
    for f in failures[:5]:
        print(f"   {f}")
    if len(failures) > 5:
        print(f"   ... 还有 {len(failures)-5} 只")
