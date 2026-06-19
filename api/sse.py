"""
拾米交易工作室 - SSE 实时推送蓝图
/api/sse/stream — Server-Sent Events 端点

客户端:
  const es = new EventSource('/api/sse/stream');
  es.onmessage = e => { const d = JSON.parse(e.data); /* d.type, d.data */ };
"""
from flask import Blueprint, Response
import json
import time
import queue
import threading

bp = Blueprint("sse", __name__, url_prefix="/api/sse")

# 全局消息通道: 所有SSE客户端共用一个队列
_sse_clients = []


def broadcast(event_type: str, data: dict):
    """向所有SSE客户端广播消息 (由其他模块调用)

    Usage:
        from api.sse import broadcast
        broadcast("alert", {"message": "xxx", "level": "warning"})
    """
    for q in _sse_clients:
        try:
            q.put_nowait(json.dumps({"type": event_type, "data": data, "time": int(time.time())}))
        except queue.Full:
            pass


@bp.route("/stream")
def sse_stream():
    """SSE 实时流端点"""
    def generate():
        q = queue.Queue(maxsize=50)
        _sse_clients.append(q)

        # 首条消息: 连接成功
        yield f"data: {json.dumps({'type': 'connected', 'time': int(time.time())})}\n\n"

        try:
            while True:
                try:
                    msg = q.get(timeout=15)
                    yield f"data: {msg}\n\n"
                except queue.Empty:
                    # 心跳
                    yield f"data: {json.dumps({'type': 'heartbeat', 'time': int(time.time())})}\n\n"
        except GeneratorExit:
            pass
        finally:
            _sse_clients.remove(q)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
