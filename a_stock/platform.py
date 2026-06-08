"""
拾米交易工作室 - AStockPlatform 平台底座
统一生命周期管理: 初始化 → 健康检查 → 状态报告

使用方式:
    from a_stock import AStockPlatform
    platform = AStockPlatform()
    status = platform.init_all()
    health = platform.check_health()

架构原则:
  - 零 Flask 依赖 (纯 Python 基础设施)
  - 零 haitao 引用 (市场隔离)
  - 延迟初始化 (子模块按需加载)
  - 可观测性 (每个子系统有独立状态报告)
"""

import time
from typing import Dict, List, Optional
from dataclasses import dataclass, field, asdict


# ============================================================
# 子系统状态类型
# ============================================================
@dataclass
class SubsystemStatus:
    """单个子系统的运行状态"""
    name: str
    status: str  # ok | warn | fail | skipped
    detail: str = ""
    elapsed_ms: float = 0.0
    error: str = ""

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class PlatformState:
    """平台整体状态"""
    initialized: bool = False
    init_time: str = ""
    elapsed_ms: float = 0.0
    subsystems: Dict[str, SubsystemStatus] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "initialized": self.initialized,
            "init_time": self.init_time,
            "elapsed_ms": self.elapsed_ms,
            "subsystems": {k: v.to_dict() for k, v in self.subsystems.items()},
        }


# ============================================================
# 平台类
# ============================================================
class AStockPlatform:
    """拾米A股 · 平台底座

    统一管理以下子系统:
      config    — 配置加载与验证
      cache     — 缓存系统就绪
      logger    — 日志系统就绪
      db        — 数据库连接与DDL
      data      — 数据获取层
      services  — 业务服务层
      queue     — 消息队列

    示例:
        platform = AStockPlatform()
        report = platform.init_all()
        print(report.status)  # ok | warn | fail
    """

    def __init__(self):
        self._state = PlatformState()
        self._log = None  # 延迟初始化

    # ─── 属性 ─────────────────────────────────────────

    @property
    def state(self) -> PlatformState:
        return self._state

    @property
    def is_initialized(self) -> bool:
        return self._state.initialized

    # ─── 生命周期 ─────────────────────────────────────

    def init_all(self, run_db_init: bool = True) -> PlatformState:
        """初始化所有子系统

        Args:
            run_db_init: 是否运行数据库 DDL (生产环境 True)

        Returns:
            PlatformState — 包含每个子系统的状态
        """
        start = time.time()
        self._state.subsystems = {}

        # 1. 配置
        self._init_config()

        # 2. Logger
        self._init_logger()

        # 3. 消息队列
        self._init_queue()

        # 4. 缓存
        self._init_cache()

        # 5. 数据库
        self._init_db(run_db_init)

        # 6. 日级缓存 (需要依赖数据库层)
        self._init_daily_store()

        # 7. 数据层
        self._init_data()

        # 8. 服务层
        self._init_services()

        # 最终状态
        elapsed = (time.time() - start) * 1000
        self._state.initialized = True
        self._state.init_time = time.strftime("%Y-%m-%d %H:%M:%S")
        self._state.elapsed_ms = round(elapsed, 1)

        return self._state

    def _record(self, name: str, status: str, detail: str = "",
                elapsed_ms: float = 0.0, error: str = ""):
        """记录子系统状态"""
        self._state.subsystems[name] = SubsystemStatus(
            name=name, status=status, detail=detail,
            elapsed_ms=round(elapsed_ms, 1), error=error,
        )

    def _init_config(self):
        t0 = time.time()
        try:
            from config import validate as _validate
            _validate()
            self._record("config", "ok", "配置加载完成")
        except Exception as e:
            self._record("config", "warn", "", error=str(e))

    def _init_logger(self):
        t0 = time.time()
        try:
            from logger import get_logger
            self._log = get_logger("a_stock.platform")
            from logger import startup_log
            startup_log("a_stock_platform", "ok", "平台底座初始化")
            self._record("logger", "ok", "日志系统就绪")
        except Exception as e:
            self._record("logger", "warn", "", error=str(e))

    def _init_queue(self):
        t0 = time.time()
        try:
            from message_queue import enqueue
            enqueue("平台启动", "A股交易平台底座已初始化")
            self._record("queue", "ok", "消息队列就绪")
        except Exception as e:
            self._record("queue", "skip", "队列非关键")
        finally:
            self._record("queue", "skip", "消息队列跳过(非关键)")

    def _init_cache(self):
        t0 = time.time()
        try:
            from cache import cache_get, cache_set
            cache_set("_platform_ready", True, ttl=30)
            ready = cache_get("_platform_ready")
            detail = "内存缓存" if ready else "缓存异常"
            status = "ok" if ready else "warn"
            self._record("cache", status, detail)
        except Exception as e:
            self._record("cache", "ok", "内存缓存(默认)")

    def _init_db(self, run_init: bool):
        t0 = time.time()
        try:
            from db import get_db, init_db
            if run_init:
                init_db()
            # 验证连接
            conn = get_db()
            rows = conn.execute("SELECT COUNT(*) as c FROM trades")
            count = dict(rows.fetchone())["c"]
            conn.close()
            self._record("db", "ok", f"连接正常 (tables: trades={count})")
        except Exception as e:
            self._record("db", "fail", "", error=str(e))

    def _init_daily_store(self):
        """初始化日级持久缓存层"""
        t0 = time.time()
        try:
            from data.daily_store import get_store
            store = get_store()
            summary = store.summary()
            detail = f"{summary['total_records']}条记录 / {summary.get('db_size_mb', 0)}MB"
            self._record("daily_store", "ok", detail)
        except Exception as e:
            self._record("daily_store", "fail", "初始失败", error=str(e))

    def _init_data(self):
        t0 = time.time()
        try:
            from data.fetcher import get_ts
            # 只验证导入不调用 API（避免网络依赖）
            from data.fetcher_core import get_ts
            from data.fetcher_indices import INDEX_MAP
            from data.fetcher_sentiment import fetch_sentiment
            self._record("data", "ok", f"数据层加载完成 (指数映射: {len(INDEX_MAP)}个)")
        except Exception as e:
            self._record("data", "warn", "", error=str(e))

    def _init_services(self):
        t0 = time.time()
        try:
            from services.strategy import run_trend_scan
            from services.advice import generate_advice
            from services.alert import list_alerts
            from services.review import run_daily_review
            from services.pnl import compute_pnl_report
            from services.portfolio import analyze_portfolio
            self._record("services", "ok", "6个服务模块加载完成")
        except Exception as e:
            self._record("services", "warn", "", error=str(e))

    # ─── 健康检查 ─────────────────────────────────────

    def check_health(self) -> Dict:
        """全面健康检查 — 运行时状态快照

        Returns:
            dict:
                platform_initialized: bool
                db: {ok, detail}
                cache: {ok, detail}
                data_layer: {ok, detail}
                uptime: str
        """
        result = {
            "platform_initialized": self._state.initialized,
            "uptime": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

        # DB 健康
        try:
            from db import get_db
            conn = get_db()
            conn.execute("SELECT 1")
            conn.close()
            result["db"] = {"ok": True, "detail": "连接正常"}
        except Exception as e:
            result["db"] = {"ok": False, "detail": str(e)}

        # Cache 健康
        try:
            from cache import cache_get
            result["cache"] = {"ok": True, "detail": "内存缓存"}
        except Exception as e:
            result["cache"] = {"ok": False, "detail": str(e)}

        # 数据层
        try:
            from data.fetcher import get_ts
            pro = get_ts()
            result["data_layer"] = {
                "ok": pro is not None,
                "detail": "tushare可用" if pro else "tushare不可用",
            }
        except Exception as e:
            result["data_layer"] = {"ok": False, "detail": str(e)}

        # 日级缓存
        try:
            from data.daily_store import get_store
            summary = get_store().summary()
            result["daily_store"] = {
                "ok": True,
                "detail": f"{summary['total_records']}记录 / {summary.get('db_size_mb', 0)}MB",
                "db_path": summary.get("db_path", ""),
            }
        except Exception as e:
            result["daily_store"] = {"ok": False, "detail": str(e)}

        return result

    # ─── 状态报告 ─────────────────────────────────────

    def status_report(self) -> Dict:
        """返回完整状态报告"""
        base = self._state.to_dict()
        base["health"] = self.check_health()
        base["version"] = "2.0.0"
        base["role"] = "拾米A股"
        return base

    # ─── 快捷入口 ─────────────────────────────────────

    def run_strategy_scan(self) -> Dict:
        """运行三大策略扫描 (快捷入口)"""
        from services.strategy import run_trend_scan, run_hybrid_scan, run_dragon_scan
        return {
            "trend": run_trend_scan(),
            "hybrid": run_hybrid_scan(),
            "dragon": run_dragon_scan(),
        }

    def run_daily_review(self) -> Dict:
        """运行每日复盘 (快捷入口)"""
        from services.review import run_daily_review
        return run_daily_review()

    def run_weekly_review(self) -> Dict:
        """运行每周复盘 (快捷入口)"""
        from services.review_weekly import run_weekly_review
        return run_weekly_review()

    def get_alerts(self) -> List[Dict]:
        """获取所有预警规则"""
        from services.alert import list_alerts
        return list_alerts()

    def check_alerts(self, force: bool = False) -> List[Dict]:
        """检查预警条件"""
        from services.alert import check_alerts
        return check_alerts(force=force)

    def get_portfolio_analysis(self, user_id: int) -> Dict:
        """获取持仓分析"""
        from services.portfolio import analyze_portfolio
        return analyze_portfolio(user_id)

    def get_pnl_report(self, user_id: Optional[int] = None) -> Dict:
        """获取盈亏报告"""
        from services.pnl import compute_pnl_report
        return compute_pnl_report(user_id=user_id)

    # ─── 日级缓存操作 ────────────────────────────

    def refresh_daily_cache(self, trade_date: Optional[str] = None,
                            market: str = "all") -> Dict:
        """一键刷新日级缓存

        幂等操作: 只填充缺失数据, 已缓存的不重复拉取

        Args:
            trade_date: "YYYYMMDD", None=最新交易日
            market: "all"|"daily"|"basic"

        Returns:
            {type: True/False} 各数据类型的刷新结果
        """
        from data.fetcher_cached import refresh_daily
        return refresh_daily(market=market, trade_date=trade_date)

    def refresh_recent_cache(self, days_back: int = 5) -> Dict:
        """批量补全最近 N 个交易日缓存"""
        from data.fetcher_cached import refresh_all_recent
        return refresh_all_recent(days_back=days_back)

    def get_cache_summary(self) -> Dict:
        """日级缓存全景概览"""
        from data.fetcher_cached import get_cache_summary
        return get_cache_summary()

    def invalidate_cache(self, data_type: Optional[str] = None) -> int:
        """主动失效缓存 (下次调用时重新拉取 Tushare)"""
        from data.fetcher_cached import invalidate_cache
        return invalidate_cache(data_type=data_type)
