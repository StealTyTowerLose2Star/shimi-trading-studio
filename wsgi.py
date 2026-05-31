"""
Gunicorn WSGI 入口
启动: cd /root/shi-mi-dashboard && source venv/bin/activate && gunicorn wsgi:app -b 0.0.0.0:7890 -w 4
"""
import os
import sys

# 确保项目目录在路径中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 环境变量默认值
os.environ.setdefault("SHIMI_DB_TYPE", "postgresql")
os.environ.setdefault("SHIMI_USE_REDIS", "true")

from backend import app as application

if __name__ == "__main__":
    application.run()
