"""
拾米交易工作室 · 1W方案追踪系统
管理魔法师推荐的1W持仓方案：创建/更新/平仓/盈亏追踪

数据表:
  plan_1w         — 持仓明细 (买入价/当前价/盈亏/状态)
  plan_1w_pnl_log  — 每日盈亏日志
"""
import sqlite3
import json
import os
from datetime import datetime, timedelta
from collections import defaultdict

from logger import get_logger

logger = get_logger("services.plan_1w")

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "shimi.db")


def _get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_tables():
    """初始化1W方案数据表 (幂等)"""
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS plan_1w (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_date TEXT NOT NULL,
            trade_date TEXT,
            total_capital REAL DEFAULT 10000,
            code TEXT NOT NULL,
            name TEXT NOT NULL,
            buy_price REAL NOT NULL,
            shares INTEGER NOT NULL,
            cost REAL NOT NULL,
            current_price REAL,
            market_value REAL,
            pnl REAL DEFAULT 0,
            pnl_pct REAL DEFAULT 0,
            peak_price REAL,
            peak_pnl_pct REAL DEFAULT 0,
            max_drawdown_pct REAL DEFAULT 0,
            status TEXT DEFAULT 'active',
            close_price REAL,
            close_date TEXT,
            close_reason TEXT,
            score REAL,
            early_pattern TEXT,
            note TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS plan_1w_pnl_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id INTEGER NOT NULL,
            log_date TEXT NOT NULL,
            price REAL NOT NULL,
            market_value REAL,
            pnl REAL,
            pnl_pct REAL,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (plan_id) REFERENCES plan_1w(id)
        );

        CREATE INDEX IF NOT EXISTS idx_plan_1w_status ON plan_1w(status);
        CREATE INDEX IF NOT EXISTS idx_plan_1w_date ON plan_1w(plan_date);
        CREATE INDEX IF NOT EXISTS idx_plan_pnl_log_plan ON plan_1w_pnl_log(plan_id, log_date);
    """)
    # 迁移: 添加 tp_price / sl_price 列 (幂等)
    try:
        conn.execute("ALTER TABLE plan_1w ADD COLUMN tp_price REAL DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE plan_1w ADD COLUMN sl_price REAL DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    conn.close()
    logger.info("[plan_1w] 数据表初始化完成")


# ═══════════════════════════════════════════════
# CRUD
# ═══════════════════════════════════════════════

def create_plan(plan_date: str, picks: list, trade_date: str = "") -> list:
    """从魔法师推荐池创建1W方案

    Args:
        plan_date: 方案日期 '2026-06-08'
        picks: 推荐列表 [{code, name, close, score, early_pattern}, ...]
        trade_date: 交易日

    Returns:
        [plan_row, ...]
    """
    conn = _get_conn()
    created = []
    capital = 10000
    allocs = [4000, 3000, 3000]

    for i, pick in enumerate(picks[:3]):
        price = float(pick["close"])
        shares = int(allocs[i] / price / 100) * 100
        if shares < 100:
            continue
        cost = shares * price
        cat = pick.get("catalyst", {})
        row = {
            "plan_date": plan_date,
            "trade_date": trade_date,
            "total_capital": capital,
            "code": str(pick["code"]),
            "name": str(pick.get("name", "")),
            "buy_price": price,
            "shares": shares,
            "cost": round(cost, 2),
            "current_price": price,
            "market_value": round(cost, 2),
            "pnl": 0.0,
            "pnl_pct": 0.0,
            "peak_price": price,
            "peak_pnl_pct": 0.0,
            "status": "draft",
            # 止盈三档: +20%/+40%/+80%
            "tp_price": round(price * 1.40, 2),
            # 止损: -7% 硬止损
            "sl_price": round(price * 0.93, 2),
            "score": pick.get("score", 0),
            "early_pattern": cat.get("early_pattern", ""),
            "note": cat.get("early_reason", ""),
        }
        conn.execute("""
            INSERT INTO plan_1w (plan_date, trade_date, total_capital,
                code, name, buy_price, shares, cost,
                current_price, market_value, peak_price,
                status, tp_price, sl_price,
                score, early_pattern, note)
            VALUES (:plan_date, :trade_date, :total_capital,
                :code, :name, :buy_price, :shares, :cost,
                :current_price, :market_value, :peak_price,
                :status, :tp_price, :sl_price,
                :score, :early_pattern, :note)
        """, row)
        rid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        row["id"] = rid
        created.append(row)

    conn.commit()
    conn.close()
    logger.info("[plan_1w] 方案创建: %d只 (%s)", len(created), plan_date)
    return created


def list_plans(status: str = None, plan_date: str = None, limit: int = 100) -> list:
    """列出方案"""
    conn = _get_conn()
    sql = "SELECT * FROM plan_1w WHERE 1=1"
    params = []
    if status:
        sql += " AND status = ?"
        params.append(status)
    if plan_date:
        sql += " AND plan_date = ?"
        params.append(plan_date)
    sql += " ORDER BY plan_date DESC, id ASC LIMIT ?"
    params.append(limit)
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return rows


def get_plan(plan_id: int) -> dict:
    """获取单个方案"""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM plan_1w WHERE id = ?", (plan_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def activate_plan(plan_id: int, buy_price: float = None, shares: int = None):
    """确认买入 — draft → active, 允许用户修正买入价/股数"""
    conn = _get_conn()
    plan = dict(conn.execute("SELECT * FROM plan_1w WHERE id = ?", (plan_id,)).fetchone())
    if not plan:
        conn.close()
        return {"error": "not found"}

    price = buy_price if buy_price else plan["buy_price"]
    sh = shares if shares else plan["shares"]
    cost = round(price * sh, 2)

    conn.execute("""
        UPDATE plan_1w SET status='active', buy_price=?, shares=?, cost=?,
            current_price=?, market_value=?, peak_price=?,
            tp_price=?, sl_price=?,
            updated_at=datetime('now','localtime')
        WHERE id=?
    """, (price, sh, cost, price, cost, price,
          round(price * 1.40, 2), round(price * 0.93, 2),
          plan_id))
    conn.commit()
    conn.close()
    logger.info("[plan_1w] 确认买入 #%d: %s %d股 @%.2f", plan_id, plan["code"], sh, price)
    return get_plan(plan_id)


def update_price(plan_id: int, price: float):
    """更新当前价格和盈亏"""
    conn = _get_conn()
    plan = dict(conn.execute("SELECT * FROM plan_1w WHERE id = ?", (plan_id,)).fetchone())
    if not plan or plan["status"] != "active":
        conn.close()
        return

    mv = round(plan["shares"] * price, 2)
    pnl = round(mv - plan["cost"], 2)
    pnl_pct = round(pnl / plan["cost"] * 100, 2) if plan["cost"] > 0 else 0
    peak = max(price, plan.get("peak_price", 0) or 0)
    peak_pnl = round((peak - plan["buy_price"]) / plan["buy_price"] * 100, 2) if plan["buy_price"] > 0 else 0
    dd = round(min(pnl_pct, plan.get("max_drawdown_pct", 0) or 0), 2)

    conn.execute("""
        UPDATE plan_1w SET current_price=?, market_value=?, pnl=?, pnl_pct=?,
            peak_price=?, peak_pnl_pct=?, max_drawdown_pct=?,
            updated_at=datetime('now','localtime')
        WHERE id=?
    """, (price, mv, pnl, pnl_pct, peak, peak_pnl, dd, plan_id))

    # P&L log
    today = datetime.now().strftime("%Y-%m-%d")
    conn.execute("""
        INSERT OR REPLACE INTO plan_1w_pnl_log (plan_id, log_date, price, market_value, pnl, pnl_pct)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (plan_id, today, price, mv, pnl, pnl_pct))

    conn.commit()
    conn.close()


def close_plan(plan_id: int, close_price: float = None, reason: str = ""):
    """平仓"""
    conn = _get_conn()
    plan = dict(conn.execute("SELECT * FROM plan_1w WHERE id = ?", (plan_id,)).fetchone())
    if not plan or plan["status"] != "active":
        conn.close()
        return

    price = close_price or plan["current_price"] or plan["buy_price"]
    mv = round(plan["shares"] * price, 2)
    pnl = round(mv - plan["cost"], 2)
    pnl_pct = round(pnl / plan["cost"] * 100, 2) if plan["cost"] > 0 else 0

    conn.execute("""
        UPDATE plan_1w SET status='closed', close_price=?, close_date=date('now','localtime'),
            close_reason=?, current_price=?, market_value=?, pnl=?, pnl_pct=?,
            updated_at=datetime('now','localtime')
        WHERE id=?
    """, (price, reason, price, mv, pnl, pnl_pct, plan_id))
    conn.commit()
    conn.close()
    logger.info("[plan_1w] 平仓 #%d: %s PnL=%.2f (%.1f%%)", plan_id, reason, pnl, pnl_pct)


def delete_plan(plan_id: int):
    """删除方案"""
    conn = _get_conn()
    conn.execute("DELETE FROM plan_1w_pnl_log WHERE plan_id = ?", (plan_id,))
    conn.execute("DELETE FROM plan_1w WHERE id = ?", (plan_id,))
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════
# 盈亏统计
# ═══════════════════════════════════════════════

def get_pnl_summary() -> dict:
    """获取1W方案盈亏汇总"""
    conn = _get_conn()
    active = [dict(r) for r in conn.execute(
        "SELECT * FROM plan_1w WHERE status IN ('active','draft') ORDER BY plan_date DESC, id ASC"
    ).fetchall()]
    closed = [dict(r) for r in conn.execute(
        "SELECT * FROM plan_1w WHERE status='closed' ORDER BY close_date DESC LIMIT 50"
    ).fetchall()]

    total_active_pnl = sum(p.get("pnl", 0) or 0 for p in active)
    total_closed_pnl = sum(p.get("pnl", 0) or 0 for p in closed)
    total_cost = sum(p.get("cost", 0) or 0 for p in active)

    # 近期盈亏日志
    today = datetime.now().strftime("%Y-%m-%d")
    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    recent_logs = [dict(r) for r in conn.execute(
        "SELECT * FROM plan_1w_pnl_log WHERE log_date >= ? ORDER BY log_date DESC LIMIT 50",
        (week_ago,)
    ).fetchall()]

    conn.close()
    return {
        "active_count": len(active),
        "closed_count": len(closed),
        "total_cost": round(total_cost, 2),
        "total_active_pnl": round(total_active_pnl, 2),
        "total_active_pnl_pct": round(total_active_pnl / total_cost * 100, 2) if total_cost > 0 else 0,
        "total_closed_pnl": round(total_closed_pnl, 2),
        "active_plans": active,
        "closed_plans": closed[:20],
        "recent_logs": recent_logs[:30],
        "win_rate": round(
            sum(1 for p in closed if (p.get("pnl") or 0) > 0) / max(len(closed), 1) * 100, 1
        ),
    }


def batch_update_prices():
    """批量更新所有活跃持仓的价格 (从 daily 缓存获取)"""
    from data.fetcher import get_daily

    daily = get_daily()
    if daily is None or isinstance(daily, (dict, str)):
        logger.warning("[plan_1w] daily数据不可用, 跳过批量更新")
        return {"updated": 0}

    conn = _get_conn()
    active = [dict(r) for r in conn.execute(
        "SELECT * FROM plan_1w WHERE status='active'"
    ).fetchall()]
    conn.close()

    updated = 0
    for plan in active:
        code = plan["code"]
        ts_code = (code + ".SZ" if code.startswith(("0", "3"))
                   else code + ".SH" if code.startswith("6")
                   else code + ".BJ")
        row = daily[daily["ts_code"] == ts_code]
        if not row.empty:
            price = float(row.iloc[0]["close"])
            update_price(plan["id"], price)
            updated += 1

    logger.info("[plan_1w] 批量更新: %d/%d 只", updated, len(active))
    return {"updated": updated, "total_active": len(active)}


# ═══════════════════════════════════════════════
# 从魔法师扫描生成新方案
# ═══════════════════════════════════════════════

def generate_from_doubler():
    """从最新魔法师扫描结果生成1W方案"""
    picks_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "current_month_picks_v2.json"
    )
    if not os.path.exists(picks_path):
        picks_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..", "current_month_picks.json"
        )
    if not os.path.exists(picks_path):
        return {"error": "no scan results found"}

    with open(picks_path) as f:
        data = json.load(f)

    top30 = data.get("top30", [])
    if not top30:
        return {"error": "empty scan results"}

    today = datetime.now().strftime("%Y-%m-%d")
    trade_date = data.get("trade_date", today)

    # 过滤: 排除ST + 排除北交所代码
    picks = []
    for c in top30:
        if "ST" in c.get("name", ""):
            continue
        if c.get("code", "").startswith("9"):
            continue
        # 提取催化剂信息 (从嵌套结构展平)
        cat = c.get("catalyst", {})
        c["early_pattern"] = cat.get("early_pattern", "")
        c["early_reason"] = cat.get("early_reason", "")
        c["score"] = c.get("score", 0)
        picks.append(c)

    if len(picks) < 3:
        picks = [c for c in top30 if "ST" not in c.get("name", "")][:3]

    # 替换模式: 删除所有旧草稿 (未激活的), 活跃的保持不变
    conn = _get_conn()
    old_ids = [r[0] for r in conn.execute(
        "SELECT id FROM plan_1w WHERE status='draft'"
    ).fetchall()]
    for oid in old_ids:
        conn.execute("DELETE FROM plan_1w_pnl_log WHERE plan_id=?", (oid,))
        conn.execute("DELETE FROM plan_1w WHERE id=?", (oid,))
    conn.commit()
    conn.close()
    if old_ids:
        logger.info("[plan_1w] 替换旧草稿: %d条已删除", len(old_ids))

    plans = create_plan(today, picks, trade_date)
    return {"created": len(plans), "replaced": len(old_ids), "plans": plans}
