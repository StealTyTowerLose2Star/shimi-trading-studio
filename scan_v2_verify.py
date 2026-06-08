#!/usr/bin/env python3
"""
魔法师 V2 启动前期扫描 — 快速验证脚本
用法: cd /root/shimi-trading-studio && python3 scan_v2_verify.py
"""
from dotenv import load_dotenv; load_dotenv()
import json, time
from services.doubler_scanner import recommend_current_month

print("🔮 魔法师 V2 启动前期扫描开始...")
t0 = time.time()
result = recommend_current_month()
elapsed = time.time() - t0

print(f"✅ 扫描完成 (耗时 {elapsed:.1f}s)")
print(f"交易日: {result.get('trade_date')}")
print(f"全市场候选: {result.get('total_scanned')} 只")
print()

top30 = result.get('top30', [])

# Top 15 with early-stage pattern
print(f"{'#':>3} {'代码':<8} {'名称':<10} {'总分':>5} {'D0':>4} {'启动模式':<22} {'信号'}")
print('-' * 90)
for i, c in enumerate(top30[:15]):
    cat = c.get('catalyst', {})
    d0 = cat.get('d0_early_stage', 0)
    pattern = cat.get('early_pattern', '-')
    reason = cat.get('early_reason', '-')
    print(f"{i+1:>3} {c['code']:<8} {c['name']:<10} {c['score']:>5} {d0:>4} {pattern:<22} {reason}")

print()

# Pattern distribution
from collections import Counter
patterns = Counter()
for c in top30:
    p = c.get('catalyst', {}).get('early_pattern', 'none')
    patterns[p] += 1
print("=== Top30 启动模式分布 ===")
for p, cnt in patterns.most_common():
    print(f"  {p}: {cnt}只")

print()
print("=== V2 与 V1 对比 ===")
print("  V1 动量权重: pct_chg≥5=+8, vol_ratio≥3=+8 → 满分20")
print("  V2 动量权重: pct_chg≥5=+3, vol_ratio≥3=+5 → 满分10 (减半)")
print("  V1 D0评分:   只看月涨幅% (横盘=+10, 已涨=-8)")
print("  V2 D0评分:   5模式检测 (弹簧蓄力=+15, 静默吸筹=+12, 温和启动=+8)")
print("  V1 排除:     月涨>80% | 连续涨停≥3天")
print("  V2 排除:     月涨>80% | 连续涨停≥3天 | ST股")
print()

# Save result
with open('current_month_picks_v2.json', 'w') as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
print("✅ 结果已保存到 current_month_picks_v2.json")
