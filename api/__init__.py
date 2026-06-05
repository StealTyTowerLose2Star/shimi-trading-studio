"""
拾米交易工作室 - API 蓝图注册
"""
from flask import Flask


def register_blueprints(app: Flask):
    """注册所有 API 蓝图到 Flask 应用"""
    from .market import bp as market_bp
    from .strategy import bp as strategy_bp
    from .advice import bp as advice_bp
    from .trade import bp as trade_bp
    from .monitor import bp as monitor_bp
    from .doubler import bp as doubler_bp
    from .review import bp as review_bp
    from .margin import bp as margin_bp

    app.register_blueprint(market_bp)
    app.register_blueprint(strategy_bp)
    app.register_blueprint(advice_bp)
    app.register_blueprint(trade_bp)
    app.register_blueprint(monitor_bp)
    app.register_blueprint(doubler_bp)
    app.register_blueprint(review_bp)
    app.register_blueprint(margin_bp)

    # 海淘美股模块
    try:
        from haitao.api import bp as haitao_bp
        app.register_blueprint(haitao_bp)
    except Exception as e:
        print(f"⚠️ 海淘模块注册失败: {e}")
