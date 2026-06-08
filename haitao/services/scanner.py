"""
HiTao 美股 - 技术评分与扫描服务
包装 haitao.us_scanner 核心评分引擎，提供业务层接口

评分维度（五维 0-100）:
  1. 趋势强度 (30分): MA 排列 + 价格位置
  2. 动量因子 (25分): RSI + MACD
  3. 成交量验证 (20分): 量比 + 均量趋势
  4. 波动率评估 (15分): ATR% + 布林带位置
  5. 阶段判定 (10分): 鱼头/鱼身/鱼尾

数据源: yfinance (通过 haitao.us_fetcher 控制)
"""

from haitao.us_scanner import (
    score_stock,
    scan_watchlist,
    scan_top_gainers,
    scan_adr_picks,
)

__all__ = [
    "score_stock",
    "scan_watchlist",
    "scan_top_gainers",
    "scan_adr_picks",
]
