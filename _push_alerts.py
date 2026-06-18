#!/usr/bin/env python3
"""Push doubler picks to alerts and message queue."""
import sys, os, json
sys.path.insert(0, '/root/shimi-trading-studio')
os.chdir('/root/shimi-trading-studio')

from dotenv import load_dotenv
load_dotenv('/root/shimi-trading-studio/.env')

from services.doubler_scanner import push_doubler_picks_to_alerts, auto_create_doubler_alert

# Load scan result
with open('current_month_picks_v2.json') as f:
    result = json.load(f)

# Create/update alert rules (idempotent)
auto_create_doubler_alert()

# Push picks to message queue
push_doubler_picks_to_alerts(result)

# Verify
try:
    with open('services/message_queue.json') as f:
        mq = json.load(f)
    if isinstance(mq, list):
        doubler_msgs = [m for m in mq if isinstance(m, dict) and 'doubler' in str(m.get('source','')).lower()]
        print(f'message_queue doubler消息: {len(doubler_msgs)} 条')
        if doubler_msgs:
            latest = doubler_msgs[-1]
            print(f'  最新: source={latest.get("source")}, time={latest.get("time","?")}')
    elif isinstance(mq, dict):
        print(f'message_queue keys: {list(mq.keys())[:5]}')
except Exception as e:
    print(f'message_queue 检查: {e}')

print('✅ 预警推送完成')
