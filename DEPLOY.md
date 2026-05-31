# 🏪 拾米交易工作室 · 部署指南

## 一、Docker 部署（推荐）

```bash
# 1. 复制环境变量
cp .env.example .env
# 编辑 .env，填入你的 Tushare Token
vim .env

# 2. 启动所有服务
docker compose up -d

# 3. 查看日志
docker compose logs -f

# 4. 停止
docker compose down
```

访问: http://localhost:80

## 二、本地开发部署

```bash
# 1. 安装依赖
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 2. 启动 PostgreSQL + Redis（可选，默认 SQLite + 内存缓存）
# 如使用 PG:  export SHIMI_DB_TYPE=postgresql
# 如使用 Redis: export SHIMI_USE_REDIS=true

# 3. 启动开发服务器
python3 backend.py

# 4. 或启动生产级服务
gunicorn wsgi:app -b 0.0.0.0:7890 -w 4
```

访问: http://localhost:7890

## 三、环境变量

| 变量 | 默认值 | 说明 |
|:----|:------|:----|
| `TUSHARE_TOKEN` | — | Tushare Pro Token（必填） |
| `SHIMI_DB_TYPE` | `sqlite` | 数据库类型: sqlite / postgresql |
| `SHIMI_DB_HOST` | `localhost` | PostgreSQL 地址 |
| `SHIMI_DB_NAME` | `shimi` | PostgreSQL 库名 |
| `SHIMI_DB_USER` | `shimi` | PostgreSQL 用户 |
| `SHIMI_DB_PASS` | `shimi_secret` | PostgreSQL 密码 |
| `SHIMI_USE_REDIS` | `true` | 是否启用 Redis 缓存 |
| `SHIMI_REDIS_HOST` | `localhost` | Redis 地址 |
| `SHIMI_PORT` | `7890` | 服务监听端口 |
| `SHIMI_DEBUG` | `false` | Flask 调试模式 |
| `SHIMI_TOKEN_HOURS` | `72` | JWT Token 有效期（小时） |
