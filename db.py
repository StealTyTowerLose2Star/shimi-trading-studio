"""
拾米交易工作室 - 数据库模块 (SQLite)
用户管理 + 交易记录持久化
"""
import sqlite3
import hashlib
import os
import time
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "shimi.db")


def get_db():
    """获取数据库连接"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """初始化数据库表"""
    conn = get_db()
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

        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            date TEXT,
            code TEXT NOT NULL,
            name TEXT,
            direction TEXT CHECK(direction IN ('buy', 'sell')),
            entry_price REAL NOT NULL,
            qty INTEGER DEFAULT 100,
            exit_price REAL,
            stop_loss REAL,
            target_1 REAL,
            target_2 REAL,
            target_3 REAL,
            note TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS trade_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id INTEGER,
            user_id INTEGER,
            action TEXT,
            detail TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    conn.close()


def hash_password(password: str) -> str:
    """密码哈希"""
    return hashlib.sha256(password.encode()).hexdigest()


# ─── 用户管理 ──────────────────────────────────────────

def register_user(username: str, password: str, display_name: str = None) -> dict:
    """注册用户"""
    conn = get_db()
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
    finally:
        conn.close()


def login_user(username: str, password: str) -> dict:
    """登录，返回 token"""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id, username, display_name, role FROM users WHERE username=? AND password=?",
            (username, hash_password(password))
        ).fetchone()
        if not row:
            return {"error": "用户名或密码错误"}

        user = dict(row)

        # 生成 token (72h 有效期)
        import secrets
        token = secrets.token_hex(32)
        expires = time.time() + 72 * 3600
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
        row = conn.execute(
            "SELECT u.id, u.username, u.display_name, u.role FROM tokens t "
            "JOIN users u ON t.user_id = u.id "
            "WHERE t.token=? AND t.expires_at > ?",
            (token, time.time())
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_users() -> list:
    """列出所有用户"""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id, username, display_name, role FROM users ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ─── 交易记录 ──────────────────────────────────────────

def add_trade(user_id: int, data: dict) -> dict:
    """添加交易记录"""
    conn = get_db()
    try:
        cur = conn.execute(
            """INSERT INTO trades
               (user_id, date, code, name, direction, entry_price, qty,
                exit_price, stop_loss, target_1, target_2, target_3, note)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id,
             data.get("date", datetime.now().strftime("%Y-%m-%d")),
             data.get("code", ""),
             data.get("name", ""),
             data.get("direction", "buy"),
             data.get("entry_price", 0),
             data.get("qty", 100),
             data.get("exit_price"),
             data.get("stop_loss"),
             data.get("target_1"),
             data.get("target_2"),
             data.get("target_3"),
             data.get("note", ""))
        )
        conn.commit()
        trade_id = cur.lastrowid

        conn.execute(
            "INSERT INTO trade_log (trade_id, user_id, action, detail) VALUES (?, ?, 'create', ?)",
            (trade_id, user_id, f"开仓 {data.get('code','')} @¥{data.get('entry_price',0)}")
        )
        conn.commit()

        return {"id": trade_id, **data}
    finally:
        conn.close()


def update_trade(trade_id: int, user_id: int, data: dict) -> dict:
    """更新交易记录（含平仓）"""
    conn = get_db()
    try:
        # 验证归属
        trade = conn.execute(
            "SELECT * FROM trades WHERE id=? AND user_id=?", (trade_id, user_id)
        ).fetchone()
        if not trade:
            return {"error": "交易记录不存在或无权限"}

        fields = []
        values = []
        for key in ["date", "code", "name", "direction", "entry_price", "qty",
                     "exit_price", "stop_loss", "target_1", "target_2", "target_3", "note"]:
            if key in data:
                fields.append(f"{key}=?")
                values.append(data[key])

        if fields:
            fields.append("updated_at=CURRENT_TIMESTAMP")
            values.append(trade_id)
            conn.execute(
                f"UPDATE trades SET {', '.join(fields)} WHERE id=?",
                values
            )

            # 如果是平仓，记录日志
            if "exit_price" in data and data["exit_price"] is not None:
                pnl = (data["exit_price"] - trade["entry_price"]) * trade["qty"]
                conn.execute(
                    "INSERT INTO trade_log (trade_id, user_id, action, detail) VALUES (?, ?, 'close', ?)",
                    (trade_id, user_id, f"平仓 @¥{data['exit_price']} 盈亏:¥{pnl:.2f}")
                )

        conn.commit()
        return {"id": trade_id, **dict(trade), **data}
    finally:
        conn.close()


def delete_trade(trade_id: int, user_id: int) -> dict:
    """删除交易记录"""
    conn = get_db()
    try:
        trade = conn.execute(
            "SELECT * FROM trades WHERE id=? AND user_id=?", (trade_id, user_id)
        ).fetchone()
        if not trade:
            return {"error": "交易记录不存在或无权限"}
        conn.execute("DELETE FROM trades WHERE id=?", (trade_id,))
        conn.execute("DELETE FROM trade_log WHERE trade_id=?", (trade_id,))
        conn.commit()
        return {"deleted": trade_id}
    finally:
        conn.close()


def get_trades(user_id: int = None) -> list:
    """获取交易记录"""
    conn = get_db()
    try:
        if user_id:
            rows = conn.execute(
                "SELECT t.*, u.display_name as operator FROM trades t "
                "JOIN users u ON t.user_id = u.id "
                "WHERE t.user_id=? ORDER BY t.id DESC", (user_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT t.*, u.display_name as operator FROM trades t "
                "JOIN users u ON t.user_id = u.id ORDER BY t.id DESC"
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_trade_summary(user_id: int = None) -> dict:
    """交易统计摘要"""
    conn = get_db()
    try:
        if user_id:
            closed = conn.execute(
                "SELECT * FROM trades WHERE user_id=? AND exit_price IS NOT NULL",
                (user_id,)
            ).fetchall()
            open_trades = conn.execute(
                "SELECT * FROM trades WHERE user_id=? AND exit_price IS NULL",
                (user_id,)
            ).fetchall()
        else:
            closed = conn.execute(
                "SELECT * FROM trades WHERE exit_price IS NOT NULL"
            ).fetchall()
            open_trades = conn.execute(
                "SELECT * FROM trades WHERE exit_price IS NULL"
            ).fetchall()

        total_closed = len(closed)
        won = 0
        total_pnl = 0.0
        best = 0.0
        worst = 0.0

        for t in closed:
            pnl = (t["exit_price"] - t["entry_price"]) * t["qty"] if t["direction"] == "buy" else (t["entry_price"] - t["exit_price"]) * t["qty"]
            total_pnl += pnl
            if pnl > 0:
                won += 1
            if pnl > best:
                best = pnl
            if pnl < worst:
                worst = pnl

        win_rate = round(won / total_closed * 100, 1) if total_closed > 0 else 0

        return {
            "total_trades": total_closed + len(open_trades),
            "closed_trades": total_closed,
            "open_trades": len(open_trades),
            "won": won,
            "win_rate": win_rate,
            "total_pnl": round(total_pnl, 2),
            "best_trade": round(best, 2),
            "worst_trade": round(worst, 2),
        }
    finally:
        conn.close()


# ─── 初始化 ──────────────────────────────────────────

init_db()
