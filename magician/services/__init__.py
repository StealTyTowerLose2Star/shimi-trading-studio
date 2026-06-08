"""
Magician 美股 — 服务层统一入口
"""

# ─── 翻倍股服务 ────────────────────────
from magician.doubler_scanner import (
    scan_doublers, score_doubler, recommend_doublers,
)
from magician.doubler_predictor import predict_batch
from magician.doubler_tracker import (
    save_recommendation, get_tracking_status,
    update_prices, get_monthly_report, verify_month,
)

# ─── 做空服务 ──────────────────────────
from magician.short_finder import (
    find_short_opportunities, scan_short_candidates,
)

# ─── 杠杆ETF服务 ───────────────────────
from magician.leveraged_scanner import (
    scan_leveraged_etfs, analyze_leveraged, list_supported_etfs,
)

__all__ = [
    "scan_doublers", "score_doubler", "recommend_doublers",
    "predict_batch",
    "save_recommendation", "get_tracking_status",
    "update_prices", "get_monthly_report", "verify_month",
    "find_short_opportunities", "scan_short_candidates",
    "scan_leveraged_etfs", "analyze_leveraged", "list_supported_etfs",
]
