#!/usr/bin/env python3
"""
拾米交易工作室 - 复盘报告自动发送
通过 Hermes send CLI 发送到已配置的微信
"""
import os
import json
import time
import subprocess


from message_queue import enqueue


def send(title: str, content: str) -> bool:
    """将消息加入待发送队列（由 Agent 代发到微信）"""
    # 写入日志
    log_path = os.path.join(os.path.dirname(__file__), "review_sender.log")
    with open(log_path, "a") as f:
        f.write(f"\n{'='*60}\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] {title}\n{'='*60}\n")
        f.write(content)
        f.write(f"\n{'='*60}\n\n")
    # 入队
    enqueue(title, content)
    print(f"[sender] ✅ 消息已入队，待 Agent 发送: {title}")
    return True


def send_daily_review():
    from services.review import run_daily_review
    report = run_daily_review()
    if report.get("error"):
        send("拾米 · 每日复盘", f"⚠️ {report['error']}")
        return
    content = report.get("content", {})
    items = content.get("items", [])
    summary = content.get("summary", {})
    total = summary.get("total", 0)
    exceed = summary.get("exceed", 0)
    match_count = summary.get("match", 0)
    miss = summary.get("miss", 0)
    lines = [f"> 报告时间: {time.strftime('%Y-%m-%d %H:%M')}",
             f"> 推荐回顾: {total} 只  |  ✅超预期 {exceed}  |  ➖符合 {match_count}  |  ❌不及预期 {miss}", ""]
    for item in items[:10]:
        code = item.get("code", "?")
        name = item.get("name", "")
        rec_p = item.get("recommend_price", 0)
        cur_p = item.get("current_price", 0)
        pnl = ((cur_p - rec_p) / rec_p * 100) if rec_p else 0
        pnl_str = f"+{pnl:.1f}%" if pnl >= 0 else f"{pnl:.1f}%"
        emoji = "✅" if pnl > 5 else ("❌" if pnl < -5 else "➖")
        reason = item.get("failure_reason", "")
        reason_str = f"  · 原因: {reason}" if reason else ""
        phase = item.get("tech_phase", "")
        phase_str = f" | {phase}" if phase else ""
        lines.append(f"{emoji} {code} {name}  {pnl_str}{phase_str}")
        if reason_str:
            lines.append(reason_str)
    send(f"拾米复盘 {time.strftime('%m-%d')}", "\n".join(lines))
    return report


def send_weekly_review():
    from services.review_weekly import run_weekly_review
    report = run_weekly_review()
    if report.get("error"):
        send("拾米 · 每周复盘", f"⚠️ {report['error']}")
        return
    content = report.get("content", {})
    items = content.get("items", [])
    summary = content.get("summary", {})
    total = summary.get("total", 0)
    doubler_count = summary.get("doublers", 0)
    lines = [f"> 报告时间: {time.strftime('%Y-%m-%d %H:%M')}",
             f"> 当月分析: {total} 只  |  🚀 翻倍股 {doubler_count} 只", ""]
    for item in items[:10]:
        code = item.get("code", "?")
        name = item.get("name", "")
        rec_p = item.get("recommend_price", 0)
        cur_p = item.get("current_price", 0)
        pnl = ((cur_p - rec_p) / rec_p * 100) if rec_p else 0
        pnl_str = f"+{pnl:.1f}%" if pnl >= 0 else f"{pnl:.1f}%"
        is_doubler = cur_p >= rec_p * 2
        tag = " 🚀翻倍" if is_doubler else ""
        tech = item.get("tech_phase", "")
        advice = item.get("advice", "")
        lines.append(f"{'🚀' if is_doubler else '📌'} {code} {name}{tag}")
        lines.append(f"   · 涨跌: {pnl_str}  |  阶段: {tech}")
        if advice:
            lines.append(f"   · 建议: {advice}")
    send(f"拾米周报 (第{time.strftime('%W')}周)", "\n".join(lines))
    return report


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "daily":
        send_daily_review()
    elif cmd == "weekly":
        send_weekly_review()
    elif cmd == "test":
        send("拾米 · 测试消息", "这是一条来自拾米交易工作室的测试消息\n\n如果收到说明微信推送配置正确 ✅")
    else:
        print("用法: python3 review_sender.py [daily|weekly|test]")
