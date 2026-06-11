"""
拾米交易工作室 - 服务层统一入口
导出所有 A 股相关业务服务函数

服务清单:
  strategy        — 三大策略扫描
  advice          — 操作建议
  pnl             — 盈亏分析
  portfolio       — 持仓分析
  review          — 每日复盘
  review_weekly   — 每周复盘
  alert           — 条件预警
  kline_patterns  — K线形态识别
"""

# ─── 策略扫描 ──────────────────────────────────
from services.strategy import (
    run_trend_scan,    # 趋势策略扫描 → dict{picked, total_scanned}
    run_hybrid_scan,   # 混合策略扫描 → dict{picked, total_scanned}
    run_dragon_scan,   # 龙头战法扫描 → dict{picked, total_scanned}
)

# ─── 操作建议 ──────────────────────────────────
from services.advice import (
    generate_advice,        # 综合三大策略 → dict{advice, positions, market}
    calc_atr_based_levels,  # ATR 动态止盈止损 → (t1, t2, t3, sl, trailing_start, trailing_step)
)

# ─── 盈亏分析 ──────────────────────────────────
from services.pnl import (
    compute_pnl_report,  # 逐日 PnL 报告 → dict{report, summary}
)

# ─── 持仓分析 ──────────────────────────────────
from services.portfolio import (
    analyze_portfolio,  # 持仓评分 + 建议 → dict{positions, summary}
)

# ─── 复盘系统 ──────────────────────────────────
from services.review import (
    run_daily_review,  # 每日复盘 → dict{market, recommendations, failures, ...}
)
from services.review_weekly import (
    run_weekly_review,  # 每周复盘 → dict{market, stocks, sector, ...}
)

# ─── 条件预警 ──────────────────────────────────
from services.alert import (
    create_alert,     # (type, params, enabled) → dict
    list_alerts,      # () → list[dict]
    get_alert,        # (alert_id) → dict | None
    update_alert,     # (alert_id, updates) → dict | None
    delete_alert,     # (alert_id) → bool
    check_alerts,     # (force=False) → list[triggered]
)

__all__ = [
    "run_trend_scan", "run_hybrid_scan", "run_dragon_scan",
    "generate_advice", "calc_atr_based_levels",
    "compute_pnl_report",
    "analyze_portfolio",
    "run_daily_review", "run_weekly_review",
    "create_alert", "list_alerts", "get_alert",
    "update_alert", "delete_alert", "check_alerts",
]
