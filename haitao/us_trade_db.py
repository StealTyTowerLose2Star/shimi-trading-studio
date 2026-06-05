"""海淘美股 - 数据库：美股交易记录管理
对齐拾米 db/trades.py 风格
"""
from datetime import datetime

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db.core import get_db, _row_to_dict, _rows_to_list, _last_id_suffix, _ph, config


def add_us_trade(user_id: int, data: dict) -> dict:
    """添加美股交易记录"""
    conn = get_db()
    try:
        cur = conn.execute(
            f"""INSERT INTO us_trades
               (user_id, date, ticker, name, direction, entry_price, qty,
                exit_price, stop_loss, target_1, target_2, target_3, note)
               VALUES ({_ph()}, {_ph()}, {_ph()}, {_ph()}, {_ph()}, {_ph()}, {_ph()},
                       {_ph()}, {_ph()}, {_ph()}, {_ph()}, {_ph()}, {_ph()}){_last_id_suffix()}""",
            (user_id,
             data.get("date", datetime.now().strftime("%Y-%m-%d")),
             data.get("ticker", ""),
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

        return {"id": trade_id, **data}
    finally:
        conn.close()


def update_us_trade(trade_id: int, user_id: int, data: dict) -> dict:
    """更新美股交易记录（含平仓）"""
    conn = get_db()
    try:
        cur = conn.execute(
            f"SELECT * FROM us_trades WHERE id={_ph()} AND user_id={_ph()}",
            (trade_id, user_id)
        )
        trade = cur.fetchone()
        if not trade:
            return {"error": "交易记录不存在或无权限"}

        trade_dict = _row_to_dict(trade)
        fields = []
        values = []
        for key in ["date", "ticker", "name", "direction", "entry_price", "qty",
                     "exit_price", "stop_loss", "target_1", "target_2", "target_3", "note"]:
            if key in data:
                fields.append(f"{key}={_ph()}")
                values.append(data[key])

        if fields:
            fields.append("updated_at=CURRENT_TIMESTAMP")
            values.append(trade_id)
            conn.execute(
                f"UPDATE us_trades SET {', '.join(fields)} WHERE id={_ph()}",
                values
            )

        conn.commit()
        return {"id": trade_id, **trade_dict, **data}
    finally:
        conn.close()


def delete_us_trade(trade_id: int, user_id: int) -> dict:
    """删除美股交易记录"""
    conn = get_db()
    try:
        cur = conn.execute(
            f"SELECT * FROM us_trades WHERE id={_ph()} AND user_id={_ph()}",
            (trade_id, user_id)
        )
        if not cur.fetchone():
            return {"error": "交易记录不存在或无权限"}
        conn.execute(f"DELETE FROM us_trades WHERE id={_ph()}", (trade_id,))
        conn.commit()
        return {"deleted": trade_id}
    finally:
        conn.close()


def get_us_trades(user_id: int = None) -> list:
    """获取美股交易记录"""
    conn = get_db()
    try:
        if user_id:
            cur = conn.execute(
                f"SELECT t.*, u.display_name as operator FROM us_trades t "
                f"JOIN users u ON t.user_id = u.id "
                f"WHERE t.user_id={_ph()} ORDER BY t.id DESC",
                (user_id,)
            )
        else:
            cur = conn.execute(
                "SELECT t.*, u.display_name as operator FROM us_trades t "
                "JOIN users u ON t.user_id = u.id ORDER BY t.id DESC"
            )
        return _rows_to_list(cur.fetchall())
    finally:
        conn.close()


def get_us_trade_summary(user_id: int = None) -> dict:
    """美股交易统计摘要（含持仓浮动盈亏）"""
    conn = get_db()
    try:
        if user_id:
            closed = conn.execute(
                f"SELECT * FROM us_trades WHERE user_id={_ph()} AND exit_price IS NOT NULL",
                (user_id,)
            ).fetchall()
            open_trades = conn.execute(
                f"SELECT * FROM us_trades WHERE user_id={_ph()} AND exit_price IS NULL",
                (user_id,)
            ).fetchall()
        else:
            closed = conn.execute(
                "SELECT * FROM us_trades WHERE exit_price IS NOT NULL"
            ).fetchall()
            open_trades = conn.execute(
                "SELECT * FROM us_trades WHERE exit_price IS NULL"
            ).fetchall()

        total_closed = len(closed)
        won = 0
        total_pnl = 0.0
        unrealized_pnl = 0.0
        best = 0.0
        worst = 0.0

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

        # 持仓浮动盈亏
        for t in open_trades:
            t = dict(t)
            try:
                from haitao.us_fetcher import get_history
                df = get_history(t["ticker"], period="1mo")
                if df is not None and len(df) > 0:
                    current = float(df["Close"].iloc[-1])
                    pnl = (current - t["entry_price"]) * t["qty"] if t["direction"] == "buy" \
                          else (t["entry_price"] - current) * t["qty"]
                    unrealized_pnl += pnl
            except:
                pass

        return {
            "total_trades": total_closed + len(open_trades),
            "closed_trades": total_closed,
            "open_trades": len(open_trades),
            "won": won,
            "win_rate": win_rate,
            "total_pnl": round(total_pnl, 2),
            "unrealized_pnl": round(unrealized_pnl, 2),
            "total_pnl_with_unrealized": round(total_pnl + unrealized_pnl, 2),
            "best_trade": round(best, 2),
            "worst_trade": round(worst, 2),
        }
    finally:
        conn.close()
