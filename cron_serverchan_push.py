#!/usr/bin/env python3
"""
拾米交易工作室 - Server酱 微信推送
替代 iLink 限频问题。从 message_queue 取消息，通过 Server酱推送到微信。
"""
import os, sys, json, requests

sys.path.insert(0, os.path.dirname(__file__))
from dotenv import load_dotenv
load_dotenv()

SENDKEY = os.getenv("SCT_SENDKEY", "")
API = f"https://sctapi.ftqq.com/{SENDKEY}.send"

def send_serverchan(title: str, content: str = "") -> bool:
    """通过 Server酱 发送消息到微信"""
    if not SENDKEY:
        print("[Server酱] 未配置 SCT_SENDKEY")
        return False
    try:
        r = requests.post(API, data={"title": title[:32], "desp": content[:4096]}, timeout=10)
        data = r.json()
        if data.get("code") == 0:
            return True
        print(f"[Server酱] 发送失败: {data.get('message','?')}")
        return False
    except Exception as e:
        print(f"[Server酱] 网络错误: {e}")
        return False


if __name__ == "__main__":
    from message_queue import dequeue_all, mark_delivered, mark_failed

    items = dequeue_all()
    if not items:
        print("[Server酱] 队列为空")
        sys.exit(0)

    # 合并多条消息 (最多3条, 避免内容过长)
    batch = items[:3]
    title = batch[0].get("title", "拾米消息")[:32]
    
    lines = []
    for item in batch:
        lines.append(f"【{item.get('title','?')}】")
        content = item.get("content", "")
        if content:
            lines.append(content[:500])
        lines.append("")
    
    body = "\n".join(lines)
    ok = send_serverchan(title, body)

    for item in batch:
        if ok:
            mark_delivered(item["id"])
        else:
            mark_failed(item["id"], "Server酱发送失败")

    print(f"[Server酱] {'✅' if ok else '❌'} 已发送 {len(batch)}/{len(items)} 条 | 剩余 {len(items)-len(batch)} 条")
