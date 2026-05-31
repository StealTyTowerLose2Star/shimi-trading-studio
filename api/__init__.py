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

    app.register_blueprint(market_bp)
    app.register_blueprint(strategy_bp)
    app.register_blueprint(advice_bp)
    app.register_blueprint(trade_bp)
    app.register_blueprint(monitor_bp)
