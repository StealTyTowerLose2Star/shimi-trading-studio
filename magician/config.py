"""
Magician 美股 — 翻倍股/做空/杠杆ETF 配置
从 haitao/config.py 提取，独立管理
"""

# ─── 翻倍股种子池 ──────────────────────────
DOUBLER_SEED_POOL = [
    # AI/半导体
    "NVDA", "AMD", "AVGO", "MU", "MRVL", "ARM", "INTC",
    # 科技巨头
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA",
    # 金融科技
    "COIN", "MSTR", "HOOD", "SOFI", "AFRM",
    # 云计算/SaaS
    "CRM", "SNOW", "NET", "DDOG", "MDB", "PLTR",
    # 生物科技
    "MRNA", "BNTX", "CRSP", "NTLA",
    # 新能源
    "ENPH", "FSLR", "PLUG", "FCEL",
    # 中概
    "BABA", "JD", "PDD", "BIDU", "NIO", "XPEV", "LI",
    # 加密货币
    "MARA", "RIOT", "CLSK",
    # 航天
    "RKLB", "ASTS",
    # 量子计算
    "IONQ", "RGTI", "QBTS",
    # 核能
    "OKLO", "SMR",
]

# ─── 3x 杠杆ETF关注列表 ──────────────────
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

# ─── 评分阈值 ────────────────────────────
DOUBLER_SCORE_THRESHOLD = 65     # 翻倍潜力最低分
SHORT_SCORE_THRESHOLD = 60       # 做空机会最低分
LEVERAGED_MAX_HOLD_DAYS = 5      # 杠杆ETF最大持仓天数
LEVERAGED_DECAY_WARN_PCT = 2.0   # 月波动衰减预警阈值(%)

# ─── D0启动模式检测参数 ────────────────
COILED_SPRING_MIN_DROP = -15      # 蓄力模式最小回调(%)
COILED_SPRING_MAX_DROP = -5       # 蓄力模式最大回调(%)
SILENT_ACCUM_DAYS = 15            # 默默吸筹检测天数
EARLY_WARMING_MIN_VOL = 1.3       # 早期预警最小量比
SMART_PULLBACK_MIN_DROP = -8      # 聪明回调最小幅度(%)
SMART_PULLBACK_MAX_DROP = -3      # 聪明回调最大幅度(%)

# ─── 催化剂权重 ──────────────────────────
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
}
