"""海淘美股 - DB 迁移：添加 market 字段到 trades 表
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.core import get_db, config


def migrate():
    conn = get_db()
    try:
        # Check if market column already exists
        if config.DB_TYPE == "postgresql":
            cur = conn.execute("SELECT column_name FROM information_schema.columns WHERE table_name='trades' AND column_name='market'")
            exists = cur.fetchone()
        else:
            cur = conn.execute("PRAGMA table_info(trades)")
            cols = [r[1] for r in cur.fetchall()]
            exists = "market" in cols

        if exists:
            print("✅ market 字段已存在")
        else:
            if config.DB_TYPE == "postgresql":
                conn.execute("ALTER TABLE trades ADD COLUMN market VARCHAR(10) DEFAULT 'ashare'")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_market ON trades(market)")
            else:
                conn.execute("ALTER TABLE trades ADD COLUMN market TEXT DEFAULT 'ashare'")
            conn.commit()
            print("✅ market 字段添加成功")

        # 添加 us_trades 表（美股专用冗余表，方便独立查询）
        if config.DB_TYPE == "postgresql":
            conn.execute("""
                CREATE TABLE IF NOT EXISTS us_trades (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    date VARCHAR(20),
                    ticker VARCHAR(20) NOT NULL,
                    name VARCHAR(200),
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
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_us_trades_user ON us_trades(user_id)")
        else:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS us_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    date TEXT,
                    ticker TEXT NOT NULL,
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
                )
            """)
        conn.commit()
        print("✅ us_trades 表创建成功")

    except Exception as e:
        print(f"❌ 迁移失败: {e}")
    finally:
        conn.close()


if __name__ == "__main__":
    migrate()
