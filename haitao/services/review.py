"""
HiTao 美股 - 复盘服务
包装 haitao.us_review 每日/每周复盘，提供业务层接口

功能:
  run_daily_review()
    → 三大指数表现 + 热门股涨跌 + 摘要
    → Returns: {success, date, summary, details}

  run_weekly_review()
    → 周度指数走势 + 黄金扫描 Top 变化 + 盈亏统计 + 摘要
    → Returns: {success, week, summary, details}
"""

from haitao.us_review import (
    run_daily_review,
    run_weekly_review,
)

__all__ = [
    "run_daily_review",
    "run_weekly_review",
]
