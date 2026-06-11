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
        # 安全检查: 拒绝字面量 "local_token" 字符串 — 曾为安全漏洞
        # 请使用 .local_token 文件中的真实 token 值
        if token == "local_token":
            return None
        user = verify_token(token)
        if user:
            return user
    return None


def unauthorized():
    return jsonify({"error": "未登录或登录已过期"}), 401
