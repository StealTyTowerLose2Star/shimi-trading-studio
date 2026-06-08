"""
HiTao 美股 - 服务层统一入口
导出所有美股相关业务服务函数

服务清单:
  scanner        — 技术评分引擎 + 批量扫描 (五维评分 0-100)
  portfolio      — 持仓评估 (ATR 浮动止盈 + 动态止损, 多空双方向)
  pnl            — 盈亏分析报告
  review         — 每日/每周复盘
"""

# ─── 扫描评分 ──────────────────────────────────
from haitao.services.scanner import (
    score_stock,          # 单只美股五维评分 → dict{score, phase, signals}
    scan_watchlist,       # 批量扫描观察列表 → List[dict] (降序)
    scan_top_gainers,     # 扫描当日涨幅榜 → List[dict]
    scan_adr_picks,       # 扫描中概股精选 → List[dict]
)

# ─── 持仓评估 ──────────────────────────────────
from haitao.services.portfolio import (
    evaluate_us_position,   # 单只持仓 ATR 动态评估 → dict
    batch_evaluate_us,      # 批量持仓评估 → List[dict]
)

# ─── 盈亏分析 ──────────────────────────────────
from haitao.services.pnl import (
    calculate_pnl,  # 美股盈亏追踪 → dict{report, summary}
)

# ─── 复盘系统 ──────────────────────────────────
from haitao.services.review import (
    run_daily_review,   # 每日美股复盘 → dict
    run_weekly_review,  # 每周美股复盘 → dict
)

__all__ = [
    "score_stock", "scan_watchlist", "scan_top_gainers", "scan_adr_picks",
    "evaluate_us_position", "batch_evaluate_us",
    "calculate_pnl",
    "run_daily_review", "run_weekly_review",
]
