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
# Magician（美股翻倍股猎手）配置
# ═══════════════════════════════════════════════════════════════

# ─── 翻倍股种子池（扩展关注列表）────────────────
DOUBLER_SEED_POOL = [
    # AI/半导体
    "NVDA", "AMD", "AVGO", "MU", "MRVL", "ARM", "INTC",
    # 云计算/SaaS
    "CRM", "NOW", "DDOG", "MDB", "SNOW", "WDAY", "ADBE",
    # 新能源/电动汽车
    "TSLA", "RIVN", "LCID", "NIO", "XPEV", "LI",
    # 生物科技
    "MRNA", "BNTX", "ILMN", "REGN", "VRTX",
    # 加密/金融科技
    "COIN", "MSTR", "HOOD", "SQ", "SOFI",
    # 中概股
    "BABA", "JD", "PDD", "BIDU", "TCOM", "BILI",
    # 航天国防
    "SPCE", "RTX", "BA", "LMT",
    # 消费/电商
    "AMZN", "META", "SNAP", "DASH", "UBER", "LYFT",
    # 机器人/自动化
    "TSM", "QCOM", "ROK",
]

# ─── 3x 杠杆ETF关注列表 ──────────────────────────
LEVERAGED_3X_ETFS = [
    "TQQQ", "QLD",     # 纳斯达克3x/2x做多
    "SQQQ", "PSQ",     # 纳斯达克3x/2x做空
    "SPXL", "SSO",     # 标普3x/2x做多
    "SPXS", "SDS",     # 标普3x/2x做空
    "TNA", "UDOW",     # 罗素2000做多
    "TZA", "SDOW",     # 罗素2000做空
    "SOXL", "FAS",     # 半导体/金融做多
    "SOXS", "FAZ",     # 半导体/金融做空
    "LABU",             # 生物科技做多
    "LABD",             # 生物科技做空
    "JNUG", "JDST",    # 金矿做多/做空
    "NUGT", "DUST",    # 金矿做多/做空
    "DRIP",             # 能源做空
    "ERX", "ERY",       # 能源做多/做空
]

# ─── 评分阈值 ────────────────────────────────
DOUBLER_SCORE_THRESHOLD = 65     # 翻倍潜力最低分
SHORT_SCORE_THRESHOLD = 60       # 做空机会最低分
LEVERAGED_MAX_HOLD_DAYS = 5      # 杠杆ETF最大持仓天数
LEVERAGED_DECAY_WARN_PCT = 2.0   # 月波动衰减预警阈值(%)

# ─── 催化剂权重 ──────────────────────────────
CATALYST_WEIGHTS = {
    "C1": {"name": "量价启动", "max": 15},
    "C2": {"name": "趋势强度", "max": 12},
    "C3": {"name": "机构进场", "max": 10},
    "C4": {"name": "估值合理", "max": 10},
    "C5": {"name": "财报窗口", "max": 10},
    "C6": {"name": "赛道热度", "max": 10},
    "C7": {"name": "资金流入", "max": 8},
    "C8": {"name": "做空压制释放", "max": 8},
    "C9": {"name": "盘前异动", "max": 7},
    "C10": {"name": "波动弹性", "max": 5},
    # 做空专用
    "S1": {"name": "过度延伸", "max": 20},
    "S2": {"name": "放量滞涨", "max": 15},
    "S3": {"name": "财报前波动", "max": 10},
    "S4": {"name": "估值泡沫", "max": 10},
}

# ─── 做多模式检测阈值 ────────────────────────
COILED_SPRING_MIN_DROP = -15      # 蓄力模式最小回调(%)
COILED_SPRING_MAX_DROP = -5       # 蓄力模式最大回调(%)
SILENT_ACCUM_DAYS = 15            # 默默吸筹检测天数
EARLY_WARMING_MIN_VOL = 1.3       # 早期预警最小量比
SMART_PULLBACK_MIN_DROP = -8      # 聪明回调最小幅度(%)
SMART_PULLBACK_MAX_DROP = -3      # 聪明回调最大幅度(%)
