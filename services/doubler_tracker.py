"""
拾米交易工作室 - 翻倍股闭环跟踪 (月内跟踪 + 月末验证 + 模型反馈)

闭环三阶段:
  月初(T+0): start_tracking()  — 保存当月推荐
  月内(T+1~T+N): update_progress() — 每日更新价格
  月末(T+末): verify_month() — 对比预测 vs 实际, 更新模型权重
"""
import sqlite3
import json
import os
from datetime import datetime, timedelta
from collections import defaultdict
from cache import cache_or_fetch, cache_delete


DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "shimi.db")


def _get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_tracking_table():
    """创建跟踪表 (幂等)"""
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS doubler_tracking (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            month TEXT NOT NULL,             -- 202606
            recommend_date TEXT NOT NULL,     -- 2026-06-03
            code TEXT NOT NULL,
            name TEXT NOT NULL,
            industry TEXT,
            entry_price REAL NOT NULL,
            base_score REAL,
            total_score REAL,
            catalyst_type TEXT,               -- C1/C2/C3/C5/C6/C7
            catalyst_d7 REAL,
            catalyst_d8 REAL,
            catalyst_d9 REAL,
            current_price REAL,               -- 每日更新
            peak_price REAL DEFAULT 0,        -- 月内最高
            max_gain_pct REAL DEFAULT 0,      -- 月内最大涨幅
            final_price REAL,                  -- 月末收盘价
            final_gain_pct REAL,              -- 月末涨跌幅
            doubled INTEGER DEFAULT 0,         -- 1=月内翻倍(≥100%)
            verified INTEGER DEFAULT 0,        -- 1=已验证
            verified_date TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(month, code)
        )
    """)
    # 催化剂效果统计表
    conn.execute("""
        CREATE TABLE IF NOT EXISTS catalyst_effectiveness (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            month TEXT NOT NULL,
            catalyst_type TEXT NOT NULL,       -- C1/C2/...
            picks_count INTEGER,               -- 该类型推荐数
            doubled_count INTEGER,             -- 该类型翻倍数
            avg_gain REAL,                     -- 平均涨幅
            weight_adjustment REAL DEFAULT 0,   -- 权重调整量
            updated_at TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(month, catalyst_type)
        )
    """)
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════
# 阶段1: 月初保存推荐
# ═══════════════════════════════════════════════
def start_tracking(month=None):
    """
    保存当月推荐到跟踪表, 启动月度跟踪

    如果已有本月记录, 覆盖更新(允许月初重新推荐)
    """
    from services.doubler_predictor import predict_monthly_doublers

    result = predict_monthly_doublers()
    picks = result.get("elite_picks", [])
    if not picks:
        return {"error": "no picks available", "status": "fail"}

    if month is None:
        month = datetime.now().strftime("%Y%m")

    today = datetime.now().strftime("%Y-%m-%d")

    conn = _get_conn()
    conn.execute("DELETE FROM doubler_tracking WHERE month=?", (month,))

    saved = 0
    for p in picks:
        conn.execute("""
            INSERT OR REPLACE INTO doubler_tracking
            (month, recommend_date, code, name, industry, entry_price,
             base_score, total_score, catalyst_type, catalyst_d7, catalyst_d8, catalyst_d9)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            month, today,
            p["code"], p["name"], p.get("industry", ""),
            p["close"],
            p.get("potential", 0),
            p.get("potential", 0),
            p.get("catalyst_type", ""),
            p.get("catalyst_d7", 0),
            0, 0,  # d8/d9 not in prediction model
        ))
        saved += 1

    conn.commit()
    conn.close()
    cache_delete("doubler_tracking_status")  # 清除缓存
    return {"status": "ok", "saved": saved, "month": month, "date": today}


# ═══════════════════════════════════════════════
# 阶段2: 月内每日更新
# ═══════════════════════════════════════════════
def update_progress():
    """
    更新所有在跟踪股票的当前价格和最大涨幅

    每日调用, 更新 current_price 和 peak_price
    """
    import tushare as ts
    import config

    conn = _get_conn()
    # 获取本月所有未验证的跟踪记录
    month = datetime.now().strftime("%Y%m")
    rows = conn.execute(
        "SELECT id, code, entry_price, peak_price FROM doubler_tracking "
        "WHERE month=? AND verified=0", (month,)
    ).fetchall()

    if not rows:
        conn.close()
        return {"updated": 0, "message": "no active tracking for this month"}

    pro = ts.pro_api(config.TUSHARE_TOKEN)
    updated = 0

    for row in rows:
        rid = row["id"]
        code = row["code"]
        entry = row["entry_price"]
        peak = row["peak_price"] or 0

        # 确定ts_code格式
        if code.startswith("6"):
            ts_code = f"{code}.SH"
        elif code.startswith(("0", "3")):
            ts_code = f"{code}.SZ"
        elif code.startswith("9"):
            ts_code = f"{code}.BJ"
        else:
            continue

        try:
            end = datetime.now().strftime("%Y%m%d")
            start = (datetime.now() - timedelta(days=3)).strftime("%Y%m%d")
            df = pro.daily(ts_code=ts_code, start_date=start, end_date=end,
                          fields="close")
            if df is not None and len(df) > 0:
                current = float(df.iloc[0]["close"])
                gain = (current / entry - 1) * 100
                new_peak = max(peak, current)

                conn.execute(
                    "UPDATE doubler_tracking SET current_price=?, peak_price=?, "
                    "max_gain_pct=? WHERE id=?",
                    (current, new_peak, round((new_peak / entry - 1) * 100, 1), rid)
                )
                updated += 1
        except:
            continue

    conn.commit()
    conn.close()
    cache_delete("doubler_tracking_status")
    return {"updated": updated, "month": month}


# ═══════════════════════════════════════════════
# 阶段2b: 获取跟踪状态(前端展示)
# ═══════════════════════════════════════════════
def get_tracking_status():
    """获取本月跟踪状态 (缓存30s)"""
    month = datetime.now().strftime("%Y%m")
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM doubler_tracking WHERE month=? ORDER BY total_score DESC",
        (month,)
    ).fetchall()
    conn.close()

    if not rows:
        return {"tracking": False, "month": month, "stocks": []}

    stocks = []
    total_gain = 0
    doubled_count = 0
    positive_count = 0

    for r in rows:
        current = r["current_price"] or r["entry_price"]
        gain = (current / r["entry_price"] - 1) * 100
        on_track = "🔥" if gain >= 50 else "📈" if gain >= 20 else "➡️" if gain >= 0 else "📉"

        if gain >= 100:
            doubled_count += 1
        if gain >= 0:
            positive_count += 1
        total_gain += gain

        stocks.append({
            "code": r["code"], "name": r["name"],
            "entry_price": r["entry_price"],
            "current_price": round(current, 2),
            "gain_pct": round(gain, 1),
            "peak_pct": round(r["max_gain_pct"], 1),
            "catalyst": r["catalyst_type"],
            "total_score": r["total_score"],
            "on_track": on_track,
        })

    n = len(stocks)
    return {
        "tracking": True,
        "month": month,
        "start_date": rows[0]["recommend_date"],
        "stock_count": n,
        "avg_gain": round(total_gain / n, 1),
        "doubled_count": doubled_count,
        "positive_count": positive_count,
        "stocks": stocks,
    }


# ═══════════════════════════════════════════════
# 阶段3: 月末验证 + 模型反馈
# ═══════════════════════════════════════════════
def verify_month(month=None):
    """
    月末验证: 获取本月推荐的实际表现, 更新催化剂权重

    流程:
      1. 获取本月跟踪的所有股票
      2. 用tushare获取月末收盘价
      3. 计算最终涨幅, 标记翻倍股
      4. 统计各催化剂类型的命中率
      5. 调整D7/D8权重
    """
    if month is None:
        # 默认验证上个月
        now = datetime.now()
        prev = now - timedelta(days=now.day + 1)
        month = prev.strftime("%Y%m")

    import tushare as ts
    import config

    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM doubler_tracking WHERE month=? AND verified=0",
        (month,)
    ).fetchall()

    if not rows:
        conn.close()
        return {"error": f"no unverified records for {month}"}

    pro = ts.pro_api(config.TUSHARE_TOKEN)
    verified_count = 0

    for row in rows:
        code = row["code"]
        if code.startswith("6"): ts_code = f"{code}.SH"
        elif code.startswith(("0","3")): ts_code = f"{code}.SZ"
        elif code.startswith("9"): ts_code = f"{code}.BJ"
        else: continue

        # 获取月末附近收盘价
        end_month = f"{month}31"
        try:
            df = pro.daily(ts_code=ts_code, start_date=f"{month}20", 
                          end_date=end_month, fields="trade_date,close")
            if df is not None and len(df) > 0:
                final_close = float(df.iloc[0]["close"])
                final_gain = (final_close / row["entry_price"] - 1) * 100
                doubled = 1 if final_gain >= 100 else 0

                conn.execute(
                    "UPDATE doubler_tracking SET final_price=?, final_gain_pct=?, "
                    "doubled=?, verified=1, verified_date=? WHERE id=?",
                    (final_close, round(final_gain, 1), doubled,
                     datetime.now().strftime("%Y-%m-%d"), row["id"])
                )
                verified_count += 1
        except:
            continue

    conn.commit()

    # === 模型反馈: 按催化剂类型统计命中率 ===
    stats_rows = conn.execute("""
        SELECT catalyst_type, COUNT(*) as total, SUM(doubled) as hits,
               AVG(final_gain_pct) as avg_gain
        FROM doubler_tracking
        WHERE month=? AND verified=1 AND catalyst_type != ''
        GROUP BY catalyst_type
    """, (month,)).fetchall()

    for sr in stats_rows:
        cat_type = sr["catalyst_type"]
        total = sr["total"]
        hits = sr["hits"] or 0
        avg_g = sr["avg_gain"] or 0
        hit_rate = hits / max(total, 1)

        # 权重调整: 命中率偏离50%越大调整越多
        adjustment = (hit_rate - 0.3) * 3  # 0.3为基线预期命中率
        adjustment = max(-2, min(2, adjustment))  # 限制调整范围

        conn.execute("""
            INSERT OR REPLACE INTO catalyst_effectiveness
            (month, catalyst_type, picks_count, doubled_count, avg_gain, weight_adjustment)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (month, cat_type, total, hits, round(avg_g, 1), round(adjustment, 2)))

    conn.commit()
    conn.close()

    return {
        "verified": verified_count,
        "month": month,
        "by_catalyst": [
            {"type": sr["catalyst_type"], "total": sr["total"],
             "hits": sr["hits"] or 0, "avg_gain": round(sr["avg_gain"] or 0, 1)}
            for sr in stats_rows
        ]
    }


def get_catalyst_effectiveness():
    """获取各催化剂类型的历史效果 (用于模型调权)"""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT catalyst_type, 
               SUM(picks_count) as total_picks,
               SUM(doubled_count) as total_hits,
               AVG(avg_gain) as avg_gain,
               AVG(weight_adjustment) as avg_adjustment
        FROM catalyst_effectiveness
        GROUP BY catalyst_type
        ORDER BY total_hits * 1.0 / MAX(total_picks, 1) DESC
    """).fetchall()
    conn.close()
    return [
        {"type": r["catalyst_type"], "picks": r["total_picks"],
         "hits": r["total_hits"] or 0, "avg_gain": round(r["avg_gain"] or 0, 1),
         "adjustment": round(r["avg_adjustment"] or 0, 2)}
        for r in rows
    ]


def get_monthly_history():
    """获取历史月度验证记录"""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT month, recommend_date, COUNT(*) as picks,
               SUM(doubled) as doubled, 
               AVG(final_gain_pct) as avg_gain,
               MAX(max_gain_pct) as best_gain
        FROM doubler_tracking WHERE verified=1
        GROUP BY month ORDER BY month DESC
    """).fetchall()
    conn.close()
    return [
        {"month": r["month"], "date": r["recommend_date"],
         "picks": r["picks"], "doubled": r["doubled"],
         "avg_gain": round(r["avg_gain"] or 0, 1),
         "best": round(r["best_gain"] or 0, 1)}
        for r in rows
    ]


# 初始化
init_tracking_table()
