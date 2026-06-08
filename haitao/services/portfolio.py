"""
HiTao 美股 - 持仓评估服务
包装 haitao.us_position 做多/做空持仓管理，提供业务层接口

功能:
  evaluate_us_position(ticker, entry_price, direction, qty, entry_date)
    → ATR 浮动止盈阶梯 T1/T2/T3 + 动态止损随价格上移
    → 支持做多(buy)和做空(sell)双方向

  batch_evaluate_us(positions)
    → 批量评估持仓列表
"""

from haitao.us_position import (
    evaluate_us_position,
    batch_evaluate_us,
)

__all__ = [
    "evaluate_us_position",
    "batch_evaluate_us",
]
