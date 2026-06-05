"""
拾米交易工作室 - 数据库：交易记录管理模块
增删改查 + 交易统计 + 持仓浮动盈亏
"""
from datetime import datetime

from db.core import get_db, _row_to_dict, _rows_to_list, _last_id_suffix, _ph, config


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

            # 如果是平仓
            if "exit_price" in data and data["exit_price"] is not None:
                exit_qty = data.get("exit_qty", trade_dict["qty"])
                is_partial = exit_qty < trade_dict["qty"]

                if is_partial:
                    # 部分平仓：减少持仓数量，清除之前可能已设置的 exit_price
                    remaining = trade_dict["qty"] - exit_qty
                    conn.execute(f"UPDATE trades SET qty={_ph()}, exit_price=NULL, updated_at=CURRENT_TIMESTAMP WHERE id={_ph()}", (remaining, trade_id))
                    pnl = (data["exit_price"] - trade_dict["entry_price"]) * exit_qty if trade_dict["direction"] == "buy" else (trade_dict["entry_price"] - data["exit_price"]) * exit_qty
                    conn.execute(f"INSERT INTO trade_log (trade_id, user_id, action, detail) VALUES ({_ph()}, {_ph()}, 'partial_close', {_ph()})",
                        (trade_id, user_id, f"部分平仓 {exit_qty}股 @¥{data['exit_price']} 盈亏:¥{pnl:.2f} 剩余{remaining}股"))
                else:
                    # 全部平仓
                    pnl = (data["exit_price"] - trade_dict["entry_price"]) * trade_dict["qty"] if trade_dict["direction"] == "buy" else (trade_dict["entry_price"] - data["exit_price"]) * trade_dict["qty"]
                    conn.execute(f"INSERT INTO trade_log (trade_id, user_id, action, detail) VALUES ({_ph()}, {_ph()}, 'close', {_ph()})",
                        (trade_id, user_id, f"平仓 @¥{data['exit_price']} 盈亏:¥{pnl:.2f}"))

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
