"""Local-only accounts. Does not enable trading or unsafe password recovery."""
from flask import request, jsonify
from db.users_db import register_user, login_user, verify_token, logout_token


def install_local_accounts(app, port):
    origins = {f'http://127.0.0.1:{port}', f'http://localhost:{port}'}

    @app.before_request
    def account_boundary():
        if not request.path.startswith('/api/auth/'):
            return None
        if request.host not in {f'127.0.0.1:{port}', f'localhost:{port}'}:
            return jsonify(error='账号仅支持本机访问'), 403
        if request.headers.get('Origin') and request.headers['Origin'] not in origins:
            return jsonify(error='拒绝跨站账号请求'), 403
        if request.content_length and request.content_length > 16384:
            return jsonify(error='请求过大'), 413
        if request.method == 'POST' and not request.is_json:
            return jsonify(error='请使用JSON提交账号信息'), 415

    def payload():
        data = request.get_json(silent=True)
        return data if isinstance(data, dict) else None

    def bearer():
        value = request.headers.get('Authorization', '')
        return value[7:] if value.startswith('Bearer ') else ''

    def register():
        data = payload()
        if data is None:
            return jsonify(error='账号请求格式错误'), 400
        result = register_user(data.get('username'), data.get('password'), data.get('display_name'))
        return jsonify(result), 400 if 'error' in result else 201

    def login():
        data = payload()
        if data is None:
            return jsonify(error='账号请求格式错误'), 400
        result = login_user(data.get('username'), data.get('password'))
        return jsonify(result), 401 if 'error' in result else 200

    def me():
        user = verify_token(bearer())
        return (jsonify(user=user), 200) if user else (jsonify(error='未登录或登录已过期'), 401)

    def logout():
        # Idempotent: also succeeds for a missing/already-revoked session.
        token = bearer()
        if token:
            logout_token(token)
        return jsonify(ok=True)

    # Replace only existing account handlers, not the trade blueprint.
    app.view_functions['trade.api_register'] = register
    app.view_functions['trade.api_login'] = login
    app.view_functions['trade.api_me'] = me
    app.add_url_rule('/api/auth/logout', 'local_account_logout', logout, methods=['POST'])
