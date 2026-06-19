"""
拾米交易工作室 - WebSocket 蓝图
实时推送行情数据变更

事件:
  connect     → 客户端连接
  disconnect  → 客户端断开  
  market_ping → 定时心跳
  data_update → 数据变更推送 (由其他模块触发)
"""

import json
import os
import threading
import time
from datetime import datetime

from monitor import check_external_deps

bp = None  # 延迟初始化，由 register_socketio 设置

# 全局状态
_connected_clients = 0
_last_health = {}
_watch_interval = 30  # 监控检查间隔(秒)


def register_socketio(app):
    """注册 Socket.IO 到 Flask 应用"""
    global bp

    from flask import Blueprint
    
    # 使用 Flask-SocketIO 包装
    try:
        from flask_socketio import SocketIO, emit

        socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

        @socketio.on("connect")
        def handle_connect():
            global _connected_clients
            _connected_clients += 1

        @socketio.on("disconnect")
        def handle_disconnect():
            global _connected_clients
            _connected_clients = max(0, _connected_clients - 1)

        @socketio.on("market_ping")
        def handle_ping():
            """心跳 + 返回当前状态"""
            deps = _last_health or check_external_deps()
            emit("market_pong", {
                "timestamp": datetime.now().strftime("%H:%M:%S"),
                "deps": deps,
                "clients": _connected_clients,
            })

        # 后台监控线程
        def _watch_loop():
            global _last_health
            while True:
                try:
                    _last_health = check_external_deps()
                    # 推送状态变化
                    if _connected_clients > 0:
                        socketio.emit("health_update", {
                            "deps": _last_health,
                            "timestamp": datetime.now().strftime("%H:%M:%S"),
                        })
                except Exception:
                    pass
                time.sleep(_watch_interval)

        monitor_thread = threading.Thread(target=_watch_loop, daemon=True)
        monitor_thread.start()

        return socketio

    except ImportError:
        print("⚠️ flask-socketio 未安装, WebSocket 不可用")
        return None
    except Exception as e:
        print(f"⚠️ WebSocket 初始化失败: {e}")
        return None
