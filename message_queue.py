#!/usr/bin/env python3
"""
拾米交易工作室 - 通讯员 · 消息队列 (增强版)
职责: 消息入队/出队 + 投递状态追踪 + 重试机制

状态机: pending → sent → delivered | failed
"""
import os
import json
import time
from datetime import datetime
from typing import List, Dict, Optional

QUEUE_PATH = os.path.join(os.path.dirname(__file__), "message_queue.json")
MAX_RETRY = 3
MAX_QUEUE = 50


def enqueue(title: str, content: str, priority: str = "normal") -> Dict:
    """将消息加入待发送队列

    Returns:
        {"id": int, "title": str, "status": "pending"}
    """
    queue = _load()
    msg_id = max([m.get("id", 0) for m in queue], default=0) + 1

    msg = {
        "id": msg_id,
        "title": title,
        "content": content,
        "priority": priority,
        "status": "pending",
        "retry_count": 0,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "sent_at": None,
        "delivered_at": None,
        "failed_at": None,
        "error": None,
    }
    queue.append(msg)
    queue = queue[-MAX_QUEUE:]
    _save(queue)
    return msg


def dequeue_all() -> List[Dict]:
    """读取并清空所有 pending 消息 (标记为 sent)"""
    queue = _load()
    pending = [m for m in queue if m.get("status") == "pending"]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for m in queue:
        if m.get("status") == "pending":
            m["status"] = "sent"
            m["sent_at"] = now
    _save(queue)
    return pending


def mark_delivered(msg_id: int) -> bool:
    """标记消息已送达"""
    queue = _load()
    for m in queue:
        if m.get("id") == msg_id:
            m["status"] = "delivered"
            m["delivered_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            _save(queue)
            return True
    return False


def mark_failed(msg_id: int, error: str = "") -> bool:
    """标记消息投递失败 (自动重试)"""
    queue = _load()
    for m in queue:
        if m.get("id") == msg_id:
            m["retry_count"] = m.get("retry_count", 0) + 1
            m["error"] = error[:200]
            if m["retry_count"] >= MAX_RETRY:
                m["status"] = "failed"
                m["failed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            else:
                m["status"] = "pending"  # 重新入队
            _save(queue)
            return True
    return False


def get_status() -> Dict:
    """获取队列状态摘要"""
    queue = _load()
    counts = {"pending": 0, "sent": 0, "delivered": 0, "failed": 0}
    for m in queue:
        counts[m.get("status", "pending")] = counts.get(m.get("status", "pending"), 0) + 1
    return {
        "total": len(queue),
        "by_status": counts,
        "failed_items": [m for m in queue if m.get("status") == "failed"][:5],
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def _load() -> List[Dict]:
    if os.path.exists(QUEUE_PATH):
        try:
            with open(QUEUE_PATH) as f:
                return json.load(f)
        except Exception:
            pass
    return []


def _save(queue: List[Dict]):
    os.makedirs(os.path.dirname(QUEUE_PATH), exist_ok=True)
    with open(QUEUE_PATH, "w") as f:
        json.dump(queue, f, ensure_ascii=False, indent=2)


# ─── CLI ──────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        s = get_status()
        print(f"📡 消息队列: {s['total']}条")
        for status, count in s["by_status"].items():
            if count:
                print(f"  {status}: {count}")
        if s["failed_items"]:
            print(f"\n❌ 失败项:")
            for m in s["failed_items"]:
                print(f"  #{m['id']} {m['title']}: {m.get('error','?')[:60]}")
    elif sys.argv[1] == "enqueue":
        enqueue(" ".join(sys.argv[2:]) if len(sys.argv) > 2 else "测试消息", "")
        print("✅ 已入队")
    elif sys.argv[1] == "dequeue":
        items = dequeue_all()
        print(f"📤 取出 {len(items)} 条消息")
        for item in items:
            print(f"  [{item.get('id')}] {item.get('title','')}")
    elif sys.argv[1] == "mark":
        if len(sys.argv) > 2:
            mid = int(sys.argv[2])
            action = sys.argv[3] if len(sys.argv) > 3 else "delivered"
            if action == "delivered":
                mark_delivered(mid)
                print(f"✅ #{mid} 已送达")
            elif action == "failed":
                mark_failed(mid)
                print(f"⚠️ #{mid} 标记失败")
