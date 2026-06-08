#!/usr/bin/env python3
"""
魔法师 V3 扫描 + 预警对接验证
"""
from dotenv import load_dotenv; load_dotenv()
import json, time, os

print("🔮 魔法师 V3 扫描 + 预警对接...")
t0 = time.time()
from services.doubler_scanner import recommend_current_month
result = recommend_current_month()
elapsed = time.time() - t0

print(f"✅ 扫描完成 ({elapsed:.1f}s) | 交易日:{result.get('trade_date')}")

top30 = result.get('top30', [])

# Top 10
print(f"\n{'#':>3} {'代码':<8} {'名称':<10} {'得分':>4} {'D0':>4} {'模式':<22} {'信号'}")
print('-' * 90)
for i, c in enumerate(top30[:10]):
    cat = c.get('catalyst', {})
    print(f"{i+1:>3} {c['code']:<8} {c['name']:<10} {c['score']:>4} {cat.get('d0_early_stage',0):>4} "
          f"{cat.get('early_pattern','-'):<22} {cat.get('early_reason','-')}")

# 检查预警
print("\n=== 预警对接验证 ===")
from services.alert import list_alerts, check_alerts

alerts = list_alerts()
doubler_alerts = [a for a in alerts if a.get('type') == 'strategy_signal' and a.get('params', {}).get('strategy') == 'doubler']
print(f"doubler 预警规则: {len(doubler_alerts)}条")
for a in doubler_alerts:
    print(f"  ID={a['id']} | {a['params']} | enabled={a.get('enabled')}")

# 检查消息队列
queue_path = os.path.join(os.path.dirname(os.path.abspath('services/__init__.py')), 'message_queue.json')
queue_path = '/root/shimi-trading-studio/services/message_queue.json'
if os.path.exists(queue_path):
    with open(queue_path) as f:
        queue = json.load(f)
    doubler_msgs = [m for m in queue if m.get('source') == 'doubler']
    print(f"\n消息队列 doubler 消息: {len(doubler_msgs)}条")
    for m in doubler_msgs[-1:]:  # 最新一条
        print(f"  标题: {m.get('title')}")
        print(f"  内容: {m['message'][:200]}...")
    print(f"\n  队列总计: {len(queue)}条消息")

# 手动触发一次检查
print("\n=== 手动触发预警检查 ===")
triggered = check_alerts(force=True)
doubler_triggered = [t for t in triggered if 'doubler' in str(t.get('data', {}).get('strategy', ''))]
print(f"总触发: {len(triggered)}条")
print(f"doubler 触发: {len(doubler_triggered)}条")
for t in doubler_triggered:
    print(f"  {t['message'][:150]}")

print("\n✅ 对接验证完成")
