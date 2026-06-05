"""
拾米交易工作室 - 数据库：SQLite → PostgreSQL 数据迁移模块
"""
import os
from datetime import datetime

from db.core import get_db, _get_pg, _row_to_dict, config


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
