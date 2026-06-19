"""
拾米交易工作室 - 启动入口
建筑师基础设施: 纯启动脚本，所有路由由蓝图管理

架构:
  app.py (工厂) → 蓝图注册 → api/ (8个) + haitao/ (1个)
  backend.py → 环境加载 + 基础设施 + 启动
"""
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

# ═══════════════════════════════════════════════
# 第0步: 加载环境变量 (必须在任何 import config 之前)
# ═══════════════════════════════════════════════
from dotenv import load_dotenv
load_dotenv()

import config
from logger import startup_log

# ─── 全局平台实例 ───────────────────────────
_platform = None


def get_platform():
    """获取 AStockPlatform 单例"""
    global _platform
    if _platform is None:
        from a_stock import AStockPlatform
        _platform = AStockPlatform()
    return _platform


def create_app():
    """创建并装配 Flask 应用 (纯组装，不含路由逻辑)"""
    import numpy as np
    from flask import Flask, jsonify, request
    from flask_cors import CORS
    from flask.json.provider import DefaultJSONProvider

    class NumpyJSONProvider(DefaultJSONProvider):
        def default(self, obj):
            if isinstance(obj, (np.integer,)):
                return int(obj)
            if isinstance(obj, (np.floating,)):
                return float(obj)
            if isinstance(obj, (np.bool_,)):
                return bool(obj)
            if isinstance(obj, (np.ndarray,)):
                return obj.tolist()
            return super().default(obj)

    app = Flask(__name__)
    app.json = NumpyJSONProvider(app)
    app.static_folder = os.path.dirname(os.path.abspath(__file__))
    app.static_url_path = ""
    CORS(app)

    # ─── 基础设施 ──────────────────────────
    from middleware import register_middleware
    register_middleware(app)
    startup_log("middleware", "ok")

    # ─── 确保默认用户存在 & 本地令牌就绪 ────
    def _ensure_default_user():
        """首次启动时确保有用户可登录，并生成本地令牌"""
        try:
            from db.core import get_db
            from db import list_users, register_user
            import secrets, time

            users = list_users()
            if not users:
                # 首次启动：创建默认用户
                u = register_user("admin", "admin", "本地用户")
                if "error" in u:
                    startup_log("auth", "warn", f"创建默认用户失败: {u}")
                    return
                # 直接用新用户id
                user_id = u["id"]
                username = "admin"
            else:
                user_id = users[0]["id"]
                username = users[0]["username"]

            # 生成或刷新本地令牌
            token_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".local_token")
            token = secrets.token_hex(32)
            expires = time.time() + 72 * 3600
            conn = get_db()
            conn.execute(
                "INSERT OR REPLACE INTO tokens (token, user_id, expires_at) VALUES (?, ?, ?)",
                (token, user_id, expires),
            )
            conn.commit()
            conn.close()
            with open(token_path, "w") as f:
                f.write(token)
            startup_log("auth", "ok", f"本地令牌已生成 (用户: {username})")
        except Exception as e:
            startup_log("auth", "warn", f"默认用户处理异常: {e}")

    _ensure_default_user()

    # ─── 蓝图注册 (所有路由的归宿) ────────
    from api import register_blueprints
    register_blueprints(app)
    startup_log("blueprints", "ok", "api/* + haitao/*")

    # ─── A股平台底座初始化 ─────────────────
    try:
        platform = get_platform()
        state = platform.init_all(run_db_init=True)

        # 汇报各子系统状态
        for name, sub in state.subsystems.items():
            startup_log(f"a_stock.{name}", sub.status, sub.detail)

        # 附加到 app 供端点使用
        app.extensions["a_stock_platform"] = platform
    except Exception as e:
        startup_log("a_stock_platform", "fail", str(e))

    # ─── 1W方案数据表初始化 ─────────────────
    try:
        from services.plan_1w import init_tables
        init_tables()
        startup_log("plan_1w", "ok", "plan_1w + plan_1w_pnl_log")
    except Exception as e:
        startup_log("plan_1w", "fail", str(e))

    # ─── API: 平台状态 ─────────────────────
    @app.route("/api/a-stock/status")
    def a_stock_status():
        platform = get_platform()
        return jsonify(platform.status_report())

    @app.route("/api/a-stock/health")
    def a_stock_health():
        platform = get_platform()
        return jsonify(platform.check_health())

    @app.route("/api/a-stock/version")
    def a_stock_version():
        from a_stock import __version__, __description__
        return jsonify({
            "version": __version__,
            "role": "拾米A股",
            "description": __description__,
        })

    # ─── API: 日级缓存管理 ──────────────────
    @app.route("/api/a-stock/cache/summary")
    def a_stock_cache_summary():
        platform = get_platform()
        return jsonify(platform.get_cache_summary())

    @app.route("/api/a-stock/cache/refresh", methods=["POST"])
    def a_stock_cache_refresh():
        platform = get_platform()
        import json as _json
        body = {}
        try:
            body = _json.loads(request.data) if request.data else {}
        except Exception:
            pass
        market = body.get("market", "all")
        trade_date = body.get("trade_date")
        result = platform.refresh_daily_cache(market=market, trade_date=trade_date)
        return jsonify({"status": "ok" if any(result.values()) else "noop", "result": result})

    @app.route("/api/a-stock/cache/invalidate", methods=["POST"])
    def a_stock_cache_invalidate():
        platform = get_platform()
        import json as _json
        body = {}
        try:
            body = _json.loads(request.data) if request.data else {}
        except Exception:
            pass
        data_type = body.get("data_type")
        deleted = platform.invalidate_cache(data_type=data_type)
        return jsonify({"status": "ok", "deleted": deleted})

    @app.route("/api/a-stock/cache/refresh-recent", methods=["POST"])
    def a_stock_cache_refresh_recent():
        platform = get_platform()
        import json as _json
        body = {}
        try:
            body = _json.loads(request.data) if request.data else {}
        except Exception:
            pass
        days = body.get("days_back", 5)
        result = platform.refresh_recent_cache(days_back=days)
        return jsonify({"status": "ok", "result": result})

    # ─── 根路由 ────────────────────────────
    @app.route("/")
    def index():
        return app.send_static_file("index.html")

    # ─── API 文档 ────────────────────────────
    @app.route("/docs")
    def api_docs():
        return app.send_static_file("docs/swagger.html")

    @app.route("/docs/api-spec.yaml")
    def api_spec():
        return app.send_static_file("docs/api-spec.yaml")

    return app


# ═══════════════════════════════════════════════
# 启动
# ═══════════════════════════════════════════════
if __name__ == "__main__":
    import signal as _signal

    app = create_app()
    platform = get_platform()
    startup_log("app", "ok", "AStock平台已就绪")

    # 信号处理：确保 SIGTERM 时干净退出、释放端口
    def _graceful_shutdown(signum, frame):
        startup_log("app", "info", f"收到信号 {signum}，正在退出...")
        # Flask dev server 在 signal handler 中 sys.exit 即可
        import sys as _sys
        _sys.exit(0)

    _signal.signal(_signal.SIGTERM, _graceful_shutdown)
    _signal.signal(_signal.SIGINT, _graceful_shutdown)

    print("🚀 拾米交易工作室 Backend 启动中...")
    print(f"   🏛️  http://localhost:{config.SERVER_PORT}")
    print(f"   📋 日志: logs/shimi.log")
    print(f"   ❌ 错误: logs/error.log")
    print(f"   🔍 依赖: /api/health/deps")
    print(f"   📊 A股平台: /api/a-stock/status")
    print(f"   💰 融资融券: /api/margin?code=000001")
    print(f"   🔑 自动登录: 已启用 (本地令牌)")
    print(f"   💓 健康检查: /api/a-stock/health")

    app.run(
        host=config.SERVER_HOST,
        port=config.SERVER_PORT,
        debug=config.DEBUG,
    )
