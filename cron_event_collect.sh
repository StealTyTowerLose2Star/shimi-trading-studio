#!/bin/bash
# ───────────────────────────────────────────────
# 拾米交易工作室 · 市场事件搜集 (每10分钟)
# 仅搜集 → 写入本地缓存 → 半小时推送统一消费
# ───────────────────────────────────────────────
PROJECT_DIR="/root/shimi-trading-studio"
cd "$PROJECT_DIR"

python3 << 'PYEOF' 2>/dev/null | sed '/^[0-9].*| INFO.*logger/d; /^[0-9].*| WARNING/d'
import sys, os, json, time
sys.path.insert(0, '/root/shimi-trading-studio')

CACHE = os.path.join('/root/shimi-trading-studio', 'data', 'event_cache.json')
SRC   = os.path.join('/root/shimi-trading-studio', 'data', 'market_events.json')

now = time.time()

# 加载已有缓存
cached = {}
if os.path.exists(CACHE):
    try:
        with open(CACHE) as f:
            cached = json.load(f)
    except:
        cached = {}

# 加载源数据
if not os.path.exists(SRC):
    sys.exit(0)
with open(SRC) as f:
    raw = json.load(f)
events = raw if isinstance(raw, list) else raw.get("events", [])

# 关键词过滤
kw = ["中标","重组","重大","ST","退市","停牌","复牌","减持","增持","回购","业绩预告","立案"]
new_count = 0
for e in events:
    title = e.get("title","")
    impact = e.get("impact","low")
    if impact != "high" and not any(k in title for k in kw):
        continue
    uid = f"{e.get('code','')}|{title[:40]}"
    if uid not in cached:
        cached[uid] = {
            "code": e.get("code",""),
            "title": title,
            "impact": impact,
            "date": e.get("date",""),
            "collected_at": now
        }
        new_count += 1

# 清理30天前的缓存
cutoff = now - 30 * 86400
cached = {k:v for k,v in cached.items() if v.get("collected_at",0) > cutoff}

with open(CACHE, 'w') as f:
    json.dump(cached, f, ensure_ascii=False)

if new_count:
    print(f"[event_collect] +{new_count}条新事件 (缓存总数{len(cached)})")
PYEOF
