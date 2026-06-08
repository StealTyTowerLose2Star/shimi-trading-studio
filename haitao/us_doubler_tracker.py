"""
Magician 美股翻倍跟踪闭环 (US Doubler Tracker)

闭环三阶段 (匹配A股 doubler_tracker.py):
   启动跟踪(T+0): save_recommendation() — 保存当月推荐
   跟踪更新(T+1~T+N): update_prices() — 每日更新价格
   月末验证(T+末): verify_month() — 对比预测 vs 实际

表: us_doubler_tracking (SQLite)
"""
import os
import json
import sqlite3
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from haitao.us_fetcher import get_quotes, get_history

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "shimi.db")


def _get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_tracking_table():
    """创建美股翻倍跟踪表 (幂等)"""
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS us_doubler_tracking (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            month TEXT NOT NULL,             -- 202606
            recommend_date TEXT NOT NULL,     -- 推荐日期
            ticker TEXT NOT NULL,
            score REAL,
            rating TEXT,
            entry_price REAL NOT NULL,
            current_price REAL DEFAULT 0,     -- 每日更新
            peak_price REAL DEFAULT 0,
            max_gain_pct REAL DEFAULT 0,
            final_price REAL,
            final_gain_pct REAL,
            doubled INTEGER DEFAULT 0,
            D0_mode TEXT,
            catalysts TEXT,                   -- JSON
            verified INTEGER DEFAULT 0,
            verified_date TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(month, ticker)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS us_doubler_pnl_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tracking_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            price REAL NOT NULL,
            gain_pct REAL NOT NULL,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (tracking_id) REFERENCES us_doubler_tracking(id)
        )
    """)
    conn.commit()
    conn.close()


def save_recommendation(recommend: dict) -> dict:
    """保存当月推荐到跟踪表

    Args:
        recommend: recommend_doublers() 的输出
    """
    init_tracking_table()
    conn = _get_conn()

    month = datetime.now().strftime("%Y%m")
    today = datetime.now().strftime("%Y-%m-%d")
    saved = 0

    elite = recommend.get("elite_picks", [])
    watch = recommend.get("watch_list", [])

    for item in elite + watch:
        ticker = item["ticker"]
        score = item.get("score", 0)
        rating = item.get("rating", "")
        price = item.get("current_price", 0)

        try:
            conn.execute("""
                INSERT OR IGNORE INTO us_doubler_tracking
                    (month, recommend_date, ticker, score, rating, entry_price, D0_mode, catalysts)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                month, today, ticker, score, rating, price,
                item.get("D0_mode", ""),
                json.dumps(item.get("catalysts", {}), ensure_ascii=False),
            ))
            saved += 1
        except Exception as e:
            logger.warning("保存 %s 跟踪记录失败: %s", ticker, e)

    conn.commit()
    conn.close()
    return {"month": month, "saved": saved, "total": len(elite) + len(watch)}


def update_prices(tickers: List[str] = None) -> dict:
    """批量更新所有活跃跟踪的价格"""
    init_tracking_table()
    conn = _get_conn()
    month = datetime.now().strftime("%Y%m")

    # 获取本月未验证的跟踪记录
    rows = conn.execute("""
        SELECT id, ticker, entry_price, current_price, peak_price
        FROM us_doubler_tracking
        WHERE month=? AND verified=0
    """, (month,)).fetchall()

    if not rows:
        conn.close()
        return {"updated": 0, "message": "无活跃跟踪"}

    # 批量获取报价
    track_tickers = [r["ticker"] for r in rows]
    quotes = get_quotes(track_tickers)
    quote_map = {}
    if isinstance(quotes, list):
        for q in quotes:
            if isinstance(q, dict) and "ticker" in q:
                quote_map[q["ticker"].upper()] = q

    updated = 0
    today = datetime.now().strftime("%Y-%m-%d")
    for row in rows:
        ticker = row["ticker"]
        q = quote_map.get(ticker.upper())
        if not q:
            continue

        price = q.get("price") or q.get("c")
        if price is None or price == 0:
            continue

        price = float(price)
        entry = float(row["entry_price"])
        gain_pct = round((price / entry - 1) * 100, 2) if entry > 0 else 0
        peak = max(float(row["peak_price"]), price)
        max_gain = round((peak / entry - 1) * 100, 2) if entry > 0 else 0

        conn.execute("""
            UPDATE us_doubler_tracking
            SET current_price=?, peak_price=?, max_gain_pct=?,
                doubled=CASE WHEN max_gain_pct>=100 THEN 1 ELSE doubled END
            WHERE id=?
        """, (price, peak, max_gain, row["id"]))

        # 日志
        conn.execute("""
            INSERT INTO us_doubler_pnl_log (tracking_id, date, price, gain_pct)
            VALUES (?, ?, ?, ?)
        """, (row["id"], today, price, gain_pct))
        updated += 1

    conn.commit()
    conn.close()
    return {"updated": updated, "total": len(rows), "date": today}


def get_tracking_status() -> dict:
    """获取当前跟踪状态"""
    init_tracking_table()
    conn = _get_conn()
    month = datetime.now().strftime("%Y%m")

    rows = conn.execute("""
        SELECT * FROM us_doubler_tracking
        WHERE month=? AND verified=0
        ORDER BY score DESC
    """, (month,)).fetchall()

    active = []
    for r in rows:
        entry = float(r["entry_price"])
        current = float(r["current_price"])
        gain = round((current / entry - 1) * 100, 2) if entry > 0 else 0
        active.append({
            "id": r["id"],
            "ticker": r["ticker"],
            "score": r["score"],
            "rating": r["rating"],
            "entry_price": entry,
            "current_price": current,
            "gain_pct": gain,
            "peak_price": r["peak_price"],
            "max_gain_pct": r["max_gain_pct"],
            "D0_mode": r["D0_mode"],
            "recommend_date": r["recommend_date"],
        })

    # 统计
    total = len(active)
    winners = sum(1 for a in active if a["gain_pct"] > 0)
    losers = sum(1 for a in active if a["gain_pct"] < 0)

    conn.close()
    return {
        "month": month,
        "date": datetime.now().strftime("%Y-%m-%d"),
        "active": active,
        "summary": {
            "total": total,
            "winners": winners,
            "losers": losers,
            "avg_gain": round(sum(a["gain_pct"] for a in active) / total, 2) if total > 0 else 0,
            "max_gain": max((a["max_gain_pct"] for a in active), default=0),
        },
    }


def verify_month(month: str = None) -> dict:
    """月末验证: 计算实际涨跌幅"""
    if not month:
        month = datetime.now().strftime("%Y%m")

    init_tracking_table()
    conn = _get_conn()

    rows = conn.execute("""
        SELECT * FROM us_doubler_tracking
        WHERE month=? AND verified=0
    """, (month,)).fetchall()

    verified_count = 0
    for r in rows:
        # 获取月份最后一个交易日价格
        try:
            df = get_history(r["ticker"], days=35)
            if df is not None and len(df) > 0:
                close = float(df["Close"].values[-1])
                entry = float(r["entry_price"])
                gain = round((close / entry - 1) * 100, 2) if entry > 0 else 0
                doubled = 1 if gain >= 100 else 0

                conn.execute("""
                    UPDATE us_doubler_tracking
                    SET final_price=?, final_gain_pct=?, doubled=?,
                        verified=1, verified_date=?
                    WHERE id=?
                """, (close, gain, doubled, datetime.now().strftime("%Y-%m-%d"), r["id"]))
                verified_count += 1
        except Exception as e:
            logger.warning("验证 %s 失败: %s", r["ticker"], e)

    conn.commit()
    conn.close()

    # 统计
    report = get_monthly_report(month)
    return {
        "month": month,
        "verified": verified_count,
        "total": len(rows),
        "report": report,
    }


def get_monthly_report(month: str = None) -> dict:
    """获取月度验证报告"""
    if not month:
        month = datetime.now().strftime("%Y%m")

    init_tracking_table()
    conn = _get_conn()

    rows = conn.execute("""
        SELECT * FROM us_doubler_tracking
        WHERE month=?
        ORDER BY score DESC
    """, (month,)).fetchall()

    results = []
    for r in rows:
        entry = float(r["entry_price"])
        final = float(r["final_price"]) if r["final_price"] else 0
        current = float(r["current_price"]) if r["current_price"] else 0
        gain = round((final / entry - 1) * 100, 2) if final > 0 else (
            round((current / entry - 1) * 100, 2) if current > 0 else 0
        )

        results.append({
            "ticker": r["ticker"],
            "score": r["score"],
            "rating": r["rating"],
            "entry_price": entry,
            "final_price": final or current,
            "gain_pct": gain,
            "max_gain_pct": r["max_gain_pct"],
            "doubled": bool(r["doubled"]),
            "D0_mode": r["D0_mode"],
            "verified": bool(r["verified"]),
        })

    # 统计
    total = len(results)
    verified_count = sum(1 for r in results if r["verified"])
    doubled_count = sum(1 for r in results if r["doubled"])
    gains = [r["gain_pct"] for r in results if r["gain_pct"] != 0]

    conn.close()
    return {
        "month": month,
        "total_picks": total,
        "verified_count": verified_count,
        "doubled_count": doubled_count,
        "doubled_rate": round(doubled_count / total * 100, 1) if total > 0 else 0,
        "avg_gain": round(sum(gains) / len(gains), 2) if gains else 0,
        "max_gain": max(gains) if gains else 0,
        "win_rate": round(sum(1 for g in gains if g > 0) / len(gains) * 100, 1) if gains else 0,
        "picks": results,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


def clear_tracking(month: str = None):
    """清除指定月份跟踪数据"""
    import logging
    logger = logging.getLogger(__name__)
    if not month:
        month = datetime.now().strftime("%Y%m")
    conn = _get_conn()
    conn.execute("DELETE FROM us_doubler_pnl_log WHERE tracking_id IN "
                 "(SELECT id FROM us_doubler_tracking WHERE month=?)", (month,))
    conn.execute("DELETE FROM us_doubler_tracking WHERE month=?", (month,))
    conn.commit()
    conn.close()
    logger.info("已清除 %s 跟踪数据", month)
