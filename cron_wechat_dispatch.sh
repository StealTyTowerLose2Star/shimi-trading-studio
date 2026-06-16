#!/bin/bash
# ───────────────────────────────────────────────
# 拾米交易工作室 · 通讯员 · 消息队列投递
# cron: */5 * * * * → deliver: weixin
# 功能: 取出消息队列中所有pending消息, 通过stdout投递到微信
# ───────────────────────────────────────────────
set -euo pipefail

PROJECT_DIR="/root/shimi-trading-studio"
cd "$PROJECT_DIR"

python3 -c "
from message_queue import dequeue_all, mark_delivered, mark_failed
items = dequeue_all()
if not items:
    exit(0)
for item in items:
    try:
        mid = item.get('id')
        title = item.get('title','?')
        content = item.get('content','')
        print(f'{title}')
        if content:
            print(content)
        print('---')
        mark_delivered(mid)
    except Exception as e:
        mark_failed(mid, str(e)[:100])
" 2>&1
