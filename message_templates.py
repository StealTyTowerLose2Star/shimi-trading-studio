#!/usr/bin/env python3
"""拾米交易工作室 - 通讯员 · 消息模板"""
from datetime import date


def _short_idx(name: str) -> str:
    return name.replace("上证指数","上证").replace("深证成指","深证")\
               .replace("创业板指","创业板").replace("科创50","科创")


def format_daily_digest(m: dict) -> str:
    today = date.today().strftime("%m-%d")
    a = m.get("a_stock", {})
    trades = m.get("trades", [])
    events = m.get("events", [])
    out = [f"📊 拾米日报 {today}", ""]

    # ═══ 市场 ═══ header standalone, content on next lines
    out.append("── 市场 ──")

    indices = a.get("indices", [])
    idx_strs = []
    for i in indices:
        chg = i.get("change", 0)
        arrow = "↑" if chg > 0 else "↓" if chg < 0 else "→"
        idx_strs.append(f"{_short_idx(i.get('name','?'))} {chg:+.2f}%{arrow}")

    total = a.get("total", 0)
    mood_s = ""
    if total > 0:
        up, down = a.get("up", 0), a.get("down", 0)
        ratio = up / total * 100 if total else 0
        mood = "🔥" if ratio > 60 else "😐" if ratio > 35 else "❄️"
        mood_s = f" {mood} {up}↑/{down}↓ ({ratio:.0f}%)"

    line1 = "  ".join(idx_strs) + mood_s
    out.append(line1)

    if total > 0:
        line2 = f"涨停{a.get('limit_up',0)} 跌停{a.get('limit_down',0)} 量{a.get('volume_ratio',1):.2f} 仓{a.get('position_ratio',50):.0f}%"
        phase = a.get("phase","")
        if phase:
            line2 += f" · {phase}"
        out.append(line2)

    sectors = m.get("sectors", [])
    if sectors:
        out.append("🔥 " + "  ".join(
            f"{s.get('name','?')}{s.get('change',0):+.1f}%"
            for s in sectors[:3]))

    # ═══ 交易 ═══ header+stats inline, trades on next line
    if trades:
        out.append("")
        active = [t for t in trades if t.get("exit_price") is None]
        closed = [t for t in trades if t.get("exit_price") is not None]

        total_pnl = sum(
            (t["exit_price"] - t["entry_price"]) * t["qty"]
            for t in closed if t.get("exit_price") and t.get("entry_price")
        )

        stats = []
        if active:
            stats.append(f"持仓 {len(active)}只")
        if total_pnl:
            pnl_s = f"+{total_pnl:,.0f}" if total_pnl >= 0 else f"{total_pnl:,.0f}"
            stats.append(f"累计盈亏 ¥{pnl_s}")

        out.append(f"── 交易 ── {' | '.join(stats)}")
        if active:
            out.append("")
            for t in active:
                out.append(f"{t['code']} {t['name']} | 成本¥{t['entry_price']:.2f} | {t['qty']}股")

    # ═══ 翻倍股 ═══ header standalone, content on next lines
    doubler = m.get("doubler", {})
    top5 = doubler.get("top5", [])
    if top5:
        out.append("")
        out.append("── 翻倍股 ──")
        for p in top5[:5]:
            stars = "⭐" * min(5, max(1, int(p['score'] / 20)))
            pat = p.get("pattern", "")
            line = f"{p['code']} {p['name']} {p['score']:.0f} {stars}"
            if pat:
                line += f" · {pat}"
            out.append(line)

    # ═══ 事件 ═══ header standalone, content on next lines
    if events:
        out.append("")
        out.append("── 事件 ──")
        for e in events[:5]:
            code = e.get("code", "")
            title = e.get("title", "")
            imp = e.get("impact", "")
            tag = "🔴" if imp == "high" else "📰"
            if len(title) > 42:
                title = title[:40] + "…"
            out.append(f"{tag} {code} {title}")

    return "\n".join(out)


def format_alert(alert_type: str, message: str, severity: str = "warning") -> str:
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
