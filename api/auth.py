"""
拾米交易工作室 - 认证辅助（向后兼容）
api/auth.py → shared/auth.py
"""
# 从共享模块重导出，保持 from api.auth import X 向后兼容
from shared.auth import require_user, unauthorized
