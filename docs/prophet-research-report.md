# 先知 (Prophet) · ML预测引擎 研究报告

> 研究日期: 2026-06-09 | 角色: 观星台 | 目标: 学术理论→工程实操→项目改造路线图

---

## 一、现状审计

### 1.1 代码真实状态

| 模块 | 技能文档描述 | 实际状态 |
|:---|:---|:---|
| `ml/predictor.py` | ML训练+推理 (Logistic/XGBoost/Stacking) | ❌ 不存在 |
| `ml/backtest.py` | 回测框架 (夏普/回撤/胜率) | ❌ 不存在 |
| `api/prophet.py` | API蓝图 `/api/prophet/*` | ❌ 不存在 |
| `cron_prophet_train.sh` | 每月1日自动重训 | ❌ 不存在 |
| `ml/__init__.py` | 模块入口 | ⚠️ 仅占位注释 |
| `docs/prophet-spec.md` | 设计文档 | ✅ 存在 (79行) |

### 1.2 当前预测体系: 纯规则引擎

现有翻倍股预测全部基于硬编码规则，无ML组件：

```
services/doubler_scanner.py (840行)
├── D1 price_score:        <10元=30, <20元=25, <50元=20...
├── D2 industry_score:     热门行业Top7=20, 热门=15, 其他=8
├── D3 mv_score:           <20亿=25, <50亿=20, <100亿=15...
├── D4 momentum_score:     涨幅>9.5%=+5, >5%=+3...
├── D5 turnover_score:     硬编码阈值
├── D6 board_score:        主板=5, 创业板=3, 中小板=2, 北交=1
├── D0 early_stage:        价格位置/月涨幅/压缩/缩量/ATR
├── D7 catalyst_score:     C1政策/C2商品/C3合同/C5重组/C6概念/C7业绩
├── D8 catalyst_grade:     强度等级
├── D9 pre_surge:           龙虎榜+资金流
└── D10 resonance:         多催化剂共振=+2

total_score = D1~D6(base) + D0 + D7 + D8 + D9 + D10
```

**核心缺陷:**
- 权重全凭经验设定，未做统计验证
- 评分阈值硬编码，无法适应市场结构变化
- 无回测验证框架（规则修改后无法量化效果）
- 无概率校准（"70分"在不同市场中含义不同）
- 无过拟合防护（特征选择无系统方法）

### 1.3 观星台现状

| 模块 | 状态 | 说明 |
|:---|:---|:---|
| `data/market_events.py` (344行) | ✅ | 事件抓取/分类/映射 |
| `api/market_events.py` | ✅ | API端点 |
| 事件类型 | 6类 | 政策/财报/宏观/地缘/行业/商品 |
| 数据源 | 4个 | 巨潮资讯/Finnhub/yfinance/东方财富 |
| 标的映射 | 13行业×78标的 | A股48+美股30 |
| ML集成 | ❌ | 事件→信号为规则映射，无ML |

---

## 二、学术理论核心发现

### 2.1 金融ML的三大基石论文

| 论文 | 核心结论 | 与我们的关系 |
|:---|:---|:---|
| **Gu, Kelly & Xiu (2020, RFS)** | 13种ML方法预测美股收益，GBRT最优，月度R²≈0.4% | ⭐ 方法论基准 |
| **López de Prado (2018)** | 金融ML的系统工程：Purged Walk-Forward、回测7大陷阱 | ⭐ 工程案头书 |
| **Bailey et al. (2014)** | Deflated Sharpe Ratio: 多重测试后多数"好策略"统计不显著 | ⭐ 过拟合警示 |

### 2.2 模型选择矩阵

| 模型 | 优势 | 劣势 | 适用场景 | 推荐度 |
|:---|:---|:---|:---|:---|
| **XGBoost/LightGBM** | 非线性+缺失值+特征交互 | 外推差、对时序不敏感 | 横截面选股 | ⭐⭐⭐ |
| **LSTM/GRU** | 捕捉时变模式+自相关 | 需大量数据、易过拟合 | 时序动量/趋势 | ⭐⭐ |
| **Logistic Regression** | 可解释性+标定好 | 线性假设、交互需手动 | Stacking元学习器 | ⭐⭐⭐ |
| **Transformer** | 长程依赖+并行训练 | 金融收益预测增量有限 | 实验性 | ⭐ |

### 2.3 集成学习的核心教训

1. **Stacking不一定是好选择**: 简单模型平均（等权/IC加权）在金融中样本外表现常优于复杂Stacking（Zhang et al., 2020; Gu et al., 2020）
2. **原因**: 低信噪比下，元学习器过拟合验证集的风险 > 多样性收益
3. **最佳实践**: 用逻辑回归做元学习器（而非XGBoost），用时序CV（而非随机K-fold）

### 2.4 金融预测的独特挑战

| 挑战 | 严重程度 | 对翻倍股预测的影响 |
|:---|:---|:---|
| 低信噪比 | ⭐⭐⭐ | 月频R²达0.5%已世界级; 翻倍股是极端尾部事件 |
| 非平稳性 | ⭐⭐⭐ | A股2017年后小市值因子回撤; 注册制改变市场结构 |
| 前视偏差 | ⭐⭐⭐ | 财报延迟可达4个月; 回测用"未发布数据"严重高估 |
| 幸存者偏差 | ⭐⭐ | 退市ST股被排除→高估历史收益 |
| 小样本 | ⭐⭐⭐ | 月翻倍股仅1-5只/月; 正样本极度稀疏 |

### 2.5 翻倍股预测的特殊性

翻倍股是**极端尾部事件**（月涨幅≥100%），与"收益率预测"有本质不同：

1. **样本极度不平衡**: 每月沪深5000只中仅1-5只翻倍 → 正负比 1:1000+
2. **标签定义需精炼**: 不能简单用"月涨幅≥100%"，需区分启动前/启动中/已启动
3. **特征工程方向不同**: 催化剂事件、资金异动、龙虎榜比标准技术指标更重要
4. **评估指标不同**: Precision@Top30比AUC更有意义（只需要Top30列表）

---

## 三、工程实操框架

### 3.1 数据划分: Purged Walk-Forward

```python
# 核心纪律: 严禁随机shuffle
# 训练/验证/测试必须有时间顺序 + purge + embargo

train_window = 504天   # 覆盖至少2个完整市场周期
test_window  = 63天    # 季度验证
purge_days   = 21天    # 标签forward horizon
embargo_days = 30天    # 防止信息回流
step_size    = 63天    # 季度滚动

# 最少10个分割, 保证统计显著性
```

### 3.2 特征工程体系

当前10维特征的改进方向：

| 现有维度 | 当前方法 | ML提升方向 |
|:---|:---|:---|
| D1 price | 硬编码5档 | → 价格分位数(滚动窗口) + 相对行业折溢价 |
| D2 industry | 22个热门行业加分 | → 行业动量(20日排名) + 行业轮动速度 |
| D3 mv | 硬编码5档 | → 市值分位数 + 流通市值/总市值比 |
| D4 momentum | 单日pct_chg | → 5日/10日/20日多周期动量 + 夏普 |
| D5 turnover | 硬编码阈值 | → 换手率20日分位数 + 量比趋势 |
| D6 board | 板块固定分 | → 板块相对强弱(20日) + 板块资金净流入 |
| D7 catalyst | 文本关键词计数 | → TF-IDF向量化 + 事件强度NLP |
| D9 pre_surge | 龙虎榜+资金流 | → 机构净买入占比 + 大单成交比例 |

**新增建议维度 (D11-D15):**
- **D11**: 20日波动率分位数 (低波动启动更有力)
- **D12**: 距52周高点距离 (超卖反弹vs强势突破)
- **D13**: 机构持仓变化 (北向资金+融资余额)
- **D14**: 同行业龙头20日涨幅 (板块带动效应)
- **D15**: 财务健康评分 (负债率/现金流/ROE合成)

### 3.3 回测框架设计

```
样本内(诊断) → 样本外Walk-Forward(决策) → 纸交易(验证执行)

关键检查点:
├── 前视偏差: T日收盘→T+1日开盘执行(不能T日收盘)
├── 财务数据: 必须用point-in-time, 或统一滞后60天
├── 停牌处理: 停牌期不计入收益, 恢复后开盘价执行
├── 涨跌停保护: 涨停不能买入, 跌停不能卖出
├── 交易成本: 佣金万2.5+印花税千1(卖)+滑点3bp+冲击
└── 股票池: 必须包含退市标的 (防幸存者偏差)
```

### 3.4 评估指标体系

| 指标 | 合理阈值 | 说明 |
|:---|:---|:---|
| Precision@30 | >15% | Top30中实际翻倍比例 |
| Rank IC | >0.03 | 评分与实际收益的Spearman相关 |
| ICIR | >0.5 | IC/IC标准差, 稳定性 |
| 命中率 | >60% | 翻倍股被Top30命中比例 |
| 月胜率 | >55% | Top30月均收益>全市场月均的比例 |
| prediction_PSI | <0.25 | 预测分数分布稳定性 |

### 3.5 概率校准

**关键判断**: 我们的Top30选股是**纯排序任务**，不是概率阈值任务。

| 策略 | 是否需要校准 | 原因 |
|:---|:---|:---|
| Top30推荐（当前） | ❌ 不需要 | 排序好即可, IC稳定更重要 |
| 入选门槛"≥X分" | ✅ 必须 | 否则阈值无意义 |
| 仓位=概率×资金 | ✅ 必须 | 概率需映射到真实胜率 |

**实践建议**: 先用Brier Score+可靠性图诊断模型输出是否需校准；如需要，用Isotonic Regression。

### 3.6 MLOps 技术栈推荐

| 类别 | 工具 | 优先级 |
|:---|:---|:---|
| 实验追踪 | MLflow | P0 |
| 数据版本 | DVC | P1 |
| 漂移监控 | Evidently / PSI自实现 | P1 |
| 模型解释 | SHAP | P0 |
| 概率校准 | sklearn.calibration | P2 |
| 调度 | cron + shell | P0（已有） |

---

## 四、项目改造路线图

### Phase 1: 基础设施 (1-2天)

| 任务 | 产出 | 优先级 |
|:---|:---|:---|
| 1.1 创建 `ml/predictor.py` | Logistic基准模型 | P0 |
| 1.2 创建 `ml/backtest.py` | Purged Walk-Forward框架 | P0 |
| 1.3 创建 `ml/features.py` | 15维特征工程管道 | P0 |
| 1.4 创建 `api/prophet.py` | `/api/prophet/predict`端点 | P0 |
| 1.5 创建 `cron_prophet_train.sh` | 每月1日自动重训 | P1 |

### Phase 2: 基准训练 (2-3天)

| 任务 | 产出 | 说明 |
|:---|:---|:---|
| 2.1 训练数据构建 | 2023-2025历史翻倍股+负样本 | 正: 月涨幅≥100%, 负: 随机采样1:3 |
| 2.2 Logistic基准 | sklearn LogisticRegression | AUC/Precision@30 |
| 2.3 XGBoost模型 | xgboost.XGBClassifier | 与Logistic对比 |
| 2.4 特征重要性 | SHAP + Permutation | 验证D0-D10权重是否合理 |
| 2.5 回测报告 | 2025H2样本外验证 | 准确率/召回率/F1/IC |

### Phase 3: 集成优化 (2-3天)

| 任务 | 产出 | 说明 |
|:---|:---|:---|
| 3.1 Stacking Ensemble | Logistic(元)+XGBoost+RF(基) | Purged CV, 逻辑回归元学习器 |
| 3.2 简单平均Baseline | 规则+ML等权融合 | 对比Stacking效果 |
| 3.3 A/B测试框架 | 规则vsML双轨运行 | 月度对比报告 |
| 3.4 概率校准 | Platt/Isotonic | 评估是否需要 |

### Phase 4: 监控与迭代 (持续)

| 任务 | 产出 | 说明 |
|:---|:---|:---|
| 4.1 PSI监控 | prediction_PSI + feature_PSI | 日频自动检测 |
| 4.2 自动重训练 | 触发条件 + 自动流水线 | PSI>0.25连续5日/IC转负/K-S检验p<0.01 |
| 4.3 模型版本管理 | MLflow Registry | Staging→Production→Archived |
| 4.4 SHAP报告 | 月度特征归因 | 通信员自动推送 |

---

## 五、观星台 ML 增强建议

### 5.1 事件→信号 ML 化

当前: 事件关键词匹配 → 硬编码映射到标的

| 改进 | 方法 | 效果 |
|:---|:---|:---|
| 事件重要性排序 | NLP TF-IDF + 历史影响回测 | 事件不再"等权" |
| 标的映射ML化 | 行业链+供应链知识图谱 | 避免遗漏间接受益标的 |
| 信号强度校准 | 事件→历史涨跌幅回归 | "high impact"有数据支持 |
| 事件衰减建模 | 指数衰减函数拟合 | 持续时间不再硬编码 |

### 5.2 跨市场数据层

当前: 市场隔离 (api/与haitao/零互引)

| 增强 | 方法 | 价值 |
|:---|:---|:---|
| A股→美股映射 | 同类公司+供应链关联 | 美股财报先行指标 |
| 商品→股票映射 | 历史弹性系数 | 锂价→锂电股延时映射 |
| 政策→板块扩散 | Granger因果检验 | 政策影响的传导路径 |

---

## 六、风险警示

### 6.1 不做什么

- ❌ **不追求每月翻倍股100%命中**: 目标60%命中率即可，追求完美命中会导致过拟合
- ❌ **不盲目用深度学习**: LSTM/Transformer在翻倍股预测中证据不足，先从XGBoost开始
- ❌ **不随机切分数据**: 唯一可接受的CV方法是时序Walk-Forward
- ❌ **不信任单次回测结果**: 必须用Deflated Sharpe Ratio评估统计显著性
- ❌ **不让ML输出直接取代规则引擎**: 双轨运行至少6个月，规则作为安全网

### 6.2 合理的性能预期

| 指标 | 合理目标 | 世界级 |
|:---|:---|:---|
| Precision@30 (月度) | 13-17% (每月4-5只真翻倍) | >20% |
| 翻倍股命中率 | 55-65% | >70% |
| Rank IC | 0.03-0.06 | >0.08 |
| 样本外R² | 0.3-0.5% | >1% |

### 6.3 灵魂宪章约束

- ✅ 数据驱动,不信故事
- ✅ 风控硬约束不可被ML软化
- ✅ 低耦合优先: ml/不 import api/ 或 haitao/
- ✅ 单一数据入口: 仅通过 data/fetcher.py
- ❌ 禁止绕过风控: 止损/仓位限制不可ML动态调整

---

## 七、参考文献

### 必读论文 (按优先级)

1. ⭐⭐⭐ **Gu, S., Kelly, B. & Xiu, D. (2020).** "Empirical Asset Pricing via Machine Learning." *Review of Financial Studies*, 33(5), 2223-2273. — 金融ML全面基准比较

2. ⭐⭐⭐ **López de Prado, M. (2018).** *Advances in Financial Machine Learning*. Wiley. — 金融ML工程实践

3. ⭐⭐⭐ **Bailey, D.H. et al. (2014).** "The Deflated Sharpe Ratio: Correcting for Selection Bias." *Journal of Portfolio Management*, 41(1). — 多重测试风险

4. ⭐⭐ **Fama, E.F. & French, K.R. (2015).** "A Five-Factor Asset Pricing Model." *Journal of Financial Economics*, 116(1), 1-22. — 因子模型基准

5. ⭐⭐ **Lundberg, S.M. & Lee, S.I. (2017).** "A Unified Approach to Interpreting Model Predictions." *NeurIPS 2017*. — SHAP特征重要性

6. ⭐⭐ **Arnott, R.D., Harvey, C.R. & Markowitz, H. (2019).** "A Backtesting Protocol in the Era of Machine Learning." *Journal of Financial Data Science*, 1(1). — 回测协议

7. ⭐ **Fischer, T. & Krauss, C. (2018).** "Deep Learning with LSTM for Financial Market Predictions." *European Journal of Operational Research*, 270(2). — LSTM金融应用

8. ⭐ **Zeng, A. et al. (2023).** "Are Transformers Effective for Time Series Forecasting?" *AAAI 2023*. — 简单模型 > 复杂Transformer警示

9. ⭐ **Liu, J., Stambaugh, R.F. & Yuan, Y. (2019).** "Size and Value in China." *Journal of Financial Economics*, 134(1). — A股因子模型

10. ⭐ **Chen, T. & Guestrin, C. (2016).** "XGBoost: A Scalable Tree Boosting System." *KDD 2016*. — XGBoost原始论文

---

## 八、立即行动清单

- [ ] **今天**: 创建 `ml/predictor.py` (Logistic基准)
- [ ] **明天**: 创建 `ml/backtest.py` (Purged Walk-Forward)
- [ ] **本周**: 完成Phase 1全部基础设施
- [ ] **下周**: 训练Logistic + XGBoost基准模型
- [ ] **两周内**: 完成首次样本外回测报告
- [ ] **月度**: 双轨运行(规则+ML并行), 对比报告
- [ ] **季度**: 决定ML是否替代/补充规则引擎
