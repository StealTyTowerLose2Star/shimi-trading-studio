#!/usr/bin/env python3
"""
拾米交易工作室 - 待发送消息队列
cron 脚本不直接发微信（有频率限制），写入队列，由 Agent 代发
"""
import os
import json
import time

QUEUE_PATH = os.path.join(os.path.dirname(__file__), "message_queue.json")


def enqueue(title: str, content: str):
    """将消息加入待发送队列"""
    queue = []
    if os.path.exists(QUEUE_PATH):
        try:
            with open(QUEUE_PATH) as f:
                queue = json.load(f)
        except Exception:
            queue = []
    queue.append({
        "title": title,
        "content": content,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    # 只保留最近20条
    queue = queue[-20:]
    with open(QUEUE_PATH, "w") as f:
        json.dump(queue, f, ensure_ascii=False, indent=2)
    print(f"[mq] ✅ 消息已入队: {title}")


def dequeue_all():
    """读取并清空队列"""
    if not os.path.exists(QUEUE_PATH):
        return []
    try:
        with open(QUEUE_PATH) as f:
            queue = json.load(f)
        # 清空
        with open(QUEUE_PATH, "w") as f:
            json.dump([], f)
        return queue
    except Exception:
        return []


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        enqueue(" ".join(sys.argv[1:]), "(来自命令行)")
    else:
        print(f"队列位置: {QUEUE_PATH}")
        items = dequeue_all()
        print(f"当前待发送: {len(items)} 条")
        for item in items:
            print(f"  [{item.get('created_at','')}] {item.get('title','')}")
