# 拾米交易工作室 · ShiMi Trading Studio

> A股 + 美股 双市场量化交易系统 | 7 角色松耦合架构 | 18 蓝图 | 独立Agent cron
>
> **v1.5.0** | 安全加固 | 2026-06-19

## 架构

```
🏛️ 建筑师 (Architect)      框架/监控/解耦/变更审计
🍚 拾米A股 (ShiMi)         策略/交易/预警/缓存 (42端点)
🧙 魔法师 (Sorcerer)       V4多模式翻倍股引擎
🌊 HiTao                   美股扫描/黄金/盈亏/投资建议 (32端点)
🧙‍♂️ Magician               翻倍/做空/杠杆 (magician/目录, 14端点)
🔭 哨兵 (Sentinel)         安全扫描/测试/备份/完整性/存储监控
📡 通讯员 (Correspondent)  消息状态机/模板/每日摘要
```

## 技术栈

| 层 | 技术 |
|:---|:---|
| Web | Flask + flask-cors |
| 数据库 | SQLite (可切换 PostgreSQL) |
| 缓存 | 内存 + Redis 双后端 |
| A股数据 | tushare Pro + 东方财富 + 巨潮资讯 |
| 美股数据 | yfinance + Finnhub |
| 前端 | 原生 HTML/CSS/JS SPA |
| 代理 | Nginx (:80 → :7890) |
| 安全 | hermes_security_guardian (92/A级) |

## 快速启动

```bash
# 1. 配置密钥
cp .env.example .env
# 编辑 .env 填入 TUSHARE_TOKEN 和 FINNHUB_KEY

# 2. 安装依赖
pip install flask flask-cors numpy pandas tushare yfinance requests python-dotenv

# 3. 启动
bash start.sh
# 访问 http://localhost:7890

# 4. 健康检查
curl http://localhost:7890/api/health
curl http://localhost:7890/api/health/deps
```

## 项目结构

```
shimi-trading-studio/
├── api/            A股蓝图 (10个)          # 拾米A股 + 建筑师
├── haitao/         美股模块 (21文件,4蓝图)  # HiTao
├── magician/       美股翻倍股 (10文件,3蓝图) # Magician
├── services/       业务逻辑层 (13模块)      # 拾米 + 魔法师
├── data/           数据抓取层 (13模块)      # tushare/yfinance统一入口
├── db/             数据库层 (6模块)         # SQLite/PostgreSQL
├── hermes_security_guardian/ 安全扫描器     # 哨兵
├── app.py          应用工厂
├── backend.py      启动入口 (84行纯启动)
├── config.py       统一配置
├── cache.py        缓存接口
├── logger.py       统一日志
├── middleware.py    请求追踪/错误处理/超时
├── monitor.py       系统监控 + 依赖健康检查
├── message_queue.py 消息队列 (状态机+重试)
├── message_templates.py 消息模板
├── test_smoke.py    冒烟测试 (11项)
├── test_api.py      API测试 (19端点)
└── cron_*.sh        定时任务 (8个)
```

## API 端点

| 模块 | 前缀 | 数量 |
|:---|:---|:---:|
| A股市场 | `/api/*` | 42 |
| 美股HiTao | `/api/us/*` | 32 |
| 美股Magician | `/api/magician/*` | 14 |
| **总计** | | **112** |

## 定时任务

| 脚本 | 频率 | 功能 |
|:---|:---|:---|
| cron_db_backup.sh | 每日 02:00 | 数据库备份 |
| cron_data_integrity.sh | 每日 01:00 | 数据完整性+存储监控 |
| cron_alert_check.sh | 每5分钟 | 预警条件检查 |
| cron_daily_digest.sh | 工作日 18:00 | 每日收盘摘要 |
| cron_scan_us.sh | 定时 | 美股扫描 |
| cron_send_review.sh | 定时 | 复盘报告推送 |
| cron_security_guardian.sh | 定时 | 安全扫描 |
| cron_reflect.sh | 定时 | 每日反思 |

## 测试

```bash
python3 test_smoke.py    # 冒烟测试 (11项)
python3 test_api.py      # API端点测试 (19端点)
python3 hermes_security_guardian.py --verbose  # 安全扫描
```

## 核心原则

- **低耦合**: api/ ↔ haitao/ 零互引，模块变更不波及其他
- **数据驱动**: 所有决策基于 data/fetcher.py 真实数据
- **风控纪律**: 单票≤40% | 总回撤≤15% | 3日跌>5%止损
- **变更审计**: 任何角色变更后必须呼叫建筑师全量审计
- **市场隔离**: A股逻辑 ≠ 美股逻辑

详见 [灵魂.md](灵魂.md) | [画像.md](画像.md) | [记忆.md](记忆.md)
