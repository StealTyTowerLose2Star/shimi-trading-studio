"""
HiTao 美股 — 平台生命周期管理（对齐 a_stock/platform.py）

提供统一的初始化入口和服务编排。

使用方式:
    p = HiTaoPlatform()
    p.init_all()           # 初始化所有美股组件
    p.run_scan()           # 执行扫描
    p.check_short_candidates()  # 检查做空机会
"""

import logging

logger = logging.getLogger(__name__)


from haitao.services.review import run_daily_review as _run_daily_review
from haitao.services.review import run_weekly_review as _run_weekly_review


class HiTaoPlatform:
    """HiTao 美股平台 — 统一生命周期管理"""

    def __init__(self):
        self._initialized = False

    def init_all(self) -> bool:
        """初始化所有美股组件（缓存、数据源配置等）"""
        if self._initialized:
            logger.info("HiTao 平台已初始化，跳过")
            return True

        try:
            # 验证数据源可用性
            from haitao.us_fetcher import get_us_market_status
            status = get_us_market_status()
            logger.info(f"HiTao 平台初始化完成 — 市场状态: {status.get('status', 'unknown')}")
        except Exception as e:
            logger.warning(f"HiTao 平台初始化警告: {e}")
            # 不阻塞启动，数据源不可用时降级运行

        self._initialized = True
        return True

    def run_scan(self, mode: str = "hot") -> dict:
        """执行美股扫描

        Args:
            mode: "hot" | "adr" | "gainers"

        Returns:
            dict: scan results
        """
        from haitao.services.scanner import (
            scan_watchlist, scan_top_gainers, scan_adr_picks,
        )
        from haitao.config import HOT_US_STOCKS

        if mode == "adr":
            results = scan_adr_picks()
        elif mode == "gainers":
            results = scan_top_gainers()
        else:
            results = scan_watchlist(HOT_US_STOCKS)

        return {"mode": mode, "count": len(results), "results": results}

    # check_short_candidates → 已迁移至 magician/platform.py (Magician职责)

    def run_gold_scan(self, mode: str = "hot") -> dict:
        """黄金挖掘扫描"""
        from haitao.us_gold_scanner import gold_pan, gold_pan_adr, gold_pan_top_gainers
        from haitao.config import HOT_US_STOCKS

        if mode == "adr":
            results = gold_pan_adr()
        elif mode == "gainers":
            results = gold_pan_top_gainers()
        else:
            results = gold_pan(HOT_US_STOCKS)

        return {"mode": mode, "count": len(results), "results": results}

    def run_daily_review(self) -> dict:
        """每日复盘"""
        return _run_daily_review()

    def run_weekly_review(self) -> dict:
        """每周复盘"""
        return _run_weekly_review()
