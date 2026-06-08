"""
Magician 美股 — 平台生命周期管理
对齐 haitao/platform.py 和 a_stock/platform.py
"""

import logging

logger = logging.getLogger(__name__)


class MagicianPlatform:
    """Magician 美股翻倍猎手 — 统一生命周期管理"""

    def __init__(self):
        self._initialized = False

    def init_all(self) -> bool:
        """初始化 Magician 组件"""
        if self._initialized:
            logger.info("Magician 已初始化，跳过")
            return True
        try:
            from magician.config import DOUBLER_SEED_POOL
            logger.info(f"Magician 初始化完成 — 种子池: {len(DOUBLER_SEED_POOL)} 只")
        except Exception as e:
            logger.warning(f"Magician 初始化警告: {e}")
        self._initialized = True
        return True

    def run_doubler_scan(self, tickers=None) -> dict:
        """翻倍股扫描"""
        from magician.doubler_scanner import scan_doublers
        from magician.config import DOUBLER_SEED_POOL
        targets = tickers or DOUBLER_SEED_POOL
        results = scan_doublers(targets)
        return {"count": len(results), "results": results}

    def run_short_scan(self, tickers=None) -> dict:
        """做空机会扫描"""
        from magician.short_finder import scan_short_candidates
        from magician.config import DOUBLER_SEED_POOL
        targets = tickers or DOUBLER_SEED_POOL
        results = scan_short_candidates(targets)
        return results

    def run_leveraged_scan(self, direction="all") -> dict:
        """杠杆ETF扫描"""
        from magician.leveraged_scanner import scan_leveraged_etfs
        return scan_leveraged_etfs(filters={"direction": direction})

    def run_doubler_predict(self, tickers=None) -> dict:
        """翻倍预测"""
        from magician.doubler_predictor import predict_batch
        from magician.config import DOUBLER_SEED_POOL
        targets = tickers or DOUBLER_SEED_POOL
        results = predict_batch(targets)
        return {"count": len(results), "results": results}
