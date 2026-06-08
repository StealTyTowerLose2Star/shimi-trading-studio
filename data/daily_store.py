"""
拾米交易工作室 - 本地日级数据持久存储
Tushare 数据每日落地一次，读本地为主，降消耗提效率

架构:
  策略: 写穿缓存 (write-through cache)
  - 首次调用 → Tushare API → 写 SQLite → 返回
  - 后续调用 → 读 SQLite (零 API 消耗)
  - 交易日切换 → 自动触发增量刷新

设计要点:
  - DataFrame 以 JSON 行格式存储 (records oriented)
  - 字典/列表直接 JSON 序列化
  - 每张表按 (data_type, trade_date) 做复合主键
  - 自动淘汰过期数据 (节省磁盘)
"""

import json
import os
import sqlite3
import time
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional, Any, Dict, List, Union

import config

# ============================================================
# 常量
# ============================================================

# 数据类型 → 保留天数
RETENTION_DAYS: Dict[str, int] = {
    "daily": 5,         # 全市场日行情 — 保留 5 个交易日
    "daily_basic": 5,   # 日线基础指标
    "stock_basic": 7,   # 股票基础信息 (每周刷新)
    "indices": 7,       # 指数数据
    "sectors": 7,       # 板块排行
    "sentiment": 7,     # 情绪分析
    "limit_up": 7,      # 涨停板
    "limit_down": 7,    # 跌停板
}

# 无需每日刷新的类型
WEEKLY_TYPES = {"stock_basic", "indices", "sectors"}


def _default_db_path() -> str:
    """daily_store.db 与 shimi.db 同目录"""
    db_dir = os.path.dirname(config.DB_PATH) if os.path.dirname(config.DB_PATH) else "."
    return os.path.join(db_dir, "daily_store.db")


# ============================================================
# 存储层
# ============================================================

class DailyStore:
    """日级数据持久存储

    使用示例:
        store = DailyStore()
        store.save_data("daily", "20240101", df.to_dict(orient="records"))
        records = store.load_data("daily", "20240101")  # → list[dict] | None

    写入模式:
        save_data     - 不复写已存在的数据 (幂等, 节省磁盘 IO)
        save_overwrite - 强制覆写 (用于手动刷新)
    """

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or _default_db_path()
        self._init_db()

    # ─── DDL ───────────────────────────────────────────────

    def _init_db(self):
        """建表 (幂等, 第一次运行时自动执行)"""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS daily_data (
                    data_type   TEXT NOT NULL,
                    trade_date  TEXT NOT NULL,
                    data_json   TEXT NOT NULL,
                    row_count   INTEGER DEFAULT 0,
                    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                    PRIMARY KEY (data_type, trade_date)
                );

                CREATE TABLE IF NOT EXISTS daily_kline (
                    ts_code     TEXT NOT NULL,
                    trade_date  TEXT NOT NULL,
                    data_json   TEXT NOT NULL,
                    row_count   INTEGER DEFAULT 0,
                    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                    PRIMARY KEY (ts_code, trade_date)
                );

                CREATE TABLE IF NOT EXISTS daily_meta (
                    key   TEXT PRIMARY KEY,
                    value TEXT
                );
            """)
            conn.commit()
        finally:
            conn.close()

    # ─── 连接 ───────────────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        """获取连接 (每次调用新建, 线程安全)"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ─── 通用数据 (daily_data 表) ──────────────────────────

    def save_data(self, data_type: str, trade_date: str,
                  data: Any, overwrite: bool = False) -> bool:
        """保存结构化数据到日库

        Args:
            data_type:  数据类型 (daily / daily_basic / stock_basic / ...)
            trade_date: 交易日 "YYYYMMDD" 或 "latest"
            data:       DataFrame(自动转json), list[dict], dict, 或 JSON字符串
            overwrite:  是否覆写已有记录 (默认 False = 幂等跳过)

        Returns:
            True=成功写入, False=已存在跳过
        """
        # 序列化
        json_str, row_count = self._serialize(data)
        if json_str is None:
            return False

        conn = self._conn()
        try:
            if overwrite:
                conn.execute(
                    "INSERT OR REPLACE INTO daily_data (data_type, trade_date, data_json, row_count) VALUES (?, ?, ?, ?)",
                    (data_type, trade_date, json_str, row_count),
                )
            else:
                cur = conn.execute(
                    "SELECT 1 FROM daily_data WHERE data_type=? AND trade_date=?",
                    (data_type, trade_date),
                )
                if cur.fetchone() is not None:
                    return False  # 已存在, 略过
                conn.execute(
                    "INSERT INTO daily_data (data_type, trade_date, data_json, row_count) VALUES (?, ?, ?, ?)",
                    (data_type, trade_date, json_str, row_count),
                )
            conn.commit()
            return True
        finally:
            conn.close()

    def load_data(self, data_type: str,
                  trade_date: Optional[str] = None) -> Optional[Any]:
        """读取日库数据

        Args:
            data_type:  数据类型
            trade_date: 交易日, None=返回最新

        Returns:
            list[dict] / dict / str  (取决于原始数据类型), None=不存在
        """
        conn = self._conn()
        try:
            if trade_date:
                cur = conn.execute(
                    "SELECT data_json FROM daily_data WHERE data_type=? AND trade_date=?",
                    (data_type, trade_date),
                )
            else:
                cur = conn.execute(
                    "SELECT data_json FROM daily_data WHERE data_type=? ORDER BY trade_date DESC LIMIT 1",
                    (data_type,),
                )
            row = cur.fetchone()
            if row is None:
                return None
            return json.loads(row["data_json"]) if row["data_json"] else None
        finally:
            conn.close()

    def has_data(self, data_type: str, trade_date: str) -> bool:
        """检查某类型某日是否已缓存"""
        conn = self._conn()
        try:
            cur = conn.execute(
                "SELECT 1 FROM daily_data WHERE data_type=? AND trade_date=?",
                (data_type, trade_date),
            )
            return cur.fetchone() is not None
        finally:
            conn.close()

    def get_latest_trade_date(self, data_type: str) -> Optional[str]:
        """获取某类型最新缓存交易日"""
        conn = self._conn()
        try:
            cur = conn.execute(
                "SELECT trade_date FROM daily_data WHERE data_type=? ORDER BY trade_date DESC LIMIT 1",
                (data_type,),
            )
            r = cur.fetchone()
            return r["trade_date"] if r else None
        finally:
            conn.close()

    def get_cached_dates(self, data_type: str) -> List[str]:
        """返回某类型所有缓存交易日 (有序)"""
        conn = self._conn()
        try:
            cur = conn.execute(
                "SELECT trade_date FROM daily_data WHERE data_type=? ORDER BY trade_date DESC",
                (data_type,),
            )
            return [r["trade_date"] for r in cur.fetchall()]
        finally:
            conn.close()

    def needs_refresh(self, data_type: str,
                      latest_trade_date: Optional[str] = None) -> bool:
        """判断是否需要刷新

        规则:
          - 周刷新类型 (stock_basic/indices/sectors): 
              使用 meta 中记录的刷新时间, 超 7 天刷新
          - 日刷新类型: 
              缓存日期 ≠ 最新交易日 → 刷新
        """
        is_weekly = data_type in WEEKLY_TYPES
        latest = self.get_latest_trade_date(data_type)

        if latest is None:
            return True  # 从未缓存

        if is_weekly:
            # 检查上次刷新时间
            refresh_key = f"{data_type}_refreshed"
            refreshed = self.get_meta(refresh_key)
            if refreshed:
                try:
                    dt = datetime.strptime(refreshed, "%Y-%m-%d %H:%M:%S")
                    return (datetime.now() - dt).days >= 7
                except ValueError:
                    pass
            # 没有 meta 记录 → 看创建时间戳
            return False  # 有数据就算可用

        # 日级: 缓存日期 ≠ 最新交易日
        if latest_trade_date is None:
            return True  # 无法判断, 保守刷新
        return latest != latest_trade_date

    def count_data(self, data_type: Optional[str] = None) -> Dict[str, int]:
        """统计各类型缓存记录数"""
        conn = self._conn()
        try:
            if data_type:
                cur = conn.execute(
                    "SELECT data_type, COUNT(*) as cnt FROM daily_data WHERE data_type=? GROUP BY data_type",
                    (data_type,),
                )
            else:
                cur = conn.execute(
                    "SELECT data_type, COUNT(*) as cnt FROM daily_data GROUP BY data_type",
                )
            return {r["data_type"]: r["cnt"] for r in cur.fetchall()}
        finally:
            conn.close()

    # ─── K 线存储 (daily_kline 表) ──────────────────────

    def save_kline(self, ts_code: str, trade_date: str,
                   data: Any, overwrite: bool = False) -> bool:
        """保存个股 K 线数据"""
        json_str, row_count = self._serialize(data)
        if json_str is None:
            return False

        conn = self._conn()
        try:
            if overwrite:
                conn.execute(
                    "INSERT OR REPLACE INTO daily_kline (ts_code, trade_date, data_json, row_count) VALUES (?, ?, ?, ?)",
                    (ts_code, trade_date, json_str, row_count),
                )
            else:
                cur = conn.execute(
                    "SELECT 1 FROM daily_kline WHERE ts_code=? AND trade_date=?",
                    (ts_code, trade_date),
                )
                if cur.fetchone() is not None:
                    return False
                conn.execute(
                    "INSERT INTO daily_kline (ts_code, trade_date, data_json, row_count) VALUES (?, ?, ?, ?)",
                    (ts_code, trade_date, json_str, row_count),
                )
            conn.commit()
            return True
        finally:
            conn.close()

    def load_kline(self, ts_code: str,
                   trade_date: Optional[str] = None) -> Optional[Any]:
        """读取个股 K 线"""
        conn = self._conn()
        try:
            if trade_date:
                cur = conn.execute(
                    "SELECT data_json FROM daily_kline WHERE ts_code=? AND trade_date=?",
                    (ts_code, trade_date),
                )
            else:
                cur = conn.execute(
                    "SELECT data_json FROM daily_kline WHERE ts_code=? ORDER BY trade_date DESC LIMIT 1",
                    (ts_code,),
                )
            row = cur.fetchone()
            if row is None:
                return None
            return json.loads(row["data_json"]) if row["data_json"] else None
        finally:
            conn.close()

    # ─── 维护 ───────────────────────────────────────────────

    def prune(self, data_type: Optional[str] = None) -> Dict[str, int]:
        """淘汰过期数据 — 按交易日计数，非自然日

        规则:
          - 日级类型 (daily / daily_basic):
              保留最近 N 个**交易日** (N=RETENTION_DAYS[type])
              如 N=5 且最近5个交易日是 [6/8,6/7,6/4,6/3,6/2]
              则删除所有不在这个列表里的行
          - 周级类型 (stock_basic / indices / sectors):
              按自然日处理 (7天), 因为它们使用 trade_date='latest'

        Args:
            data_type: 指定类型, None=全部

        Returns:
            {data_type: deleted_rows}
        """
        types = [data_type] if data_type else list(RETENTION_DAYS.keys())
        result: Dict[str, int] = {}
        conn = self._conn()
        try:
            for dt in types:
                days = RETENTION_DAYS.get(dt, 14)
                is_weekly = dt in WEEKLY_TYPES

                if is_weekly:
                    # 周级: 按自然日处理
                    cutoff = (date.today() - timedelta(days=days)).strftime("%Y%m%d")
                    cur = conn.execute(
                        "DELETE FROM daily_data WHERE data_type=? AND trade_date < ?",
                        (dt, cutoff),
                    )
                    result[dt] = cur.rowcount
                else:
                    # 日级: 按交易日计数 — 保留最近 N 个交易日
                    cur = conn.execute(
                        "SELECT DISTINCT trade_date FROM daily_data "
                        "WHERE data_type=? AND trade_date != 'latest' "
                        "ORDER BY trade_date DESC LIMIT ?",
                        (dt, days),
                    )
                    keep_dates = [r["trade_date"] for r in cur.fetchall()]
                    if not keep_dates:
                        result[dt] = 0
                        continue
                    placeholders = ",".join("?" for _ in keep_dates)
                    cur = conn.execute(
                        f"DELETE FROM daily_data WHERE data_type=? "
                        f"AND trade_date != 'latest' "
                        f"AND trade_date NOT IN ({placeholders})",
                        (dt, *keep_dates),
                    )
                    result[dt] = cur.rowcount
            conn.commit()
        finally:
            conn.close()
        return result

    def clear(self, data_type: Optional[str] = None) -> int:
        """清空缓存数据"""
        conn = self._conn()
        try:
            if data_type:
                cur = conn.execute("DELETE FROM daily_data WHERE data_type=?", (data_type,))
            else:
                cur = conn.execute("DELETE FROM daily_data")
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def summary(self) -> Dict:
        """缓存概览"""
        counts = self.count_data()
        return {
            "total_types": len(counts),
            "total_records": sum(counts.values()),
            "by_type": counts,
            "db_path": self.db_path,
            "db_size_mb": round(os.path.getsize(self.db_path) / 1024 / 1024, 2) if os.path.exists(self.db_path) else 0,
        }

    # ─── 元数据 ─────────────────────────────────────────────

    def set_meta(self, key: str, value: str):
        conn = self._conn()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO daily_meta (key, value) VALUES (?, ?)", (key, value)
            )
            conn.commit()
        finally:
            conn.close()

    def get_meta(self, key: str, default: Optional[str] = None) -> Optional[str]:
        conn = self._conn()
        try:
            cur = conn.execute("SELECT value FROM daily_meta WHERE key=?", (key,))
            r = cur.fetchone()
            return r["value"] if r else default
        finally:
            conn.close()

    # ─── 序列化工具 ─────────────────────────────────────────

    @staticmethod
    def _serialize(data: Any) -> tuple:
        """将数据转为 JSON 字符串 + 行数统计

        Supported input types:
          - pd.DataFrame → records JSON
          - dict → JSON
          - list → JSON
          - str → 原样返回
          - None → (None, 0)
        """
        if data is None:
            return None, 0
        if isinstance(data, str):
            # 已是 JSON 字符串, 验证有效性
            try:
                parsed = json.loads(data)
                return data, len(parsed) if isinstance(parsed, list) else 1
            except json.JSONDecodeError:
                return None, 0

        # 检测 pandas DataFrame
        cls_name = type(data).__name__
        if cls_name == "DataFrame":
            records = data.to_dict(orient="records")
            json_str = json.dumps(records, ensure_ascii=False, default=str)
            return json_str, len(records)

        json_str = json.dumps(data, ensure_ascii=False, default=str)
        row_count = len(data) if isinstance(data, (list, dict)) else 1
        return json_str, row_count

    @staticmethod
    def to_dataframe(data: Any) -> Any:
        """将本地缓存数据转回 DataFrame

        当存入的数据是 DataFrame 序列化而来时,
        调用此方法还原回 DataFrame 避免下游改动
        """
        if data is None:
            return None
        if isinstance(data, list) and len(data) > 0:
            import pandas as pd
            return pd.DataFrame(data)
        return data


# ============================================================
# 全局单例
# ============================================================
_global_store: Optional[DailyStore] = None


def get_store() -> DailyStore:
    """获取全局 DailyStore 单例"""
    global _global_store
    if _global_store is None:
        _global_store = DailyStore()
    return _global_store


def reset_store():
    """重置单例 (测试用)"""
    global _global_store
    _global_store = None
