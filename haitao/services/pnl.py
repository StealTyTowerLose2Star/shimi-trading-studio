"""
HiTao 美股 - 盈亏分析服务
包装 haitao.us_pnl 盈亏追踪，提供业务层接口

功能:
  calculate_pnl(period="month"|"year"|"week")
    → 按 period 聚合平仓交易的 PnL、胜率、总盈亏
    → Report 含按月/年/周分组统计
"""

from haitao.us_pnl import calculate_pnl

__all__ = ["calculate_pnl"]
