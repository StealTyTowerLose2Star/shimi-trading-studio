#!/usr/bin/env python3
"""
拾米交易工作室 - 通讯员 · 消息模板
提供统一的消息格式化函数
"""


def format_daily_digest(market_data: dict) -> str:
    """生成微信友好的收盘摘要 —— 精简、可扫读"""
    from datetime import datetime, date

    today = date.today().strftime("%m-%d")
    lines = [f"📊 拾米收盘 {today}"]

    # A股
    a = market_data.get("a_stock", {})
    phase = a.get("phase", "—")
    lines.append(f"A股 {phase}")

    # 美股
    us = market_data.get("us", {})
    sp500 = us.get("sp500_change")
    vix = us.get("vix")
    if sp500 is not None:
        emoji = "🟢" if sp500 > 0 else "🔴" if sp500 < 0 else "⚪"
        lines.append(f"标普 {emoji}{sp500:+.2f}% | VIX {vix or '—'}")

    # 翻倍股信号
    alerts = market_data.get("alerts", [])
    for a in alerts:
        if a.get("type") == "strategy_signal":
            lines.append(f"🚀 {a.get('message','')[:60]}")
            break

    # 存储
    storage = market_data.get("storage", {})
    disk = storage.get("disk_usage_pct", 0)
    if disk > 70:
        lines.append(f"💾 磁盘 {disk}%")

    return "\n".join(lines)


def format_alert(alert_type: str, message: str, severity: str = "warning") -> str:
    """格式化系统告警消息

    消息格式: 🚨 系统告警 | 级别 | 组件 | 建议
    """
    from datetime import datetime

    return (
        f"🚨 拾米系统告警\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"> 级别: {severity.upper()}\n"
        f"> 组件: {alert_type}\n"
        f"> 时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"\n{message}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"💡 请检查系统状态"
    )


def format_doubler_signal(code: str, name: str, score: float, pattern: str, price: float) -> str:
    """格式化翻倍股信号消息

    消息格式: 🚀 翻倍股更新 | 新增X只 | 评分变化
    """
    return (
        f"🚀 翻倍股信号\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"> 代码: {code} {name}\n"
        f"> 评分: {score:.0f}/100\n"
        f"> 形态: {pattern}\n"
        f"> 当前价: ¥{price:.2f}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"💡 进入推荐池，请评估"
    )
