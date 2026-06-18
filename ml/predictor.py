"""
先知 (Prophet) · ML预测引擎
ml/predictor.py

训练 + 推理 + 模型管理。
使用 sklearn + 模拟数据快速验证架构。
生产环境替换为真实 tushare 数据。
"""

import os
import pickle
import json
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import numpy as np

MODEL_DIR = os.path.join(os.path.dirname(__file__), "models")
os.makedirs(MODEL_DIR, exist_ok=True)

FEATURE_NAMES = [
    "price_score", "industry_score", "mv_score",          # D1-D3: 价格/行业/市值
    "momentum_score", "turnover_score", "board_score",    # D4-D6: 动量/换手/板块
    "catalyst_d7", "catalyst_d8", "pre_surge_d9",         # D7-D9: 催化剂
    "resonance_d10",                                      # D10: 共振
    "ma_convergence", "vol_contraction",                  # D11-D12: 均线收敛/缩量
    "monthly_change", "limit_up_count", "stage_encoded",  # D13-D15: 月涨幅/连板/阶段
]

STAGE_MAP = {"early": 0, "warming": 1, "mid_stage": 2, "late": 3, "pullback": 4, "excluded": 5, "neutral": 0}


def train_model(X: np.ndarray = None, y: np.ndarray = None) -> dict:
    """训练 ML 模型

    Args:
        X: 特征矩阵 (n_samples, 15)。None 时使用模拟数据。
        y: 标签 (n_samples,)。1=翻倍, 0=未翻倍

    Returns:
        {"model_type": str, "accuracy": float, "precision@30": float, "version": str}
    """
    if X is None:
        X, y = _generate_synthetic_data()

    from sklearn.ensemble import RandomForestClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import cross_val_score

    # 基学习器
    rf = RandomForestClassifier(n_estimators=200, max_depth=8, random_state=42)
    lr = LogisticRegression(max_iter=1000, random_state=42)

    # 训练
    rf.fit(X, y)
    lr.fit(X, y)

    # 评估: 交叉验证准确率
    rf_cv = cross_val_score(rf, X, y, cv=5, scoring="accuracy").mean()
    lr_cv = cross_val_score(lr, X, y, cv=5, scoring="accuracy").mean()

    # 选最优模型
    best_model = rf if rf_cv >= lr_cv else lr
    best_name = "RandomForest" if rf_cv >= lr_cv else "LogisticRegression"

    # 集成评分: 简单平均 (金融ML研究: Stacking不优于简单平均)
    def _ensemble_proba(X_pred):
        return (rf.predict_proba(X_pred)[:, 1] + lr.predict_proba(X_pred)[:, 1]) / 2

    # 保存模型
    version = datetime.now().strftime("%Y%m%d")
    model_path = os.path.join(MODEL_DIR, f"prophet_{version}.pkl")
    with open(model_path, "wb") as f:
        pickle.dump({
            "rf": rf,
            "lr": lr,
            "feature_names": FEATURE_NAMES,
            "stage_map": STAGE_MAP,
            "version": version,
        }, f)

    # 特征重要性
    if best_name == "RandomForest":
        importances = dict(zip(FEATURE_NAMES, rf.feature_importances_.tolist()))
    else:
        importances = dict(zip(FEATURE_NAMES, abs(lr.coef_[0]).tolist()))

    return {
        "model_type": best_name,
        "accuracy": round(max(rf_cv, lr_cv), 3),
        "precision_at_30": round(_precision_at_k(_ensemble_proba, X, y, k=30), 3),
        "version": version,
        "feature_count": len(FEATURE_NAMES),
        "sample_count": len(y),
        "positive_rate": round(y.mean(), 3),
        "feature_importance": importances,
        "model_path": model_path,
    }


def predict_single(features: Dict) -> Dict:
    """单股翻倍概率预测

    Args:
        features: 15维特征字典 {"price_score": 25, "monthly_change": 5.2, ...}

    Returns:
        {"probability": float, "confidence": str, "top_features": list}
    """
    # 加载最新模型
    model_data = _load_latest_model()
    if model_data is None:
        return {"error": "no model available, train first"}

    # 构建特征向量
    X = np.zeros((1, len(FEATURE_NAMES)))
    for i, name in enumerate(FEATURE_NAMES):
        if name in features:
            X[0, i] = float(features[name])
        elif name == "stage_encoded":
            stage = features.get("early_stage", "neutral")
            X[0, i] = STAGE_MAP.get(stage, 0)

    # 集成预测
    rf = model_data["rf"]
    lr = model_data["lr"]
    prob_rf = rf.predict_proba(X)[0, 1]
    prob_lr = lr.predict_proba(X)[0, 1]
    prob = (prob_rf + prob_lr) / 2

    # 关键特征
    if hasattr(rf, "feature_importances_"):
        top_idx = np.argsort(rf.feature_importances_)[-3:][::-1]
        top_features = [(FEATURE_NAMES[i], round(float(X[0, i]), 2)) for i in top_idx]
    else:
        top_features = []

    confidence = "high" if prob > 0.6 else "medium" if prob > 0.3 else "low"

    return {
        "probability": round(float(prob), 4),
        "confidence": confidence,
        "top_features": top_features,
    }


def predict_batch(features_list: List[Dict]) -> List[Dict]:
    """批量预测"""
    return [predict_single(f) for f in features_list]


def _generate_synthetic_data(n_samples: int = 5000) -> Tuple[np.ndarray, np.ndarray]:
    """生成模拟训练数据 (生产环境替换为真实 tushare 数据)

    模拟特征分布参考历史翻倍股规律:
      - 低价格 (<20) 翻倍概率高
      - 小市值 (<50亿) 翻倍概率高
      - 高催化剂 (d7>10) 翻倍概率高
      - 月涨幅 0~10% 是最佳启动区间
    """
    np.random.seed(42)
    X = np.zeros((n_samples, len(FEATURE_NAMES)))
    y = np.zeros(n_samples)

    for i in range(n_samples):
        # D1-D3: 基础评分
        X[i, 0] = np.random.randint(5, 35)    # price_score
        X[i, 1] = np.random.randint(0, 25)    # industry_score
        X[i, 2] = np.random.randint(0, 30)    # mv_score

        # D4-D6: 动量
        X[i, 3] = np.random.randint(0, 15)    # momentum_score
        X[i, 4] = np.random.randint(0, 15)    # turnover_score
        X[i, 5] = np.random.randint(0, 10)    # board_score

        # D7-D10: 催化剂
        X[i, 6] = np.random.randint(0, 20)    # catalyst_d7
        X[i, 7] = np.random.randint(0, 10)    # catalyst_d8
        X[i, 8] = np.random.randint(0, 10)    # pre_surge_d9
        X[i, 9] = np.random.randint(0, 5)     # resonance_d10

        # D11-D12: 技术指标
        X[i, 10] = np.random.uniform(-0.05, 0.05)  # ma_convergence
        X[i, 11] = np.random.uniform(0.3, 1.5)     # vol_contraction

        # D13-D15: 启动特征
        X[i, 12] = np.random.uniform(-10, 40)      # monthly_change
        X[i, 13] = np.random.randint(0, 5)         # limit_up_count
        X[i, 14] = np.random.randint(0, 5)         # stage_encoded

        # 标签生成: 综合多个因素
        base_score = X[i, :6].sum() / 130  # 基础评分归一化
        cat_score = X[i, 6:10].sum() / 45   # 催化剂归一化
        early_bonus = 0.3 if 0 <= X[i, 12] <= 15 else (-0.2 if X[i, 12] > 30 else 0)
        limit_penalty = -0.3 if X[i, 13] >= 3 else 0

        prob = base_score * 0.3 + cat_score * 0.4 + early_bonus + limit_penalty + np.random.normal(0, 0.1)
        y[i] = 1 if prob > 0.55 else 0

    # 确保正负样本比例 ~1:20 (模拟真实市场)
    pos = y.sum()
    target_pos = int(n_samples * 0.05)  # 5%
    if pos > target_pos:
        neg_idx = np.where(y == 1)[0]
        to_flip = np.random.choice(neg_idx, int(pos - target_pos), replace=False)
        y[to_flip] = 0

    return X, y


def _precision_at_k(predict_fn, X: np.ndarray, y: np.ndarray, k: int = 30) -> float:
    """Precision@K: 预测概率 Top K 中真正为正的占比"""
    probas = predict_fn(X)
    top_k_idx = np.argsort(probas)[-k:]
    if len(top_k_idx) == 0:
        return 0.0
    return y[top_k_idx].mean()


def _load_latest_model() -> Optional[dict]:
    """加载最新版本的模型"""
    models = sorted(
        [f for f in os.listdir(MODEL_DIR) if f.startswith("prophet_") and f.endswith(".pkl")],
        reverse=True,
    )
    if not models:
        return None
    with open(os.path.join(MODEL_DIR, models[0]), "rb") as f:
        return pickle.load(f)


# ─── CLI ──────────────────────────────────────────

if __name__ == "__main__":
    print("🧪 先知 ML引擎 · 训练测试")
    result = train_model()
    print(f"  模型: {result['model_type']}")
    print(f"  准确率: {result['accuracy']:.1%}")
    print(f"  Precision@30: {result['precision_at_30']:.1%}")
    print(f"  样本数: {result['sample_count']} (正样本率: {result['positive_rate']:.1%})")
    print(f"  版本: {result['version']}")
    print(f"\n  特征重要性 Top 5:")
    for name, imp in sorted(result['feature_importance'].items(), key=lambda x: -x[1])[:5]:
        print(f"    {name}: {imp:.3f}")

    # 测试单股预测
    test_features = {
        "price_score": 25, "industry_score": 15, "mv_score": 20,
        "momentum_score": 8, "turnover_score": 10, "board_score": 5,
        "catalyst_d7": 16, "catalyst_d8": 5, "pre_surge_d9": 4, "resonance_d10": 2,
        "ma_convergence": 0.01, "vol_contraction": 0.6,
        "monthly_change": 8.5, "limit_up_count": 0, "early_stage": "early",
    }
    pred = predict_single(test_features)
    print(f"\n  单股预测: 概率={pred['probability']:.1%} 置信度={pred['confidence']}")
