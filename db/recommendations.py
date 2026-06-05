"""
拾米交易工作室 - 数据库：推荐 & 复盘报告模块
"""
from datetime import datetime

from db.core import get_db, _row_to_dict, _rows_to_list, _last_id_suffix, _ph, config


# ─── 推荐记录 ────────────────────────────────────────

def save_recommendations(recommendations: list, market_phase: str, generated_at: str):
    """保存操作建议生成的推荐股票记录"""
    conn = get_db()
    try:
        for rec in recommendations:
            conn.execute(
                f"INSERT INTO recommendations (code, name, price, signal, consensus, "
                f"stop_loss, target_1, target_2, target_3, position, strategies, reason, "
                f"market_phase, generated_at) "
                f"VALUES ({_ph()},{_ph()},{_ph()},{_ph()},{_ph()},"
                f"{_ph()},{_ph()},{_ph()},{_ph()},{_ph()},"
                f"{_ph()},{_ph()},{_ph()},{_ph()})",
                (
                    rec.get("code"), rec.get("name"), rec.get("price"),
                    rec.get("signal"), rec.get("consensus", 0),
                    rec.get("stop_loss"), rec.get("target_1"),
                    rec.get("target_2"), rec.get("target_3"),
                    rec.get("position"),
                    ",".join(rec.get("strategies", [])),
                    rec.get("reason"), market_phase, generated_at,
                )
            )
        conn.commit()
    finally:
        conn.close()


def get_recommendations(days_ago: int = 3, limit: int = 50) -> list:
    """获取 N 天前的推荐记录"""
    conn = get_db()
    try:
        from datetime import datetime, timedelta
        target_date = (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d")
        cur = conn.execute(
            f"SELECT * FROM recommendations WHERE generated_at LIKE {_ph()} ORDER BY generated_at DESC LIMIT {_ph()}",
            (f"{target_date}%", limit)
        )
        return _rows_to_list(cur.fetchall())
    finally:
        conn.close()


def get_all_recommendations(limit: int = 100) -> list:
    """获取所有推荐记录"""
    conn = get_db()
    try:
        cur = conn.execute(
            f"SELECT * FROM recommendations ORDER BY id DESC LIMIT {_ph()}",
            (limit,)
        )
        return _rows_to_list(cur.fetchall())
    finally:
        conn.close()


# ─── 复盘报告 ────────────────────────────────────────

def save_review_report(review_type: str, content: dict, period_start: str = None,
                       period_end: str = None, summary: str = None):
    """保存复盘报告"""
    conn = get_db()
    try:
        import json
        cur = conn.execute(
            f"INSERT INTO review_reports (review_type, generated_at, period_start, period_end, content, summary) "
            f"VALUES ({_ph()}, {_ph()}, {_ph()}, {_ph()}, {_ph()}, {_ph()}){_last_id_suffix()}",
            (review_type, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
             period_start, period_end, json.dumps(content, ensure_ascii=False, default=str), summary)
        )
        conn.commit()
        if config.DB_TYPE == "postgresql":
            return cur.fetchone()[0]
        return cur.lastrowid
    finally:
        conn.close()


def get_latest_review(review_type: str) -> dict:
    """获取最新一份复盘报告"""
    conn = get_db()
    try:
        cur = conn.execute(
            f"SELECT * FROM review_reports WHERE review_type={_ph()} ORDER BY id DESC LIMIT 1",
            (review_type,)
        )
        row = cur.fetchone()
        if row:
            d = _row_to_dict(row)
            import json
            d["content"] = json.loads(d["content"]) if isinstance(d["content"], str) else d["content"]
            return d
        return None
    finally:
        conn.close()


def get_review_history(review_type: str, limit: int = 10) -> list:
    """获取复盘历史"""
    conn = get_db()
    try:
        cur = conn.execute(
            f"SELECT id, review_type, generated_at, period_start, period_end, summary "
            f"FROM review_reports WHERE review_type={_ph()} ORDER BY id DESC LIMIT {_ph()}",
            (review_type, limit)
        )
        return _rows_to_list(cur.fetchall())
    finally:
        conn.close()
