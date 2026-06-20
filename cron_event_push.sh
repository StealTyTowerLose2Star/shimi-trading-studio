#!/bin/bash
# ───────────────────────────────────────────────
# 拾米交易工作室 · 事件推送 (每30分钟)
# 从 event_cache.json 取未发送的事件 → enqueue → Server酱送达微信
# ───────────────────────────────────────────────
PROJECT_DIR="/root/shimi-trading-studio"
cd "$PROJECT_DIR"

python3 << 'PYEOF' 2>/dev/null | sed '/^[0-9].*| INFO.*logger/d; /^[0-9].*| WARNING/d'
import sys, os, json, time
sys.path.insert(0, '/root/shimi-trading-studio')

from message_queue import enqueue

CACHE  = os.path.join('/root/shimi-trading-studio', 'data', 'event_cache.json')
SENT   = os.path.join('/root/shimi-trading-studio', 'data', 'event_sent.json')
now    = time.time()

if not os.path.exists(CACHE):
    sys.exit(0)

with open(CACHE) as f:
    cached = json.load(f)

# 已发送记录
sent = {}
if os.path.exists(SENT):
    with open(SENT) as f:
        sent = json.load(f)

pushed = 0
for uid, e in cached.items():
    if uid in sent:
        continue
    code  = e.get("code", "")
    title = e.get("title", "")
    impact = e.get("impact", "low")
    tag = "🔴" if impact == "high" else "📰"
    if len(title) > 80:
        title = title[:78] + "…"

    msg = f"{tag} {code}\n{title}"
    enqueue(f"{tag} {code}", msg, priority="low")
    sent[uid] = now
    pushed += 1

with open(SENT, 'w') as f:
    json.dump(sent, f, ensure_ascii=False)

if pushed:
    print(f"[event_push] 📨 {pushed}条事件已入队 → Server酱")
PYEOF
