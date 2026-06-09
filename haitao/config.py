"""海淘美股 - 配置模块
US stocks config: ticker lists, API keys, cache TTL
"""
import os

# ─── 美股指数 Ticker ─────────────────────────
US_INDICES = {
    "^GSPC": "S&P 500",
    "^IXIC": "纳斯达克",
    "^DJI": "道琼斯",
    "^RUT": "罗素2000",
    "^VIX": "VIX恐慌指数",
    "SOXX": "费城半导体",
    "XLF": "金融板块ETF",
    "XLE": "能源板块ETF",
}

# ─── 热门中概股 ──────────────────────────────
CHINESE_ADR = [
    "BABA", "JD", "PDD", "BIDU", "NIO", "XPEV", "LI",
    "TCOM", "BILI", "NTES", "YUMC", "BEKE", "IQ",
    "DIDIY", "TAL", "EDU", "ZLAB", "HTHT",
]

# ─── 热门美股 Ticker ─────────────────────────
HOT_US_STOCKS = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "META",
    "NVDA", "TSLA", "AMD", "AVGO", "PLTR",
    "SPY", "QQQ", "IWM",
]

# ─── 海淘关注池（用户可自定义） ────────────
HAITAO_WATCHLIST = os.getenv("HAITAO_WATCHLIST", "").split(",") if os.getenv("HAITAO_WATCHLIST") else []

# ─── 缓存 TTL ─────────────────────────────────
CACHE_TTL_QUOTES = 30       # 实时报价（秒）
CACHE_TTL_INDICES = 60      # 指数
CACHE_TTL_HISTORY = 300     # 日线历史
CACHE_TTL_SCAN = 600        # 扫描结果

# ─── 美国市场交易时间 ──────────────────────
# 美国东部时间 9:30-16:00 (正常盘)
# 盘前 4:00-9:30, 盘后 16:00-20:00
US_MARKET_OPEN_HOUR = 9
US_MARKET_OPEN_MIN = 30
US_MARKET_CLOSE_HOUR = 16
US_MARKET_CLOSE_MIN = 0

# ─── 项目根目录 ──────────────────────────────
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ═══════════════════════════════════════════════════════════════
# Magician 配置 → 已迁移至 magician/config.py
# 以下仅保留向后兼容重导出，新代码请用 from magician.config import ...
# ═══════════════════════════════════════════════════════════════
from magician.config import (
    DOUBLER_SEED_POOL, LEVERAGED_3X_ETFS,
    DOUBLER_SCORE_THRESHOLD, SHORT_SCORE_THRESHOLD,
    LEVERAGED_MAX_HOLD_DAYS, LEVERAGED_DECAY_WARN_PCT,
    CATALYST_WEIGHTS,
    COILED_SPRING_MIN_DROP, COILED_SPRING_MAX_DROP,
    SILENT_ACCUM_DAYS, EARLY_WARMING_MIN_VOL,
    SMART_PULLBACK_MIN_DROP, SMART_PULLBACK_MAX_DROP,
)
