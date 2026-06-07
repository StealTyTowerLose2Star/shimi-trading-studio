"""
拾米交易工作室 - 中间件模块 (Middleware)
建筑师基础设施: Flask 错误处理 + 请求超时 + 追踪 ID + 日志

注册方式:
    from middleware import register_middleware
    register_middleware(app)
"""

import time
import uuid
import traceback
import signal
from contextlib import contextmanager
from flask import Flask, request, jsonify, g

from logger import (
    set_request_id, get_request_id,
    request_log, get_logger,
)

log = get_logger(__name__)


# ─── 超时配置 ──────────────────────────────────────
DEFAULT_TIMEOUT = 30          # 默认请求超时 (秒)
LONG_RUNNING_PATHS = {        # 长耗时端点
    "/api/dashboard": 60,
    "/api/doubler/recommend": 45,
    "/api/doubler/recommend/refresh": 45,
    "/api/us/dashboard": 45,
    "/api/us/scan": 45,
}
QUICK_PATHS = {               # 快速端点
    "/api/health": 5,
    "/api/monitor": 5,
}


def _get_timeout() -> int:
    """根据请求路径返回超时时间"""
    path = request.path
    if path in LONG_RUNNING_PATHS:
        return LONG_RUNNING_PATHS[path]
    if path in QUICK_PATHS:
        return QUICK_PATHS[path]
    return DEFAULT_TIMEOUT


@contextmanager
def timeout_context(seconds: int):
    """超时上下文管理器 (基于 signal.SIGALRM)

    注意: 仅在主线程有效，gunicorn worker 兼容
    """
    def _timeout_handler(signum, frame):
        raise TimeoutError(f"请求超时 ({seconds}s)")

    old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)


# ─── 请求追踪中间件 ────────────────────────────────

def _before_request():
    """每个请求前: 设置追踪 ID 和计时器"""
    rid = request.headers.get("X-Request-ID", str(uuid.uuid4())[:8])
    set_request_id(rid)
    g.start_time = time.time()
    g.request_id = rid


def _after_request(response):
    """每个请求后: 记录请求日志"""
    duration_ms = (time.time() - g.get("start_time", time.time())) * 1000
    request_log(
        method=request.method,
        path=request.path,
        status=response.status_code,
        duration_ms=duration_ms,
    )
    # 在响应头中返回追踪 ID
    response.headers["X-Request-ID"] = g.get("request_id", "-")
    response.headers["X-Response-Time-Ms"] = f"{duration_ms:.0f}"
    return response


# ─── 错误处理 ──────────────────────────────────────

def _handle_400(error):
    """400 错误统一响应"""
    log.warning("400 Bad Request: %s", str(error))
    return jsonify({
        "error": "bad_request",
        "message": str(error) or "请求格式错误",
        "request_id": get_request_id(),
    }), 400


def _handle_404(error):
    """404 错误统一响应"""
    return jsonify({
        "error": "not_found",
        "message": f"端点不存在: {request.path}",
        "request_id": get_request_id(),
    }), 404


def _handle_405(error):
    """405 方法不允许"""
    return jsonify({
        "error": "method_not_allowed",
        "message": f"方法 {request.method} 不允许访问 {request.path}",
        "request_id": get_request_id(),
    }), 405


def _handle_500(error):
    """500 内部错误统一响应 (含 traceback 日志)"""
    tb = traceback.format_exc()
    log.error("500 Internal Error on %s %s\n%s", request.method, request.path, tb)
    return jsonify({
        "error": "internal_error",
        "message": "服务器内部错误，已记录日志",
        "request_id": get_request_id(),
    }), 500


def _handle_timeout(error):
    """请求超时响应"""
    timeout = _get_timeout()
    log.error("请求超时 (%ds): %s %s", timeout, request.method, request.path)
    return jsonify({
        "error": "timeout",
        "message": f"请求处理超时 ({timeout}s)，请稍后重试或缩小查询范围",
        "request_id": get_request_id(),
        "hint": "可尝试使用缓存数据 (/api/dashboard 已缓存)",
    }), 504


def _handle_generic(error):
    """通用异常捕获"""
    tb = traceback.format_exc()
    log.error("未捕获异常: %s\n%s", str(error), tb)
    return jsonify({
        "error": "internal_error",
        "message": str(error)[:200],
        "request_id": get_request_id(),
    }), 500


# ─── 注册入口 ──────────────────────────────────────

def register_middleware(app: Flask):
    """在 Flask 应用上注册所有中间件

    Usage:
        from middleware import register_middleware
        register_middleware(app)
    """
    # 请求追踪
    app.before_request(_before_request)
    app.after_request(_after_request)

    # HTTP 错误
    app.register_error_handler(400, _handle_400)
    app.register_error_handler(404, _handle_404)
    app.register_error_handler(405, _handle_405)
    app.register_error_handler(500, _handle_500)

    # 超时
    app.register_error_handler(TimeoutError, _handle_timeout)

    # 通用兜底
    app.register_error_handler(Exception, _handle_generic)

    log.info("中间件注册完成: 追踪ID | 请求日志 | 错误处理 | 超时控制")
