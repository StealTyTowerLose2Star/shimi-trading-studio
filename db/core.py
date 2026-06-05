"""
拾米交易工作室 - 数据库核心模块
双层引擎连接管理 + DDL 初始化
"""
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

CREATE TABLE IF NOT EXISTS recommendations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    name TEXT,
    price REAL,
    signal TEXT,
    consensus INTEGER DEFAULT 0,
    stop_loss REAL,
    target_1 REAL,
    target_2 REAL,
    target_3 REAL,
    position TEXT,
    strategies TEXT,
    reason TEXT,
    market_phase TEXT,
    generated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS review_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    review_type TEXT NOT NULL CHECK(review_type IN ('daily', 'weekly')),
    generated_at TEXT NOT NULL,
    period_start TEXT,
    period_end TEXT,
    content TEXT NOT NULL,
    summary TEXT
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

CREATE TABLE IF NOT EXISTS recommendations (
    id SERIAL PRIMARY KEY,
    code VARCHAR(20) NOT NULL,
    name VARCHAR(100),
    price DOUBLE PRECISION,
    signal VARCHAR(20),
    consensus INTEGER DEFAULT 0,
    stop_loss DOUBLE PRECISION,
    target_1 DOUBLE PRECISION,
    target_2 DOUBLE PRECISION,
    target_3 DOUBLE PRECISION,
    position VARCHAR(20),
    strategies VARCHAR(100),
    reason TEXT,
    market_phase VARCHAR(50),
    generated_at VARCHAR(20) NOT NULL
);

CREATE TABLE IF NOT EXISTS review_reports (
    id SERIAL PRIMARY KEY,
    review_type VARCHAR(10) NOT NULL CHECK(review_type IN ('daily', 'weekly')),
    generated_at VARCHAR(20) NOT NULL,
    period_start VARCHAR(20),
    period_end VARCHAR(20),
    content TEXT NOT NULL,
    summary TEXT
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
