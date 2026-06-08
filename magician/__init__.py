"""
Magician 美股 — 翻倍股猎手 统一入口
对齐 a_stock/__init__.py 和 haitao/__init__.py 模式

推荐消费方式:
    from magician import (
        MagicianPlatform,
        scan_doublers, score_doubler, predict_batch,
        find_short_opportunities, scan_short_candidates,
        scan_leveraged_etfs, analyze_leveraged,
    )

    p = MagicianPlatform()
    p.init_all()
    p.run_doubler_scan()
    p.run_short_scan()
"""

# ─── 平台生命周期 ────────────────────────
from magician.platform import MagicianPlatform

# ─── 翻倍股 ─────────────────────────────
from magician.doubler_scanner import (
    scan_doublers, score_doubler, recommend_doublers,
)
from magician.doubler_predictor import predict_batch
from magician.doubler_tracker import (
    save_recommendation, get_tracking_status,
    update_prices, get_monthly_report, verify_month,
)

# ─── 做空 ───────────────────────────────
from magician.short_finder import (
    find_short_opportunities, scan_short_candidates,
)

# ─── 杠杆ETF ────────────────────────────
from magician.leveraged_scanner import (
    scan_leveraged_etfs, analyze_leveraged, list_supported_etfs,
)

__all__ = [
    "MagicianPlatform",
    "scan_doublers", "score_doubler", "recommend_doublers",
    "predict_batch",
    "save_recommendation", "get_tracking_status",
    "update_prices", "get_monthly_report", "verify_month",
    "find_short_opportunities", "scan_short_candidates",
    "scan_leveraged_etfs", "analyze_leveraged", "list_supported_etfs",
]
