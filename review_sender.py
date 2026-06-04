#!/usr/bin/env python3
"""
拾米交易工作室 - 复盘报告自动发送
通过 Hermes send CLI 发送到已配置的微信
"""
import os
import json
import time
import subprocess


def send(title: str, content: str) -> bool:
    """通过 Hermes send 发送到微信，失败时写入日志（带重试）"""
    target = "weixin:o9cq802sGt2rNSJN4X99q9e1lV5M@im.wechat"
    full_msg = f"*{title}*\n{content}"

    # 写入日志（无论发送成功与否）
    log_path = os.path.join(os.path.dirname(__file__), "review_sender.log")
    with open(log_path, "a") as f:
        f.write(f"\n{'='*60}\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] {title}\n{'='*60}\n")
        f.write(content)
        f.write(f"\n{'='*60}\n\n")

    # 尝试发送（带重试，应对频率限制）
    for attempt in range(3):
        try:
            result = subprocess.run(
                ["hermes", "send", "--to", target],
                input=full_msg, capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                print(f"[sender] ✅ 微信发送成功: {title}")
                return True
            err = result.stderr or result.stdout
            if "rate limited" in err.lower():
                wait = 5 * (attempt + 1)
                print(f"[sender] ⚠️ 频率限制，{wait}s后重试 ({attempt+1}/3)")
                time.sleep(wait)
                continue
            print(f"[sender] ❌ 发送失败: {err}")
            return False
        except Exception as e:
            if attempt < 2:
                time.sleep(3)
                continue
            print(f"[sender] ⚠️ 发送异常: {e}")
            return False

    print(f"[sender] ❌ 重试3次均失败，内容已写入 {log_path}")
    return False


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
    from services.review import run_weekly_review
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
