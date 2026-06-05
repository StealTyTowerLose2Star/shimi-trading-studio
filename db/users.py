"""
拾米交易工作室 - 数据库：用户管理模块
注册 / 登录 / Token验证 / 用户列表
"""
import hashlib
import secrets
import time

from db.core import get_db, _row_to_dict, _rows_to_list, _last_id_suffix, _ph, config


# ─── 密码 ────────────────────────────────────────────────

def hash_password(password: str) -> str:
    """密码哈希"""
    return hashlib.sha256(password.encode()).hexdigest()


# ─── 用户管理 ──────────────────────────────────────────

def register_user(username: str, password: str, display_name: str = None) -> dict:
    """注册用户"""
    conn = get_db()
    try:
        cur = conn.execute(
            f"INSERT INTO users (username, password, display_name) VALUES ({_ph()}, {_ph()}, {_ph()}){_last_id_suffix()}",
            (username, hash_password(password), display_name or username)
        )
        conn.commit()
        if config.DB_TYPE == "postgresql":
            uid = cur.fetchone()[0]
        else:
            uid = cur.lastrowid
        return {"id": uid, "username": username, "display_name": display_name or username}
    except Exception as e:
        err = str(e)
        if "duplicate" in err.lower() or "unique" in err.lower() or "IntegrityError" in err:
            return {"error": "用户名已存在"}
        return {"error": err}
    finally:
        conn.close()


def login_user(username: str, password: str) -> dict:
    """登录，返回 token"""
    conn = get_db()
    try:
        cur = conn.execute(
            f"SELECT id, username, display_name, role FROM users WHERE username={_ph()} AND password={_ph()}",
            (username, hash_password(password))
        )
        row = cur.fetchone()
        if not row:
            return {"error": "用户名或密码错误"}

        user = _row_to_dict(row)

        # 生成 token (72h 有效期)
        token = secrets.token_hex(32)
        expires = time.time() + config.TOKEN_EXPIRY_HOURS * 3600

        # UPSERT token
        if config.DB_TYPE == "postgresql":
            conn.execute(
                "INSERT INTO tokens (token, user_id, expires_at) VALUES (%s, %s, %s) "
                "ON CONFLICT (token) DO UPDATE SET user_id=EXCLUDED.user_id, expires_at=EXCLUDED.expires_at",
                (token, user["id"], expires)
            )
        else:
            conn.execute(
                "INSERT OR REPLACE INTO tokens (token, user_id, expires_at) VALUES (?, ?, ?)",
                (token, user["id"], expires)
            )
        conn.commit()

        return {"token": token, "user": user, "expires_at": expires}
    finally:
        conn.close()


def verify_token(token: str) -> dict:
    """验证 token，返回用户信息"""
    if not token:
        return None
    conn = get_db()
    try:
        cur = conn.execute(
            f"SELECT u.id, u.username, u.display_name, u.role FROM tokens t "
            f"JOIN users u ON t.user_id = u.id "
            f"WHERE t.token={_ph()} AND t.expires_at > {_ph()}",
            (token, time.time())
        )
        return _row_to_dict(cur.fetchone())
    finally:
        conn.close()


def list_users() -> list:
    """列出所有用户"""
    conn = get_db()
    try:
        cur = conn.execute("SELECT id, username, display_name, role FROM users ORDER BY id")
        return _rows_to_list(cur.fetchall())
    finally:
        conn.close()
