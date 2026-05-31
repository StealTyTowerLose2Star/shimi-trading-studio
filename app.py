"""
拾米交易工作室 - Flask 应用工厂
将数据层(data/)、业务层(services/)、路由层(api/) 组装在一起
"""
import os
import numpy as np
from flask import Flask
from flask_cors import CORS
from flask.json.provider import DefaultJSONProvider

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

    # 注册所有蓝图
    from api import register_blueprints
    register_blueprints(app)

    # 根路由（前端页面）
    @app.route("/")
    def index():
        return app.send_static_file("index.html")

    return app
