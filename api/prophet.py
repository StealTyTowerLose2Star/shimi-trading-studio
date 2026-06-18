"""
先知 (Prophet) · API 蓝图
url_prefix: /api/prophet
"""
from flask import Blueprint, jsonify, request

bp = Blueprint("prophet", __name__, url_prefix="/api/prophet")


@bp.route("/train", methods=["POST"])
def api_train():
    """触发模型训练 (月度或手动)"""
    from ml.predictor import train_model
    result = train_model()
    return jsonify(result)


@bp.route("/predict", methods=["POST"])
def api_predict():
    """单股翻倍概率预测

    Body: {"price_score":25, "industry_score":15, ... "monthly_change":8.5}
    """
    data = request.get_json(force=True, silent=True) or {}
    if not data:
        return jsonify({"error": "请提供15维特征数据"}), 400
    from ml.predictor import predict_single
    return jsonify(predict_single(data))


@bp.route("/batch", methods=["POST"])
def api_batch_predict():
    """批量预测"""
    data = request.get_json(force=True, silent=True) or {}
    features_list = data.get("stocks", [])
    if not features_list:
        return jsonify({"error": "请提供 stocks 数组"}), 400
    from ml.predictor import predict_batch
    return jsonify({"predictions": predict_batch(features_list)})


@bp.route("/status")
def api_status():
    """模型状态"""
    from ml.predictor import _load_latest_model
    import os
    model = _load_latest_model()
    if model:
        model_dir = os.path.join(os.path.dirname(__file__), "..", "ml", "models")
        files = os.listdir(model_dir) if os.path.exists(model_dir) else []
        return jsonify({
            "trained": True,
            "version": model.get("version", "?"),
            "model_type": "RandomForest+LogisticRegression",
            "models_stored": len([f for f in files if f.endswith(".pkl")]),
        })
    return jsonify({"trained": False, "message": "尚未训练, POST /api/prophet/train"})
