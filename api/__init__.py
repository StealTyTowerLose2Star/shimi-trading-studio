"""
拾米交易工作室 - API 蓝图注册
建筑师基础设施: 所有路由的装配点
"""
from flask import Flask


def register_blueprints(app: Flask):
    """注册所有 API 蓝图到 Flask 应用

    蓝图清单 (按角色):
      拾米A股: market, strategy, advice, trade, review, margin, alert, a_stock_cache
      魔法师:   doubler
      建筑师:   monitor
      HiTao:    haitao
    """
    from .market import bp as market_bp
    from .strategy import bp as strategy_bp
    from .advice import bp as advice_bp
    from .trade import bp as trade_bp
    from .monitor import bp as monitor_bp
    from .doubler import bp as doubler_bp
    from .review import bp as review_bp
    from .margin import bp as margin_bp
    from .alert import bp as alert_bp
    from .a_stock_cache import bp as a_stock_cache_bp
    from .plan_1w import bp as plan_1w_bp

    app.register_blueprint(market_bp)
    app.register_blueprint(strategy_bp)
    app.register_blueprint(advice_bp)
    app.register_blueprint(trade_bp)
    app.register_blueprint(monitor_bp)
    app.register_blueprint(doubler_bp)
    app.register_blueprint(review_bp)
    app.register_blueprint(margin_bp)
    app.register_blueprint(alert_bp)
    app.register_blueprint(a_stock_cache_bp)
    app.register_blueprint(plan_1w_bp)

    # ─── 海淘美股模块 (7个蓝图，对齐Shimi分层架构) ──────
    try:
        from haitao.us_market import bp as haitao_market_bp
        from haitao.us_scan import bp as haitao_scan_bp
        from haitao.us_trade import bp as haitao_trade_bp
        from haitao.us_review_bp import bp as haitao_review_bp
        from haitao.us_doubler import bp as haitao_doubler_bp
        from haitao.us_short import bp as haitao_short_bp
        from haitao.us_leveraged import bp as haitao_leveraged_bp

        app.register_blueprint(haitao_market_bp)
        app.register_blueprint(haitao_scan_bp)
        app.register_blueprint(haitao_trade_bp)
        app.register_blueprint(haitao_review_bp)
        app.register_blueprint(haitao_doubler_bp)
        app.register_blueprint(haitao_short_bp)
        app.register_blueprint(haitao_leveraged_bp)
    except Exception as e:
        print(f"⚠️ 海淘模块注册失败: {e}")
