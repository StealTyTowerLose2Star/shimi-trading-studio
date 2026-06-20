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
        from urllib.parse import urlencode
        # Server酱 desp 用 markdown, 单\n被忽略 → 用双空格+\n 换行
        content = content.replace("\n", "  \n")
        body = urlencode({"title": title[:32], "desp": content[:4096]})
        r = requests.post(API, data=body,
                         headers={'Content-Type': 'application/x-www-form-urlencoded'},
                         timeout=10)
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
        sys.exit(0)

    # 跳过空报告 (0交易/无内容)
    meaningful = []
    for item in items:
        content = item.get("content", "")
        # 跳过无实际信息的报告 / 内部消息
        if "推荐回顾: 0 只" in content and "超预期 0" in content:
            mark_delivered(item["id"])
            continue
        if item.get("title") == "平台启动":
            mark_delivered(item["id"])
            continue
        meaningful.append(item)

    if not meaningful:
        print("[Server酱] 队列为空(已过滤空报告)")
        sys.exit(0)

    # 单条直接发, 多条仍合并(日报+事件同一条)
    title = meaningful[0].get("title", "拾米消息")[:32]
    if len(meaningful) > 1:
        blocks = []
        for item in meaningful:
            blocks.append(item.get("content", ""))
            blocks.append("")
        body = "\n".join(blocks).strip()
    else:
        body = meaningful[0].get("content", "")

    ok = send_serverchan(title, body)
    if ok:
        for item in meaningful:
            mark_delivered(item["id"])
        sent = len(meaningful)
    else:
        for item in meaningful:
            mark_failed(item["id"], "Server酱发送失败")
        failed = len(meaningful)

    for item in items[len(meaningful):]:
        mark_delivered(item["id"])

    print(f"[Server酱] {'✅' if ok else '❌'} {len(meaningful)}条 → 微信")
