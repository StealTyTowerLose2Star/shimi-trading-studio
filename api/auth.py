"""
拾米交易工作室 - 认证辅助（API 蓝图共享）
"""
from flask import request, jsonify
from db import verify_token
import os


def require_user():
    """从请求头获取当前用户"""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
        # 本地自动登录: 尝试读取 .local_token 文件
        if token == "local_token":
            return _auto_local_user()
        user = verify_token(token)
        if user:
            return user
    return None


def unauthorized():
    return jsonify({"error": "未登录或登录已过期"}), 401


def _auto_local_user():
    """尝试使用 .local_token 文件自动登录"""
    token_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        ".local_token"
    )
    if os.path.exists(token_path):
        with open(token_path) as f:
            token = f.read().strip()
        if token:
            return verify_token(token)
    return None
