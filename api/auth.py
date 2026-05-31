"""
拾米交易工作室 - 认证辅助（API 蓝图共享）
"""
from flask import request, jsonify
from db import verify_token


def require_user():
    """从请求头获取当前用户"""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
        user = verify_token(token)
        if user:
            return user
    return None


def unauthorized():
    return jsonify({"error": "未登录或登录已过期"}), 401
