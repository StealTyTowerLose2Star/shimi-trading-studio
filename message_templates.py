"""
拾米交易工作室 - 通讯员 · 消息模板系统
职责: 统一消息格式，各角色共享

使用:
    from message_templates import format_review, format_alert
    msg = format_review(review_data)
"""

from datetime import datetime


def format_header(title: str) -> str:
    return f"📊 {title} | {datetime.now().strftime('%Y-%m-%d %H:%M')}"


def format_review(review_data: dict) -> str:
    """格式化复盘报告"""
    lines = [format_header("复盘报告")]
    summary = review_data.get("summary", "")
    if summary:
        lines.append(f"\n📋 摘要: {summary}")

    details = review_data.get("details", {})
    if details.get("indices"):
        lines.append("\n📈 指数:")
        for idx in details["indices"][:4]:
            lines.append(f"  {idx.get('name','?')}: {idx.get('change_pct',0):+.2f}%")

    if details.get("hot_stocks"):
        lines.append("\n🔥 热门股:")
        for s in details["hot_stocks"][:5]:
            lines.append(f"  {s.get('symbol','?')}: {s.get('change_pct',0):+.2f}%")

    pnl = details.get("pnl", {}).get("summary", {})
    if pnl:
        lines.append(f"\n💰 盈亏: ${pnl.get('total_pnl',0):+.0f} | 胜率: {pnl.get('win_rate',0)}%")

    return "\n".join(lines)


def format_alert(alert_data: dict) -> str:
    """格式化告警消息"""
    lines = [format_header("🚨 系统告警")]
    lines.append(f"\n类型: {alert_data.get('type', '?')}")
    lines.append(f"消息: {alert_data.get('message', '?')}")
    if alert_data.get("data"):
        lines.append(f"数据: {alert_data['data']}")
    lines.append(f"\n⏰ {alert_data.get('time', '?')}")
    return "\n".join(lines)


def format_doubler_update(picks: list) -> str:
    """格式化翻倍股推荐更新"""
    lines = [format_header("🚀 翻倍股推荐更新")]
    lines.append(f"\n共 {len(picks)} 只推荐:\n")

    for i, p in enumerate(picks[:5], 1):
        lines.append(
            f"  #{i} {p.get('code','?')} {p.get('name','?')} "
            f"评分{p.get('score',0)}分 | "
            f"催化剂: {p.get('catalyst',{}).get('cat_type','?')}"
        )

    return "\n".join(lines)


def format_daily_digest(market_data: dict) -> str:
    """格式化每日收盘摘要"""
    lines = [format_header("📋 今日收盘摘要")]

    # A股
    if market_data.get("a_stock"):
        a = market_data["a_stock"]
        lines.append("\n🇨🇳 A股:")
        lines.append(f"  上证: {a.get('shanghai',{}).get('change_pct',0):+.2f}%")
        lines.append(f"  市场阶段: {a.get('phase','?')}")
        lines.append(f"  建议仓位: {a.get('position','?')}")

    # 美股
    if market_data.get("us"):
        u = market_data["us"]
        lines.append("\n🌊 美股:")
        lines.append(f"  S&P 500: {u.get('sp500_change',0):+.2f}%")
        lines.append(f"  VIX: {u.get('vix','?')}")

    # 告警
    if market_data.get("alerts"):
        lines.append(f"\n🚨 告警: {len(market_data['alerts'])}条")

    lines.append(f"\n⏰ 生成时间: {datetime.now().strftime('%H:%M')}")
    return "\n".join(lines)
