"""
拾米交易工作室 - 配置模块
所有敏感信息/环境变量统一管理
"""
import os

# ─── 环境变量（生产时通过 .env 或 Docker 注入）───

# Tushare Pro Token
TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN", "b5e768c112082f5a38f3400244859d3f0ef9d917296600068d6cbf49")

# 数据库
DB_TYPE = os.getenv("SHIMI_DB_TYPE", "sqlite")  # sqlite | postgresql
DB_PATH = os.getenv("SHIMI_DB_PATH", os.path.join(os.path.dirname(__file__), "shimi.db"))
DB_HOST = os.getenv("SHIMI_DB_HOST", "localhost")
DB_PORT = int(os.getenv("SHIMI_DB_PORT", "5432"))
DB_NAME = os.getenv("SHIMI_DB_NAME", "shimi")
DB_USER = os.getenv("SHIMI_DB_USER", "shimi")
DB_PASS = os.getenv("SHIMI_DB_PASS", "shimi_secret")

# Redis
REDIS_HOST = os.getenv("SHIMI_REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("SHIMI_REDIS_PORT", "6379"))
REDIS_DB = int(os.getenv("SHIMI_REDIS_DB", "0"))
USE_REDIS = os.getenv("SHIMI_USE_REDIS", "false").lower() == "true"

# 服务
SERVER_HOST = os.getenv("SHIMI_HOST", "0.0.0.0")
SERVER_PORT = int(os.getenv("SHIMI_PORT", "7890"))
DEBUG = os.getenv("SHIMI_DEBUG", "false").lower() == "true"

# Token 有效期（小时）
TOKEN_EXPIRY_HOURS = int(os.getenv("SHIMI_TOKEN_HOURS", "72"))

# 缓存默认 TTL（秒）
CACHE_TTL_DEFAULT = 60

# 项目根目录
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))


def validate():
    """启动时校验关键配置"""
    if not TUSHARE_TOKEN or TUSHARE_TOKEN == "your_token_here":
        print("⚠️  警告: TUSHARE_TOKEN 未配置，数据接口将不可用")
        print("    请在 config.py 或环境变量 TUSHARE_TOKEN 中设置")
