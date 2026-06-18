#!/usr/bin/env python3
"""Magicien daily scan — cron standalone script."""
import sys, os, json, time
from collections import Counter

sys.path.insert(0, '/root/shimi-trading-studio')
os.chdir('/root/shimi-trading-studio')

# Pitfall: load_dotenv() without explicit path fails in cron context
from dotenv import load_dotenv
load_dotenv('/root/shimi-trading-studio/.env')

print("🔮 魔法师每日扫描开始...")
t0 = time.time()

from services.doubler_scanner import recommend_current_month
result = recommend_current_month()

elapsed = time.time() - t0

if 'error' in result:
    print(f"❌ 扫描失败: {result['error']}", file=sys.stderr)
    sys.exit(1)

top30 = result.get('top30', [])
elite = result.get('elite_picks', [])
scan_time = result.get('scan_time', '?')

# Save results
with open('current_month_picks_v2.json', 'w') as f:
    json.dump(result, f, ensure_ascii=False, indent=2)

print(f"\n✅ 扫描完成 ({elapsed:.0f}s)")
print(f"扫描时间: {scan_time}")
print(f"推荐池: {len(top30)} 只 | 精英选: {len(elite)} 只")

# Top5 summary
print(f"\n📊 Top 5:")
for i, c in enumerate(top30[:5]):
    cat = c.get('catalyst', {})
    pattern = cat.get('early_pattern', '?')
    code = c.get('code', '?')
    name = c.get('name', '?')
    score = c.get('score', 0)
    sb = c.get('score_breakdown', {})
    d0 = sb.get('early_stage_d0', 0)
    d7 = sb.get('catalyst_d7', 0)
    print(f"  #{i+1} {code} {name} {score}分 (D0={d0}, D7={d7}) [{pattern}]")

# Mode distribution
patterns = Counter()
for c in top30:
    p = c.get('catalyst', {}).get('early_pattern', 'none')
    patterns[p] += 1

print(f"\n📈 模式分布 (Top30):")
for p, count in patterns.most_common(12):
    print(f"  {p}: {count}只")

# Check excludes
excludes = [c for c in top30 if c.get('catalyst', {}).get('exclude')]
if excludes:
    print(f"\n⚠️ 排除标记: {len(excludes)} 只")
    for c in excludes:
        print(f"  {c['code']} {c['name']} - {c['catalyst'].get('early_pattern','?')}")

# JSON summary for downstream
summary = {
    'scan_time': scan_time,
    'top30_count': len(top30),
    'elite_count': len(elite),
    'top5': [{'code': c['code'], 'name': c['name'], 'score': c['score'],
              'pattern': c.get('catalyst',{}).get('early_pattern','')} for c in top30[:5]],
    'patterns': dict(patterns.most_common(12)),
    'elapsed': round(elapsed)
}
print("\n__SUMMARY_JSON__")
print(json.dumps(summary, ensure_ascii=False))
