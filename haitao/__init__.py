"""
HiTao 美股 — 统一入口（对齐 a_stock/__init__.py）

所有美股组件推荐消费方式:

    from haitao import (
        # 服务层
        score_stock, scan_watchlist,
        evaluate_us_position, batch_evaluate_us,
        calculate_pnl,
        run_daily_review, run_weekly_review,

        # 平台生命周期
        HiTaoPlatform,
    )

    p = HiTaoPlatform()
    p.init_all()
"""

# ─── 服务层 ──────────────────────────────────
from haitao.services import (
    score_stock, scan_watchlist, scan_top_gainers, scan_adr_picks,
    evaluate_us_position, batch_evaluate_us,
    calculate_pnl,
    run_daily_review, run_weekly_review,
)

# ─── 平台生命周期 ────────────────────────────
from haitao.platform import HiTaoPlatform

__all__ = [
    "score_stock", "scan_watchlist", "scan_top_gainers", "scan_adr_picks",
    "evaluate_us_position", "batch_evaluate_us",
    "calculate_pnl",
    "run_daily_review", "run_weekly_review",
    "HiTaoPlatform",
]
