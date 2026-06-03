"""
拾米交易工作室 - 复盘报告自动发送
通过 Hermes send CLI 发送到已配置的微信
"""
import os
import json
import time
import subprocess


def send(title: str, content: str) -> bool:
    """通过 Hermes send 发送到微信"""
    target = "weixin:o9cq802sGt2rNSJN4X99q9e1lV5M@im.wechat"
    full_msg = f"*{title}*\n{content}"

    try:
        result = subprocess.run(
            ["hermes", "send", "--to", target],
            input=full_msg,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            print(f"[sender] ✅ 微信发送成功: {title}")
            return True
        else:
            print(f"[sender] ❌ 微信发送失败: {result.stderr or result.stdout}")
            print(f"[sender] 内容预览:\n  == {title} ==\n{content[:200]}...")
            return False
    except Exception as e:
        print(f"[sender] ⚠️ 发送异常: {e}")
        print(f"[sender] 内容预览:\n  == {title} ==\n{content[:200]}...")
        return False


# ─── 报告生成 + 发送 ──────────────────────────────

def send_daily_review():
    """生成并发送每日复盘"""
    from services.review import run_daily_review
    report = run_daily_review()
    if report.get("error"):
        msg = f"⚠️ {report['error']}"
        print(msg)
        send("拾米 · 每日复盘", msg)
        return

    content = report.get("content", {})
    items = content.get("items", [])
    summary = content.get("summary", {})

    total = summary.get("total", 0)
    exceed = summary.get("exceed", 0)
    match_count = summary.get("match", 0)
    miss = summary.get("miss", 0)

    lines = [f"> 报告时间: {time.strftime('%Y-%m-%d %H:%M')}"]
    lines.append(f"> 推荐回顾: {total} 只  |  ✅超预期 {exceed}  |  ➖符合 {match_count}  |  ❌不及预期 {miss}")
    lines.append("")

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

    msg = "\n".join(lines)
    send(f"拾米复盘 {time.strftime('%m-%d')}", msg)
    return report


def send_weekly_review():
    """生成并发送每周复盘"""
    from services.review import run_weekly_review
    report = run_weekly_review()
    if report.get("error"):
        msg = f"⚠️ {report['error']}"
        print(msg)
        send("拾米 · 每周复盘", msg)
        return

    content = report.get("content", {})
    items = content.get("items", [])
    summary = content.get("summary", {})
    total = summary.get("total", 0)
    doubler_count = summary.get("doublers", 0)

    lines = [f"> 报告时间: {time.strftime('%Y-%m-%d %H:%M')}"]
    lines.append(f"> 当月分析: {total} 只  |  🚀 翻倍股 {doubler_count} 只")
    lines.append("")

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

    msg = "\n".join(lines)
    send(f"拾米周报 (第{time.strftime('%W')}周)", msg)
    return report


# ─── CLI 入口 ─────────────────────────────────────

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
