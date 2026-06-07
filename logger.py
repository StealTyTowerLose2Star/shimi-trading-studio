"""
拾米交易工作室 - 统一日志模块 (Logger)
建筑师基础设施: 所有模块的日志入口

功能:
  - 控制台 + 文件双输出
  - 时间戳 / 级别 / 模块来源 / 行号
  - 自动轮转 (10MB x 3)
  - 请求追踪 ID (request_id)
"""

import logging
import logging.handlers
import os
import sys
import time
from datetime import datetime
from contextvars import ContextVar

# ─── 请求级追踪 ID ─────────────────────────────────
_request_id: ContextVar[str] = ContextVar("request_id", default="-")


def set_request_id(rid: str):
    """设置当前请求的追踪 ID（由中间件调用）"""
    _request_id.set(rid)


def get_request_id() -> str:
    return _request_id.get()


# ─── 配置 ──────────────────────────────────────────
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
MAX_BYTES = 10 * 1024 * 1024  # 10MB
BACKUP_COUNT = 3
CONSOLE_LEVEL = logging.DEBUG
FILE_LEVEL = logging.INFO

os.makedirs(LOG_DIR, exist_ok=True)


class RequestFormatter(logging.Formatter):
    """自定义格式化器: 注入 request_id 和模块来源"""

    def format(self, record):
        record.request_id = get_request_id()
        return super().format(record)


# ─── 根 Logger 创建 ───────────────────────────────
_logger = logging.getLogger("shimi")
_logger.setLevel(logging.DEBUG)
_logger.propagate = False

# 控制台 Handler
_console = logging.StreamHandler(sys.stdout)
_console.setLevel(CONSOLE_LEVEL)
_console.setFormatter(RequestFormatter(
    fmt="%(asctime)s | %(levelname)-5s | [%(request_id)s] | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
))
_logger.addHandler(_console)

# 文件 Handler (按日期 + 大小轮转)
_file = logging.handlers.RotatingFileHandler(
    os.path.join(LOG_DIR, "shimi.log"),
    maxBytes=MAX_BYTES,
    backupCount=BACKUP_COUNT,
    encoding="utf-8",
)
_file.setLevel(FILE_LEVEL)
_file.setFormatter(RequestFormatter(
    fmt="%(asctime)s | %(levelname)-5s | [%(request_id)s] | %(name)s | %(pathname)s:%(lineno)d | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
))
_logger.addHandler(_file)

# 错误日志单独文件
_error_file = logging.handlers.RotatingFileHandler(
    os.path.join(LOG_DIR, "error.log"),
    maxBytes=MAX_BYTES,
    backupCount=BACKUP_COUNT,
    encoding="utf-8",
)
_error_file.setLevel(logging.ERROR)
_error_file.setFormatter(RequestFormatter(
    fmt="%(asctime)s | %(levelname)s | [%(request_id)s] | %(name)s | %(pathname)s:%(lineno)d | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
))
_logger.addHandler(_error_file)


# ─── 便捷接口 ──────────────────────────────────────


def get_logger(name: str = "shimi") -> logging.Logger:
    """获取指定模块的 logger

    Usage:
        from logger import get_logger
        log = get_logger(__name__)
        log.info("策略扫描完成, 耗时 %.2fs", elapsed)
    """
    return logging.getLogger(name)


def startup_log(component: str, status: str, detail: str = ""):
    """启动阶段日志 (带 emoji 标记)

    Args:
        component: 组件名 (e.g. "cache", "db", "haitao")
        status: "ok" / "warn" / "fail"
        detail: 补充信息
    """
    log = get_logger("shimi.startup")
    icons = {"ok": "✅", "warn": "⚠️", "fail": "❌"}
    icon = icons.get(status, "▪️")
    msg = f"{icon} {component}"
    if detail:
        msg += f" | {detail}"
    if status == "ok":
        log.info(msg)
    elif status == "warn":
        log.warning(msg)
    else:
        log.error(msg)


def request_log(method: str, path: str, status: int, duration_ms: float):
    """请求日志 (由中间件自动调用)

    Args:
        method: HTTP method
        path: 请求路径
        status: HTTP 状态码
        duration_ms: 耗时 (毫秒)
    """
    log = get_logger("shimi.request")
    level = logging.ERROR if status >= 500 else (logging.WARNING if status >= 400 else logging.INFO)
    log.log(level, "%s %s → %d (%.1fms)", method, path, status, duration_ms)


def dep_check_log(service: str, reachable: bool, latency_ms: float = 0):
    """外部依赖检查日志

    Args:
        service: 服务名 (e.g. "tushare", "finnhub")
        reachable: 是否可达
        latency_ms: 延迟 (毫秒)
    """
    log = get_logger("shimi.deps")
    if reachable:
        log.info("%s 可达 (%.0fms)", service, latency_ms)
    else:
        log.error("%s 不可达", service)


# ─── 初始化日志 ────────────────────────────────────
startup_log("logger", "ok", f"日志目录: {LOG_DIR}")
