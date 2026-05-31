"""
Gunicorn WSGI 入口
使用新的模块化 app.py（数据/服务/路由三层解耦）

启动: cd /root/shi-mi-dashboard && source venv/bin/activate
      gunicorn wsgi:app -b 127.0.0.1:7890 -w 4

环境变量:
  SHIMI_DB_TYPE=postgresql   (或 sqlite)
  SHIMI_USE_REDIS=true       (或 false)
  TUSHARE_TOKEN=xxx
"""
import os
import sys

# 确保项目目录在路径中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 环境变量默认值
os.environ.setdefault("SHIMI_DB_TYPE", "postgresql")
os.environ.setdefault("SHIMI_USE_REDIS", "true")
os.environ.setdefault("SHIMI_DEBUG", "false")

# 从 app.py 工厂创建应用
from app import create_app
app = create_app()

if __name__ == "__main__":
    import config
    print("🚀 拾米交易工作室 Backend (模块化) 启动中...")
    print(f"   Dashboard: http://localhost:{config.SERVER_PORT}")
    print(f"   API:       http://localhost:{config.SERVER_PORT}/api/dashboard")
    app.run(host="127.0.0.1", port=config.SERVER_PORT, debug=config.DEBUG)
