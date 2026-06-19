"""
拾米交易工作室 - 独立用户数据库 (users.db)
与主业务数据库 (shimi.db) 分离，保障账号安全性
"""
import sqlite3
import os
import hashlib
import secrets
import time

# ─── 独立数据库路径 ──────────────────────────
USER_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "users.db"
)

os.makedirs(os.path.dirname(USER_DB_PATH), exist_ok=True)

# ─── 连接管理 ──────────────────────────────────

def get_users_db():
    """获取用户数据库连接"""
    conn = sqlite3.connect(USER_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_users_db():
    """初始化用户数据库 DDL"""
    conn = get_users_db()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                display_name TEXT NOT NULL,
                role TEXT DEFAULT 'trader',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS tokens (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                expires_at REAL NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE INDEX IF NOT EXISTS idx_tokens_user ON tokens(user_id);
        """)
        conn.commit()
    finally:
        conn.close()


# ─── 密码 ────────────────────────────────────

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


# ─── 用户管理 ────────────────────────────────

def register_user(username: str, password: str, display_name: str = None) -> dict:
    conn = get_users_db()
    try:
        conn.execute(
            "INSERT INTO users (username, password, display_name) VALUES (?, ?, ?)",
            (username, hash_password(password), display_name or username)
        )
        conn.commit()
        uid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        return {"id": uid, "username": username, "display_name": display_name or username}
    except sqlite3.IntegrityError:
        return {"error": "用户名已存在"}
    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()


def login_user(username: str, password: str) -> dict:
    conn = get_users_db()
    try:
        cur = conn.execute(
            "SELECT id, username, display_name, role FROM users WHERE username=? AND password=?",
            (username, hash_password(password))
        )
        row = cur.fetchone()
        if not row:
            return {"error": "用户名或密码错误"}

        user = dict(row)
        token = secrets.token_hex(32)
        expires = time.time() + 72 * 3600  # 72h

        conn.execute(
            "INSERT OR REPLACE INTO tokens (token, user_id, expires_at) VALUES (?, ?, ?)",
            (token, user["id"], expires)
        )
        conn.commit()
        return {"token": token, "user": user, "expires_at": expires}
    finally:
        conn.close()


def verify_token(token: str) -> dict:
    if not token:
        return None
    conn = get_users_db()
    try:
        cur = conn.execute(
            "SELECT u.id, u.username, u.display_name, u.role "
            "FROM tokens t JOIN users u ON t.user_id = u.id "
            "WHERE t.token=? AND t.expires_at > ?",
            (token, time.time())
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_users() -> list:
    conn = get_users_db()
    try:
        cur = conn.execute("SELECT id, username, display_name, role FROM users ORDER BY id")
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


# 模块加载时自动初始化
init_users_db()
