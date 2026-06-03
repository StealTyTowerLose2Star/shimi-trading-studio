"""
拾米交易工作室 - 复盘报告自动发送
支持多通道: 企业微信/Server酱/PushPlus/自定义Webhook
通过环境变量配置发送目标
"""
import os
import json
import time
import urllib.request
import urllib.error

import config

# ─── 通道配置 ─────────────────────────────────────

def _get_webhook_url():
    """获取配置的推送地址"""
    # 优先级: env → config
    return os.getenv("SHIMI_WEBHOOK_URL") or getattr(config, "WEBHOOK_URL", "")


def _get_channel():
    """检测通道类型"""
    url = _get_webhook_url()
    if not url:
        return None
    if "qyapi.weixin.qq.com" in url:
        return "wecom"      # 企业微信
    if "sctapi.ftqq.com" in url:
        return "serverchan"  # Server酱
    if "pushplus" in url:
        return "pushplus"    # PushPlus
    return "webhook"         # 通用


# ─── 发送器 ───────────────────────────────────────

def send_wecom(webhook_url: str, title: str, content: str):
    """发送到企业微信群机器人"""
    payload = {
        "msgtype": "markdown",
        "markdown": {
            "content": f"## {title}\n{content}"
        }
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(webhook_url, data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        result = json.loads(resp.read())
        return result.get("errcode") == 0
    except Exception as e:
        print(f"[sender] 企业微信发送失败: {e}")
        return False


def send_serverchan(sendkey: str, title: str, content: str):
    """发送到 Server酱"""
    url = f"https://sctapi.ftqq.com/{sendkey}.send"
    data = urllib.parse.urlencode({"title": title, "desp": content}).encode()
    req = urllib.request.Request(url, data=data)
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        return True
    except Exception as e:
        print(f"[sender] Server酱发送失败: {e}")
        return False


def send_pushplus(token: str, title: str, content: str):
    """发送到 PushPlus"""
    url = "https://www.pushplus.plus/send"
    payload = {"token": token, "title": title, "content": content, "template": "markdown"}
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        return True
    except Exception as e:
        print(f"[sender] PushPlus发送失败: {e}")
        return False


def send(title: str, content: str) -> bool:
    """通用发送接口 — 自动识别通道并发送"""
    url = _get_webhook_url()
    if not url:
        print("[sender] ⚠️ SHIMI_WEBHOOK_URL 未配置，跳过发送")
        print(f"[sender] 内容预览:\n  == {title} ==\n{content[:200]}...")
        return False

    channel = _get_channel()
    if channel == "wecom":
        return send_wecom(url, title, content)
    elif channel == "serverchan":
        # url 格式: https://sctapi.ftqq.com/SCTxxx.send
        import urllib.parse
        return send_serverchan(url, title, content)
    elif channel == "pushplus":
        return send_pushplus(url, title, content)
    else:
        # 通用 webhook: POST JSON
        payload = {"title": title, "content": content}
        data = json.dumps(payload).encode()
        req = urllib.request.Request(url, data=data,
                                     headers={"Content-Type": "application/json"})
        try:
            resp = urllib.request.urlopen(req, timeout=10)
            return True
        except Exception as e:
            print(f"[sender] Webhook发送失败: {e}")
            return False


# ─── 报告生成 + 发送 ──────────────────────────────

def send_daily_review():
    """生成并发送每日复盘"""
    from services.review import run_daily_review
    report = run_daily_review()
    if report.get("error"):
        msg = f"⚠️ 每日复盘生成失败: {report['error']}"
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

    # 构建推送内容
    lines = [f"> **报告时间**: {time.strftime('%Y-%m-%d %H:%M')}"]
    lines.append(f"> **推荐回顾**: {total} 只  |  ✅超预期 {exceed}  |  ➖符合 {match_count}  |  ❌不及预期 {miss}")
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

        lines.append(f"{emoji} **{code}** {name}  {pnl_str}{phase_str}")
        if reason_str:
            lines.append(reason_str)

    msg = "\n".join(lines)

    success = send(f"拾米 · 每日复盘 {time.strftime('%m-%d')}", msg)
    if success:
        print(f"[sender] ✅ 每日复盘已发送")
    return report


def send_weekly_review():
    """生成并发送每周复盘"""
    from services.review import run_weekly_review
    report = run_weekly_review()
    if report.get("error"):
        msg = f"⚠️ 每周复盘生成失败: {report['error']}"
        print(msg)
        send("拾米 · 每周复盘", msg)
        return

    content = report.get("content", {})
    items = content.get("items", [])
    summary = content.get("summary", {})

    total = summary.get("total", 0)
    doubler_count = summary.get("doublers", 0)

    lines = [f"> **报告时间**: {time.strftime('%Y-%m-%d %H:%M')}"]
    lines.append(f"> **当月分析**: {total} 只  |  🚀 翻倍股 {doubler_count} 只")
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

        lines.append(f"{'🚀' if is_doubler else '📌'} **{code}** {name}{tag}")
        lines.append(f"   · 涨跌: {pnl_str}  |  阶段: {tech}")
        if advice:
            lines.append(f"   · 建议: {advice}")

    msg = "\n".join(lines)

    success = send(f"拾米 · 每周复盘 (第{time.strftime('%W')}周)", msg)
    if success:
        print(f"[sender] ✅ 每周复盘已发送")
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
        send("拾米 · 测试消息", "> 这是一条测试推送\n\n如果收到说明配置正确 ✅")
    else:
        print("用法: python3 review_sender.py [daily|weekly|test]")
        print("  需设置环境变量 SHIMI_WEBHOOK_URL")
        print("  企业微信示例: SHIMI_WEBHOOK_URL=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx")
        print("  Server酱示例: SHIMI_WEBHOOK_URL=https://sctapi.ftqq.com/SCTxxx.send")
