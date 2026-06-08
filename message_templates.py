#!/usr/bin/env python3
"""
拾米交易工作室 - 通讯员 · 消息模板
提供统一的消息格式化函数
"""


def format_daily_digest(market_data: dict) -> str:
    """将 market_data 格式化为每日收盘摘要

    消息格式: 📊 今日复盘 | 日期 | 市场阶段 | 核心发现
    """
    from datetime import datetime

    date = datetime.now().strftime("%Y-%m-%d")
    lines = [f"📊 拾米每日收盘摘要 | {date}", "━" * 40, ""]

    # A股部分
    a_stock = market_data.get("a_stock", {})
    if a_stock:
        phase = a_stock.get("phase", "?")
        lines.append(f"🇨🇳 A股市场")
        lines.append(f"  · 阶段: {phase}")
        lines.append("")

    # 美股部分
    us = market_data.get("us", {})
    if us:
        sp500 = us.get("sp500_change")
        vix = us.get("vix")
        if sp500 is not None or vix:
            lines.append(f"🇺🇸 美股")
            if sp500 is not None:
                emoji = "🟢" if sp500 > 0 else "🔴" if sp500 < 0 else "⚪"
                lines.append(f"  · S&P 500: {emoji} {sp500:+.2f}%")
            if vix:
                lines.append(f"  · VIX: {vix}")
            lines.append("")

    # 告警部分
    alerts = market_data.get("alerts", [])
    if alerts:
        lines.append(f"🚨 系统告警 ({len(alerts)}条)")
        for a in alerts[:5]:
            a_type = a.get("type", "?")
            a_msg = a.get("message", "")
            lines.append(f"  · [{a_type}] {a_msg}")
        lines.append("")

    # 存储空间
    storage = market_data.get("storage", {})
    if storage:
        disk = storage.get("disk_usage_pct", 0)
        db_mb = storage.get("db_size_mb", 0)
        if disk > 70 or db_mb > 100:
            lines.append(f"💾 存储告警")
            if disk > 80:
                lines.append(f"  · ⚠️ 磁盘使用率 {disk}%！建议清理")
            elif disk > 70:
                lines.append(f"  · ⚡ 磁盘使用率 {disk}%，注意监控")
            if db_mb > 100:
                lines.append(f"  · 📦 数据库 {db_mb}MB，建议清理历史数据")
            lines.append("")

    if not any([a_stock, us, alerts, storage]):
        lines.append("ℹ️ 今日无特别市场数据或告警")
        lines.append("")

    lines.append("━" * 40)
    lines.append(f"⏰ 生成时间: {datetime.now().strftime('%H:%M')}")
    lines.append("💡 每日18:00自动发送 | 通讯员")

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
