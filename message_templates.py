#!/usr/bin/env python3
"""
拾米交易工作室 - 通讯员 · 消息模板
提供统一的消息格式化函数
"""


def format_daily_digest(market_data: dict) -> str:
    """生成微信友好的收盘摘要 —— 精简、可扫读"""
    from datetime import date

    today = date.today().strftime("%m-%d")
    lines = [f"📊 拾米收盘 {today}"]
    a = market_data.get("a_stock", {})

    # ─── 指数行情 ───
    indices = a.get("indices", [])
    if indices:
        idx_parts = []
        for i in indices:
            name = i.get("name", "?")
            chg = i.get("change", 0)
            arrow = "↑" if chg > 0 else "↓" if chg < 0 else "→"
            idx_parts.append(f"{name} {chg:+.2f}%{arrow}")
        lines.append("  ".join(idx_parts))
    else:
        lines.append(f"A股 {a.get('phase', '—')}")

    # ─── 市场情绪 ───
    total = a.get("total", 0)
    up = a.get("up", 0)
    down = a.get("down", 0)
    lu = a.get("limit_up", 0)
    ld = a.get("limit_down", 0)
    vol = a.get("volume_ratio", 1.0)
    pos = a.get("position_ratio", 50)

    if total > 0:
        ratio = up / total * 100 if total else 0
        mood = "🔥" if ratio > 60 else "😐" if ratio > 35 else "❄️"
        lines.append(
            f"{mood} 涨跌: {up}↑ / {down}↓ ({ratio:.0f}%)  "
            f"涨停{lu} 跌停{ld}  "
            f"量比{vol:.2f}  "
            f"仓位{pos:.0f}%"
        )

    # ─── 翻倍股 ───
    doubler = market_data.get("doubler", {})
    top5 = doubler.get("top5", [])
    if top5:
        dp = []
        for p in top5[:5]:
            dp.append(f"{p['code']} {p['name']} {p['score']:.0f}分")
        lines.append(f"🚀 翻倍股: {' | '.join(dp)}")

    # ─── 美股 ───
    us = market_data.get("us", {})
    sp500 = us.get("sp500_change")
    vix = us.get("vix")
    if sp500 is not None:
        emoji = "🟢" if sp500 > 0 else "🔴" if sp500 < 0 else "⚪"
        lines.append(f"🇺🇸 标普 {emoji}{sp500:+.2f}%  VIX {vix or '—'}")

    # ─── 告警 ───
    alerts = market_data.get("alerts", [])
    for a in alerts:
        if a.get("type") == "strategy_signal":
            lines.append(f"🔔 {a.get('message','')[:80]}")
            break

    # ─── 磁盘 ───
    storage = market_data.get("storage", {})
    disk = storage.get("disk_usage_pct", 0)
    if disk > 70:
        lines.append(f"💾 磁盘 {disk}%")

    return "\n".join(lines)


def format_alert(alert_type: str, message: str, severity: str = "warning") -> str:
    """格式化系统告警消息"""
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
    """格式化翻倍股信号消息"""
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
