# 拾米交易工作室 · ShiMi Trading Studio

> **全栈量化交易管理平台 | 策略总控 · 持仓管理 · 实时评分 · 服务器监控**

将三个独立策略仓库（[趋势跟踪](https://github.com/StealTyTowerLose2Star/a-share-trend-strategy)、[混合趋势](https://github.com/StealTyTowerLose2Star/a-share-hybrid-strategy)、[龙头战法](https://github.com/StealTyTowerLose2Star/a-share-dragon-strategy)）整合到统一的可视化控制面板。Flask 后端 + 单页前端，实现策略评分可视化、持仓动态管理、市场情绪监控的一站式体验。

---

## 🏗️ 系统架构

### 三层模块解耦

```
┌────────────────────────────────────────────────────────────────┐
│                      app.py (应用工厂)                           │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  api/ (Flask Blueprints — 路由层)                         │  │
│  │  ┌─────────┐ ┌──────────┐ ┌─────────┐ ┌───────┐ ┌────┐ │  │
│  │  │ market  │ │ strategy │ │ advice  │ │ trade │ │mon │ │  │
│  │  └────┬────┘ └────┬─────┘ └────┬────┘ └───┬───┘ └──┬─┘ │  │
│  └───────┼───────────┼────────────┼──────────┼────────┼──────┘  │
│          ▼           ▼            ▼          ▼        ▼        │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  services/ (业务层 — 纯逻辑，不依赖 Flask)                  │  │
│  │  ┌───────────────┐  ┌───────────────────┐                 │  │
│  │  │ strategy.py   │  │ advice.py         │                 │  │
│  │  │ 策略扫描编排    │  │ 操作建议引擎       │                 │  │
│  │  └───────┬───────┘  └────────┬──────────┘                 │  │
│  └──────────┼───────────────────┼────────────────────────────┘  │
│             ▼                   ▼                               │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  data/ (数据层 — 只负责数据抓取)                            │  │
│  │  ┌───────────────┐                                        │  │
│  │  │ fetcher.py    │  ← 15 个 tushare 数据函数              │  │
│  │  └───────┬───────┘                                        │  │
│  └──────────┼────────────────────────────────────────────────┘  │
│             ▼                                                   │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  config / cache / db / monitor / realtime_scorer         │  │
│  └──────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────┘
```

### 依赖方向（单向，无循环）

```
app.py → api/ → services/ → data/ → config / cache / db
```

**修改保障**：
- 改 `data/fetcher.py` → 只影响数据格式，不影响业务逻辑
- 改 `services/advice.py` → 只影响建议逻辑，不影响 API 路由
- 改 `api/trade.py` → 只影响交易接口，不影响策略评分
- 新增数据源（如 AKShare）→ 只需加新 fetcher 函数，零改动业务层

### 生产部署架构

```
                    公网 IP
                       │ :80
                ┌──────┴──────┐
                │   Nginx      │  ← 反向代理 + 静态缓存
                └──────┬──────┘
                       │ 127.0.0.1:7890
                ┌──────┴──────┐
                │  Gunicorn    │  ← 4 workers 并发
                ├─────────────┤
                │ PostgreSQL  │  ← 持久化存储
                ├─────────────┤
                │   Redis     │  ← 持久缓存
                └─────────────┘
```

---

## 🚀 部署方式

### 方式一：Docker 部署（推荐）

```bash
# 1. 克隆仓库
git clone https://github.com/StealTyTowerLose2Star/shimi-trading-studio.git
cd shimi-trading-studio

# 2. 配置环境变量
cp .env.example .env
vim .env   # 填入你的 TUSHARE_TOKEN

# 3. 一键启动
docker compose up -d

# 4. 查看日志
docker compose logs -f

# 访问 http://localhost:80
```

#### 依赖的 Docker 镜像

| 镜像 | 版本 | 说明 |
|:----|:----|:-----|
| `postgres:16-alpine` | 16 | 数据库 |
| `redis:7-alpine` | 7 | 缓存 |
| `nginx:1.24-alpine` | 1.24 | 反向代理 |
| `python:3.12-slim` | 3.12 | 应用（自构建） |

#### 服务端口

| 端口 | 服务 | 外部可访问 |
|:---:|:----|:---------:|
| `:80` | Nginx (Web) | ✅ |
| `:5432` | PostgreSQL | ❌ (仅内部) |
| `:6379` | Redis | ❌ (仅内部) |
| `:7890` | Gunicorn | ❌ (仅内部) |

### 方式二：本地开发部署

#### 前置条件
- Python 3.10+
- Tushare Pro Token（免费注册 [tushare.pro](https://tushare.pro)）
- PostgreSQL + Redis（可选，默认 SQLite + 内存缓存）

#### 安装与启动

```bash
# 1. 克隆
git clone https://github.com/StealTyTowerLose2Star/shimi-trading-studio.git
cd shimi-trading-studio

# 2. 创建虚拟环境
python3 -m venv venv
source venv/bin/activate

# 3. 安装依赖
pip install -r requirements.txt

# 4. 设置环境变量
export TUSHARE_TOKEN=your_token_here

# 5. 启动（开发模式，SQLite + 内存缓存）
python3 backend.py

# 或启动（生产模式，PostgreSQL + Redis）
export SHIMI_DB_TYPE=postgresql
export SHIMI_USE_REDIS=true
gunicorn wsgi:app -b 127.0.0.1:7890 -w 4
```

访问:
- **控制面板**: http://localhost:7890
- **API 聚合数据**: http://localhost:7890/api/dashboard
- **服务器监控**: http://localhost:7890/api/monitor

---

## ⚙️ 环境变量

| 变量 | 默认值 | 说明 |
|:----|:------|:----|
| `TUSHARE_TOKEN` | — | **Tushare Pro Token（必填）** |
| `SHIMI_DB_TYPE` | `postgresql` | 数据库类型: `sqlite` / `postgresql` |
| `SHIMI_DB_HOST` | `localhost` | PostgreSQL 地址 |
| `SHIMI_DB_PORT` | `5432` | PostgreSQL 端口 |
| `SHIMI_DB_NAME` | `shimi` | PostgreSQL 数据库名 |
| `SHIMI_DB_USER` | `shimi` | PostgreSQL 用户名 |
| `SHIMI_DB_PASS` | `shimi_secret` | PostgreSQL 密码 |
| `SHIMI_USE_REDIS` | `true` | 启用 Redis 缓存: `true` / `false` |
| `SHIMI_REDIS_HOST` | `localhost` | Redis 地址 |
| `SHIMI_REDIS_PORT` | `6379` | Redis 端口 |
| `SHIMI_REDIS_DB` | `0` | Redis 数据库编号 |
| `SHIMI_HOST` | `0.0.0.0` | 监听地址 |
| `SHIMI_PORT` | `7890` | 监听端口 |
| `SHIMI_DEBUG` | `false` | Flask 调试模式 |
| `SHIMI_TOKEN_HOURS` | `72` | JWT Token 有效期（小时） |

---

## 📁 项目结构

```
shimi-trading-studio/
│
├── app.py                  ← 应用工厂（组装所有模块）
├── wsgi.py                 ← Gunicorn 入口
├── backend.py              ← 旧版入口（向后兼容）
│
├── config.py               ← 配置管理
├── cache.py                ← 缓存引擎（Redis/内存）
├── db.py                   ← 数据库（SQLite/PostgreSQL）
├── monitor.py              ← 服务器监控
├── position_manager.py     ← 持仓管理引擎（ATR动态风控）
├── realtime_scorer.py      ← 三大策略评分引擎
│
├── data/
│   └── fetcher.py          ← 数据层：15个tushare数据函数
│
├── services/
│   ├── strategy.py         ← 业务层：策略扫描编排
│   └── advice.py           ← 业务层：操作建议引擎
│
├── api/
│   ├── __init__.py         ← 蓝图注册
│   ├── auth.py             ← 认证辅助
│   ├── market.py           ← 市场数据路由
│   ├── strategy.py         ← 策略评分路由
│   ├── advice.py           ← 操作建议路由
│   ├── trade.py            ← 账户交易路由
│   └── monitor.py          ← 服务器监控路由
│
├── index.html              ← 单页前端（暗色主题SPA）
│
├── Dockerfile              ← Docker 镜像构建
├── docker-compose.yml      ← Docker Compose 编排
├── nginx/default.conf      ← Nginx 配置
├── requirements.txt        ← Python 依赖
├── .env.example            ← 环境变量模板
│
├── DEPLOY.md               ← 详细部署文档
└── README.md               ← 本文件
```

---

## 🔌 API 接口文档

### 市场数据

| 接口 | 方法 | 说明 | 缓存 |
|:----|:----|:----|:---:|
| `/api/health` | GET | 健康检查 + 最新交易日 | — |
| `/api/indices` | GET | 上证/深证/创业板/科创50 | 30s |
| `/api/sectors` | GET | 行业板块排行 | 120s |
| `/api/sector-flow` | GET | 板块强度排行 | 60s |
| `/api/hot-stocks` | GET | 热门股票 | 30s |
| `/api/limit-up` | GET | 涨停板 | 60s |
| `/api/sentiment` | GET | 市场情绪指标 | 30s |
| `/api/stock/lookup` | GET | 股票代码→名称查询 | — |
| `/api/dashboard` | GET | 聚合所有数据 | 混合 |

### 策略评分

| 接口 | 方法 | 说明 | 缓存 |
|:----|:----|:----|:---:|
| `/api/strategy/trend` | GET | 趋势跟踪评分 | 120s |
| `/api/strategy/hybrid` | GET | 混合策略评分 | 120s |
| `/api/strategy/dragon` | GET | 龙头战法评分 | 120s |
| `/api/strategy/<name>/refresh` | GET | 强制刷新策略缓存 | — |

### 操作建议

| 接口 | 方法 | 说明 |
|:----|:----|:-----|
| `/api/advice` | GET | 综合交易建议 |
| `/api/positions/evaluate` | POST | 批量评估持仓（止损/目标） |
| `/api/portfolio/advice` | GET | 持仓组合分析（需登录） |

### 账户与交易

| 接口 | 方法 | 说明 |
|:----|:----|:-----|
| `/api/auth/login` | POST | 用户登录 |
| `/api/auth/register` | POST | 用户注册 |
| `/api/auth/me` | GET | 当前用户信息 |
| `/api/users` | GET | 用户列表 |
| `/api/trades` | GET | 获取交易记录 |
| `/api/trades` | POST | 新增交易 |
| `/api/trades/<id>` | PUT | 更新交易 |
| `/api/trades/<id>` | DELETE | 删除交易 |
| `/api/trades/pnl-report` | GET | 盈亏日历统计 |

### 服务器监控

| 接口 | 方法 | 说明 |
|:----|:----|:-----|
| `/api/monitor` | GET | CPU/内存/磁盘/负载/告警 |

---

## 🧠 三大策略引擎

| 策略 | 来源仓库 | 评分维度 |
|:----|:--------|:--------|
| 📈 **趋势跟踪** | [a-share-trend-strategy](https://github.com/StealTyTowerLose2Star/a-share-trend-strategy) | MA30% + 突破25% + 量能20% + 斜率25% |
| 🔀 **混合策略** | [a-share-hybrid-strategy](https://github.com/StealTyTowerLose2Star/a-share-hybrid-strategy) | 7维: 趋势+动量+量能+安全+板块+持续+爆发 |
| 🐉 **龙头战法** | [a-share-dragon-strategy](https://github.com/StealTyTowerLose2Star/a-share-dragon-strategy) | 5维: 板块+连板+时间+带动+阻力 |

---

## ⚠️ 免责声明

1. **本系统仅供学习和研究使用**
2. 所有策略评分基于历史数据，**不构成投资建议**
3. Tushare 免费数据源可能存在延迟，请勿直接用于实盘交易
4. 过往表现不代表未来收益，实盘前请充分测试
5. **股市有风险，投资需谨慎**

---

## 📄 许可证

MIT License

---

**版本**: v2.0 · **模块化重构** · **状态**: 迭代中
