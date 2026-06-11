"""
先知 ML 引擎 (Prophet ML Engine)

职责: 数据驱动的翻倍股预测、事件信号分析、回测验证
架构: 零耦合 — 不 import api/ haitao/ magician/
      单一数据入口 — data/fetcher.py

模块:
  event_predictor.py  — 事件抓取→TF-IDF分类→情绪量化→做多/做空信号
  market_mapper.py    — 事件→行业→个股概率映射
  predictor.py        — (Phase 2) Logistic/XGBoost翻倍股预测
  backtest.py         — (Phase 2) Purged Walk-Forward回测框架
  features.py         — (Phase 2) 15维特征工程管道
"""
