# 观星台 + 先知 · 架构嵌入方案

> 两个新模块与现有7角色系统的集成设计

---

## 一、模块定位

```
现有7角色:
  🏛️ 建筑师    🍚 拾米A股    🧙 魔法师
  🌊 HiTao     🧙‍♂️ Magician   🔭 哨兵    📡 通讯员

新增2模块:
  🔭 观星台 (Observatory)   — 跨市场数据层, 不属于任何单一角色
  🔮 先知   (Prophet)       — 跨角色ML基础设施
```

## 二、观星台 集成

### 数据流

```
数据源 → 观星台 → 下游消费

巨潮资讯 ──┐
Finnhub ───┤
yfinance ──┼──→ data/market_events.py ──→ ① 消息队列 (告警)
东方财富 ──┤                              ├─→ ② 每日摘要 (cron_daily_digest)
新闻聚合 ──┘                              ├─→ ③ 魔法师 catalyst_engine (C1/C6加分)
                                          └─→ ④ /api/market/events (前端面板)
```

### 接口契约

| 接口 | 方向 | 数据 |
|:---|:---|:---|
| 观星台 → 通讯员 | message_queue | 高影响事件告警 |
| 观星台 → 魔法师 | catalyst_engine | 政策/行业事件 → C1/C6加分 |
| 观星台 → 前端 | /api/market/events | 事件列表+交易信号 |
| 前端 → 观星台 | /api/market/events/refresh | 手动刷新 |

### 零耦合保证

```
✅ 观星台不 import api/ (不调用任何蓝图)
✅ 观星台不 import haitao/ (不跨市场)
✅ 观星台不 import services/strategy 等 (不侵入业务)
✅ 仅依赖: data/fetcher.py + message_queue.py (已批准的共享基础设施)
```

## 三、先知 集成

### 数据流

```
魔法师 V4 ──→ doubler_scanner ──→ 规则评分 (D1-D10)
                                   │
先知     ──→ ml/predictor ──────→ ML评分 (15维)
                                   │
                                   ├──→ 集成评分 (规则×0.4 + ML×0.6)
                                   │
                                   └──→ 精英池 Top 30
```

### 接口契约

| 接口 | 方向 | 数据 |
|:---|:---|:---|
| 先知 → 魔法师 | doubler_scanner | ML翻倍概率 → 集成评分 |
| 先知 → Magician | magician/doubler_scanner | ML翻倍概率 → 集成评分 |
| 前端 → 先知 | /api/prophet/predict | 单股预测查询 |
| 先知 → 通讯员 | message_queue | 月度回测报告 |

### 零耦合保证

```
✅ 先知不 import api/ (不调用任何蓝图)
✅ 先知不 import haitao/ 或 magician/ (不跨市场)
✅ 先知不 import services/strategy 等 (不侵入业务)
✅ 仅依赖: data/fetcher.py (训练数据) + sklearn/xgboost (ML库)
```

## 四、代码目录

```
shimi-trading-studio/
├── data/market_events.py      ← 观星台引擎 (已创建)
├── api/market_events.py       ← 观星台 API (已创建)
├── ml/                         ← 先知模块
│   ├── __init__.py            (已创建)
│   ├── predictor.py           (待构建)
│   ├── backtest.py            (待构建)
│   └── models/                (训练好的模型文件 .pkl)
├── api/prophet.py             (待构建)
├── cron_market_events.sh      (待构建: 每30分钟扫描)
├── cron_prophet_train.sh      (待构建: 每月1日重训)
├── docs/
│   ├── observatory-spec.md    (已创建)
│   └── prophet-spec.md        (已创建)
└── skills/
    └── shimi-trading/
        ├── shimi-observatory  (待创建)
        └── shimi-prophet      (待创建)
```

## 五、定时任务

| 脚本 | 频率 | 功能 |
|:---|:---|:---|
| cron_market_events.sh | 每30分钟 | 观星台扫描 + 高影响推送到消息队列 |
| cron_prophet_train.sh | 每月1日 08:00 | 先知重训模型 + 回测 + 报告 |
| cron_daily_digest.sh | 工作日 18:00 | 摘要增加 观星台事件 + 先知预测变化 |

## 六、前端集成

| 面板 | 内容 |
|:---|:---|
| 观星台面板 | 近期事件时间线 / 受影响标的 / 多空信号 / 影响评级 |
| 先知面板 | 本月预测 Top 10 / 模型准确率 / 历史回测曲线 |
| 每日摘要 | 新增"市场事件"段 + "先知预测"段 |
