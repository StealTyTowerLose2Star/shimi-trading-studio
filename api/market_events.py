"""
拾米交易工作室 - 市场事件 API 蓝图
url_prefix: /api
"""
import os
import json
import threading
from flask import Blueprint, jsonify, request

bp = Blueprint("market_events", __name__, url_prefix="/api")

CACHE_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "market_events.json")
_scan_lock = threading.Lock()
_scanning = False


def _load_cache():
    """从缓存文件读取 (毫秒级)"""
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return None


def _save_cache(data):
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with open(CACHE_FILE, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


@bp.route("/market/events")
def api_market_events():
    """获取当前市场事件 (优先缓存, 毫秒级响应)"""
    data = _load_cache()
    if data:
        data["source"] = "cache"
        return jsonify(data)

    # 无缓存时触发后台扫描, 返回空
    _start_bg_scan()
    return jsonify({
        "source": "scanning",
        "message": "后台扫描中, 请稍后刷新",
        "events": [],
        "signals": [],
        "summary": {"total_events": 0, "long_signals": 0, "short_signals": 0},
    })


@bp.route("/market/events/refresh", methods=["POST"])
def api_market_events_refresh():
    """触发后台扫描 (非阻塞)"""
    _start_bg_scan()
    return jsonify({"status": "started", "message": "后台扫描已启动, 约30秒后缓存更新"})


def _start_bg_scan():
    global _scanning
    if _scanning:
        return
    _scanning = True

    def _scan():
        global _scanning
        try:
            from data.market_events import scan_market_events
            result = scan_market_events()
            _save_cache(result)
        except Exception as e:
            print(f"[market_events] 后台扫描失败: {e}")
        finally:
            _scanning = False

    t = threading.Thread(target=_scan, daemon=True)
    t.start()
