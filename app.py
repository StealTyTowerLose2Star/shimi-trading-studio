"""
拾米交易工作室 - Flask 应用工厂
建筑师基础设施: 将数据层/业务层/路由层组装在一起

注册顺序:
  1. 基础设施 (logger, middleware)
  2. 蓝图 (api/* + haitao/*)
  3. 静态文件 (index.html)
"""
import os
import numpy as np
from flask import Flask
from flask_cors import CORS
from flask.json.provider import DefaultJSONProvider

# 自动加载 .env 文件
from dotenv import load_dotenv
load_dotenv()

import config


class NumpyJSONProvider(DefaultJSONProvider):
    """NumPy 类型自动序列化为 JSON"""
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


def create_app() -> Flask:
    """创建并配置 Flask 应用"""
    app = Flask(__name__)
    app.json = NumpyJSONProvider(app)
    app.static_folder = os.path.dirname(os.path.abspath(__file__))
    app.static_url_path = ""
    CORS(app)

    # ─── 基础设施注册 ────────────────────────────────
    from middleware import register_middleware
    register_middleware(app)

    # ─── 蓝图注册 ────────────────────────────────────
    from api import register_blueprints
    register_blueprints(app)

    # ─── 根路由 ──────────────────────────────────────
    @app.route("/")
    def index():
        return app.send_static_file("index.html")

    return app
