"""
拾米交易工作室 - 数据库模块 (SQLite + PostgreSQL 双引擎)
用户管理 + 交易记录持久化
通过 config.py 切换数据库类型
"""
import hashlib
import os
import time
import secrets
from datetime import datetime

import config

# ─── 数据库连接 ──────────────────────────────────────────

def get_db():
    """获取数据库连接（自动选择 SQLite 或 PostgreSQL）"""
    if config.DB_TYPE == "postgresql":
        return _get_pg()
    return _get_sqlite()


def _get_sqlite():
    import sqlite3
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _get_pg():
    """PostgreSQL 连接"""
    import psycopg2
    import psycopg2.extras
    conn = psycopg2.connect(
        host=config.DB_HOST,
        port=config.DB_PORT,
        dbname=config.DB_NAME,
        user=config.DB_USER,
        password=config.DB_PASS,
    )
    conn.autocommit = False
    # Wrap to provide .execute() and .executescript() like SQLite
    class PGConnection:
        def __init__(self, c):
            self._conn = c
        def _cur(self):
            return self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        def execute(self, sql, params=None):
            cur = self._cur()
            if params is None:
                cur.execute(sql)
            else:
                cur.execute(sql, params)
            return cur
        def executescript(self, sql):
            cur = self._cur()
            for stmt in sql.split(";"):
                s = stmt.strip()
                if s:
                    cur.execute(s)
            return cur
        def commit(self):
            self._conn.commit()
        def rollback(self):
            self._conn.rollback()
        def close(self):
            self._conn.close()
    return PGConnection(conn)


def _row_to_dict(row):
    """将数据库行转为普通 dict"""
    if row is None:
        return None
    return dict(row)


def _rows_to_list(rows):
    """将数据库行列表转为普通 dict 列表"""
    return [dict(r) for r in rows]


# ─── PLACEHOLDER 适配 ────────────────────────────────────
def _ph():
    """返回占位符：SQLite 用 ?，PostgreSQL 用 %s"""
    return "%s" if config.DB_TYPE == "postgresql" else "?"


def _last_id_suffix():
    """返回获取最后插入ID的语法"""
    if config.DB_TYPE == "postgresql":
        return " RETURNING id"
    return ""


# ─── DDL ─────────────────────────────────────────────────

SCHEMA_SQLITE = """
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
"""

SCHEMA_PG = """
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(100) UNIQUE NOT NULL,
    password VARCHAR(100) NOT NULL,
    display_name VARCHAR(100) NOT NULL,
    role VARCHAR(20) DEFAULT 'trader',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tokens (
    token VARCHAR(100) PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    expires_at DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS trades (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    date VARCHAR(20),
    code VARCHAR(20) NOT NULL,
    name VARCHAR(100),
    direction VARCHAR(10) CHECK(direction IN ('buy', 'sell')),
    entry_price DOUBLE PRECISION NOT NULL,
    qty INTEGER DEFAULT 100,
    exit_price DOUBLE PRECISION,
    stop_loss DOUBLE PRECISION,
    target_1 DOUBLE PRECISION,
    target_2 DOUBLE PRECISION,
    target_3 DOUBLE PRECISION,
    note TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS trade_log (
    id SERIAL PRIMARY KEY,
    trade_id INTEGER,
    user_id INTEGER,
    action VARCHAR(50),
    detail TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_trades_user ON trades(user_id);
CREATE INDEX IF NOT EXISTS idx_tokens_user ON tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_trade_log_trade ON trade_log(trade_id);
"""


def init_db():
    """初始化数据库表"""
    conn = get_db()
    try:
        if config.DB_TYPE == "postgresql":
            conn.execute(SCHEMA_PG)
        else:
            conn.executescript(SCHEMA_SQLITE)
        conn.commit()
    finally:
        conn.close()


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


# ─── 交易记录 ──────────────────────────────────────────

def add_trade(user_id: int, data: dict) -> dict:
    """添加交易记录"""
    conn = get_db()
    try:
        cur = conn.execute(
            f"""INSERT INTO trades
               (user_id, date, code, name, direction, entry_price, qty,
                exit_price, stop_loss, target_1, target_2, target_3, note)
               VALUES ({_ph()}, {_ph()}, {_ph()}, {_ph()}, {_ph()}, {_ph()}, {_ph()},
                       {_ph()}, {_ph()}, {_ph()}, {_ph()}, {_ph()}, {_ph()}){_last_id_suffix()}""",
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

        if config.DB_TYPE == "postgresql":
            trade_id = cur.fetchone()[0]
        else:
            trade_id = cur.lastrowid

        conn.execute(
            f"INSERT INTO trade_log (trade_id, user_id, action, detail) VALUES ({_ph()}, {_ph()}, 'create', {_ph()})",
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
        cur = conn.execute(
            f"SELECT * FROM trades WHERE id={_ph()} AND user_id={_ph()}",
            (trade_id, user_id)
        )
        trade = cur.fetchone()
        if not trade:
            return {"error": "交易记录不存在或无权限"}

        trade_dict = _row_to_dict(trade)
        fields = []
        values = []
        for key in ["date", "code", "name", "direction", "entry_price", "qty",
                     "exit_price", "stop_loss", "target_1", "target_2", "target_3", "note"]:
            if key in data:
                fields.append(f"{key}={_ph()}")
                values.append(data[key])

        if fields:
            fields.append("updated_at=CURRENT_TIMESTAMP")
            values.append(trade_id)
            conn.execute(
                f"UPDATE trades SET {', '.join(fields)} WHERE id={_ph()}",
                values
            )

            # 如果是平仓，记录日志
            if "exit_price" in data and data["exit_price"] is not None:
                pnl = (data["exit_price"] - trade_dict["entry_price"]) * trade_dict["qty"] \
                    if trade_dict["direction"] == "buy" \
                    else (trade_dict["entry_price"] - data["exit_price"]) * trade_dict["qty"]
                conn.execute(
                    f"INSERT INTO trade_log (trade_id, user_id, action, detail) VALUES ({_ph()}, {_ph()}, 'close', {_ph()})",
                    (trade_id, user_id, f"平仓 @¥{data['exit_price']} 盈亏:¥{pnl:.2f}")
                )

        conn.commit()
        return {"id": trade_id, **trade_dict, **data}
    finally:
        conn.close()


def delete_trade(trade_id: int, user_id: int) -> dict:
    """删除交易记录"""
    conn = get_db()
    try:
        cur = conn.execute(
            f"SELECT * FROM trades WHERE id={_ph()} AND user_id={_ph()}",
            (trade_id, user_id)
        )
        if not cur.fetchone():
            return {"error": "交易记录不存在或无权限"}
        conn.execute(f"DELETE FROM trades WHERE id={_ph()}", (trade_id,))
        conn.execute(f"DELETE FROM trade_log WHERE trade_id={_ph()}", (trade_id,))
        conn.commit()
        return {"deleted": trade_id}
    finally:
        conn.close()


def get_trades(user_id: int = None) -> list:
    """获取交易记录"""
    conn = get_db()
    try:
        if user_id:
            cur = conn.execute(
                f"SELECT t.*, u.display_name as operator FROM trades t "
                f"JOIN users u ON t.user_id = u.id "
                f"WHERE t.user_id={_ph()} ORDER BY t.id DESC",
                (user_id,)
            )
        else:
            cur = conn.execute(
                "SELECT t.*, u.display_name as operator FROM trades t "
                "JOIN users u ON t.user_id = u.id ORDER BY t.id DESC"
            )
        return _rows_to_list(cur.fetchall())
    finally:
        conn.close()


def get_trade_summary(user_id: int = None) -> dict:
    """交易统计摘要（含持仓浮动盈亏）"""
    conn = get_db()
    try:
        if user_id:
            closed = conn.execute(
                f"SELECT * FROM trades WHERE user_id={_ph()} AND exit_price IS NOT NULL",
                (user_id,)
            ).fetchall()
            open_trades = conn.execute(
                f"SELECT * FROM trades WHERE user_id={_ph()} AND exit_price IS NULL",
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
        total_pnl = 0.0       # 已平仓盈亏
        unrealized_pnl = 0.0  # 持仓浮动盈亏
        best = 0.0
        worst = 0.0

        # 已平仓统计
        for t in closed:
            t = dict(t)
            pnl = (t["exit_price"] - t["entry_price"]) * t["qty"] if t["direction"] == "buy" \
                  else (t["entry_price"] - t["exit_price"]) * t["qty"]
            total_pnl += pnl
            if pnl > 0:
                won += 1
            if pnl > best:
                best = pnl
            if pnl < worst:
                worst = pnl

        win_rate = round(won / total_closed * 100, 1) if total_closed > 0 else 0

        # 持仓浮动盈亏 — 尝试查询最新价
        for t in open_trades:
            t = dict(t)
            try:
                from position_manager import get_kline
                df = get_kline(t["code"], days=10)
                if df is not None and len(df) > 0:
                    current = float(df["close"].iloc[-1])
                    pnl = (current - t["entry_price"]) * t["qty"] if t["direction"] == "buy" \
                          else (t["entry_price"] - current) * t["qty"]
                    unrealized_pnl += pnl
            except:
                pass

        total_pnl_all = total_pnl + unrealized_pnl

        return {
            "total_trades": total_closed + len(open_trades),
            "closed_trades": total_closed,
            "open_trades": len(open_trades),
            "won": won,
            "win_rate": win_rate,
            "total_pnl": round(total_pnl, 2),
            "unrealized_pnl": round(unrealized_pnl, 2),
            "total_pnl_with_unrealized": round(total_pnl_all, 2),
            "best_trade": round(best, 2),
            "worst_trade": round(worst, 2),
        }
    finally:
        conn.close()


# ─── 数据迁移 ──────────────────────────────────────────

def migrate_from_sqlite():
    """将 SQLite 数据迁移到 PostgreSQL"""
    if config.DB_TYPE != "postgresql":
        print("⚠️  非 PostgreSQL 模式，跳过迁移")
        return

    import sqlite3

    sqlite_path = config.DB_PATH
    if not os.path.exists(sqlite_path):
        print("ℹ️  SQLite 数据库不存在，无需迁移")
        return

    sq = sqlite3.connect(sqlite_path)
    sq.row_factory = sqlite3.Row

    pg_conn = _get_pg()

    try:
        # 迁移 users
        users = sq.execute("SELECT * FROM users").fetchall()
        for u in users:
            u = dict(u)
            try:
                pg_conn.execute(
                    "INSERT INTO users (id, username, password, display_name, role, created_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
                    (u["id"], u["username"], u["password"], u["display_name"],
                     u.get("role", "trader"), u.get("created_at", datetime.now()))
                )
            except Exception as e:
                print(f"  ⚠️  用户 {u['username']} 跳过: {e}")
        pg_conn.commit()
        print(f"  ✅ 迁移 users: {len(users)} 条")

        # 迁移 tokens
        tokens = sq.execute("SELECT * FROM tokens").fetchall()
        for t in tokens:
            t = dict(t)
            try:
                pg_conn.execute(
                    "INSERT INTO tokens (token, user_id, expires_at) "
                    "VALUES (%s, %s, %s) ON CONFLICT (token) DO NOTHING",
                    (t["token"], t["user_id"], t["expires_at"])
                )
            except Exception as e:
                print(f"  ⚠️  token {t['token'][:8]}... 跳过: {e}")
        pg_conn.commit()
        print(f"  ✅ 迁移 tokens: {len(tokens)} 条")

        # 迁移 trades
        trades = sq.execute("SELECT * FROM trades").fetchall()
        for t in trades:
            t = dict(t)
            try:
                pg_conn.execute(
                    "INSERT INTO trades (id, user_id, date, code, name, direction, "
                    "entry_price, qty, exit_price, stop_loss, target_1, target_2, target_3, "
                    "note, created_at, updated_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (id) DO NOTHING",
                    (t["id"], t["user_id"], t.get("date"), t["code"], t.get("name"),
                     t["direction"], t["entry_price"], t["qty"], t.get("exit_price"),
                     t.get("stop_loss"), t.get("target_1"), t.get("target_2"), t.get("target_3"),
                     t.get("note"), t.get("created_at"), t.get("updated_at"))
                )
            except Exception as e:
                print(f"  ⚠️  trade {t['id']} 跳过: {e}")
        pg_conn.commit()
        print(f"  ✅ 迁移 trades: {len(trades)} 条")

        # 迁移 trade_log
        logs = sq.execute("SELECT * FROM trade_log").fetchall()
        for l in logs:
            l = dict(l)
            try:
                pg_conn.execute(
                    "INSERT INTO trade_log (id, trade_id, user_id, action, detail, created_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING",
                    (l["id"], l["trade_id"], l["user_id"], l["action"], l["detail"], l.get("created_at"))
                )
            except Exception as e:
                print(f"  ⚠️  log {l['id']} 跳过: {e}")
        pg_conn.commit()
        print(f"  ✅ 迁移 trade_log: {len(logs)} 条")

        print("🎉 SQLite → PostgreSQL 迁移完成！")

    except Exception as e:
        print(f"❌ 迁移失败: {e}")
        pg_conn.rollback()
    finally:
        sq.close()
        pg_conn.close()


# ─── 初始化 ──────────────────────────────────────────
init_db()
