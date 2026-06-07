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


def create_app():
    """创建并装配 Flask 应用 (纯组装，不含路由逻辑)"""
    import numpy as np
    from flask import Flask
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

    # ─── 蓝图注册 (所有路由的归宿) ────────
    from api import register_blueprints
    register_blueprints(app)
    startup_log("blueprints", "ok", "api/* + haitao/*")

    # ─── 根路由 ────────────────────────────
    @app.route("/")
    def index():
        return app.send_static_file("index.html")

    return app


# ═══════════════════════════════════════════════
# 启动
# ═══════════════════════════════════════════════
if __name__ == "__main__":
    app = create_app()
    startup_log("app", "ok", f"蓝图已装载")

    print("🚀 拾米交易工作室 Backend 启动中...")
    print(f"   🏛️  http://localhost:{config.SERVER_PORT}")
    print(f"   📋 日志: logs/shimi.log")
    print(f"   ❌ 错误: logs/error.log")
    print(f"   🔍 依赖: /api/health/deps")

    app.run(
        host=config.SERVER_HOST,
        port=config.SERVER_PORT,
        debug=config.DEBUG,
    )
