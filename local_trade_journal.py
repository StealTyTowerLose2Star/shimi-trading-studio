"""Local-only trade journal (manual bookkeeping). No broker API, no auto-trading,
no buy/sell advice, no webhooks. 纯手动记账与算账, 不构成任何投资建议。

2026-10-26 启用「交易日志/交易记录」模块 (操作者授权范围: 仅手动记录):
- 流水簿记模型: 每笔买入/卖出一条记录 (date, code, side, price, qty, fee?, note?)
- 表 trade_entries 落在本地 SQLite 业务库 (get_db), 不出本机
- 成本核算口径: 移动加权平均成本 (买入费用摊入成本, 卖出费用抵减已实现盈亏)
- 现价一律真实 EOD (real 模式经 local_tushare_data HTTPS 闸门); 查不到显式
  「行情不可用」(quote_status 标记, 浮盈为 null), 绝不用 0 或合成价
- 视图层以 app.view_functions 替换 trade 蓝图的 /api/trades 四个既有 handler
  (与 local_accounts 同一手法), 另加 /api/trades/portfolio 持仓视图
"""
import math
import re
from datetime import datetime

from db.core import get_db

_CODE_RE = re.compile(r"^\d{6}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_DDL = """
CREATE TABLE IF NOT EXISTS trade_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    code TEXT NOT NULL,
    name TEXT DEFAULT '',
    side TEXT NOT NULL CHECK(side IN ('buy','sell')),
    price REAL NOT NULL,
    qty INTEGER NOT NULL,
    fee REAL DEFAULT 0,
    note TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_trade_entries_user ON trade_entries(user_id);
CREATE INDEX IF NOT EXISTS idx_trade_entries_code ON trade_entries(user_id, code);
"""


def _ensure_table():
    conn = get_db()
    try:
        conn.executescript(_DDL)
        conn.commit()
    finally:
        conn.close()


# ─── 校验 ──────────────────────────────────────────────

def _finite_number(value):
    """严格数值化: 拒绝 bool/None、非数字、非有限值 (inf/nan); 返回 float 或 None。

    2026-10-27 验收返修: float() 会接受 bool (True→1.0) 与 'inf'/'nan' 字符串,
    记账字段必须显式排除。
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _validate_payload(data, partial=False):
    """返回 (clean, error)。partial=True 时只校验出现的字段 (PUT 用)。

    数值口径 (2026-10-27 验收返修, Codex 反例修复):
    - qty: A股按整数股记账 — 拒绝布尔、非整数 (1.9 不得截断为 1)、非有限值;
      int / 整数值 float / 纯数字字符串可接受。
    - price/fee: 拒绝布尔与 inf/nan; price 舍入到 4 位小数后复核仍为正
      (0.00001 舍入为 0 不得入库)。
    """
    clean = {}
    if not isinstance(data, dict):
        return None, "请求体必须是JSON对象"
    if not partial or "date" in data:
        date = str(data.get("date", "")).strip()
        if not _DATE_RE.match(date):
            return None, "date 格式须为 YYYY-MM-DD"
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            return None, "date 不是有效日期"
        clean["date"] = date
    if not partial or "code" in data:
        code = str(data.get("code", "")).strip()
        if not _CODE_RE.match(code):
            return None, "code 须为6位数字代码"
        clean["code"] = code
    if not partial or "side" in data:
        side = str(data.get("side", "")).strip().lower()
        if side not in ("buy", "sell"):
            return None, "side 仅支持 buy(买入)/sell(卖出)"
        clean["side"] = side
    if not partial or "price" in data:
        price = _finite_number(data.get("price"))
        if price is None:
            return None, "price 须为有限数字 (拒绝布尔/inf/nan)"
        price = round(price, 4)
        if not (price > 0):
            return None, "price 舍入到4位小数后须仍为正数"
        clean["price"] = price
    if not partial or "qty" in data:
        qty_raw = data.get("qty")
        qty = None
        if isinstance(qty_raw, bool):
            return None, "qty 须为正整数 (拒绝布尔值)"
        if isinstance(qty_raw, int):
            qty = qty_raw
        elif isinstance(qty_raw, float):
            if math.isfinite(qty_raw) and qty_raw.is_integer():
                qty = int(qty_raw)
        elif isinstance(qty_raw, str):
            s = qty_raw.strip()
            if re.fullmatch(r"\d+", s):
                qty = int(s)
        if qty is None:
            return None, "qty 须为正整数 (A股按整数股记账, 拒绝非整数)"
        if qty <= 0:
            return None, "qty 须为正整数"
        clean["qty"] = qty
    if "fee" in data or not partial:
        fee_raw = data.get("fee")
        if fee_raw is None or fee_raw == "":
            fee = 0.0
        else:
            fee = _finite_number(fee_raw)
            if fee is None:
                return None, "fee 须为有限数字 (拒绝布尔/inf/nan)"
        if fee < 0:
            return None, "fee 不能为负"
        clean["fee"] = round(fee, 4)
    if "name" in data:
        clean["name"] = str(data.get("name") or "")[:50]
    if "note" in data:
        clean["note"] = str(data.get("note") or "")[:200]
    return clean, None


def _entries_for_code(user_id, code, exclude_id=None):
    """该标的全部流水按 (date, id) 升序 — 成本重放顺序"""
    conn = get_db()
    try:
        sql = ("SELECT id, date, code, side, price, qty, fee FROM trade_entries "
               "WHERE user_id=? AND code=?")
        params = [user_id, code]
        if exclude_id is not None:
            sql += " AND id<>?"
            params.append(exclude_id)
        sql += " ORDER BY date, id"
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _oversell_error(entries):
    """重放流水, 任一卖出点持仓不足则返回错误文案, 否则 None"""
    qty = 0
    for e in entries:
        if e["side"] == "buy":
            qty += e["qty"]
        else:
            qty -= e["qty"]
            if qty < 0:
                return (f"卖出 {e['qty']} 股超出该时点持仓 (流水日期 {e['date']}); "
                        f"请先核对买入记录")
    return None


# ─── CRUD ──────────────────────────────────────────────

def add_entry(user_id, data):
    clean, err = _validate_payload(data)
    if err:
        return {"error": err}
    _ensure_table()
    if clean["side"] == "sell":
        seq = _entries_for_code(user_id, clean["code"]) + [clean]
        seq.sort(key=lambda e: (e["date"], e.get("id") or 1 << 62))
        err = _oversell_error(seq)
        if err:
            return {"error": err}
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO trade_entries (user_id, date, code, name, side, price, qty, fee, note)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (user_id, clean["date"], clean["code"], clean.get("name", ""),
             clean["side"], clean["price"], clean["qty"], clean.get("fee", 0),
             clean.get("note", "")))
        conn.commit()
        return {"id": cur.lastrowid, **clean}
    finally:
        conn.close()


def update_entry(entry_id, user_id, data):
    clean, err = _validate_payload(data, partial=True)
    if err:
        return {"error": err}
    if not clean:
        return {"error": "没有可更新的字段"}
    _ensure_table()
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM trade_entries WHERE id=? AND user_id=?",
            (entry_id, user_id)).fetchone()
        if not row:
            return {"error": "记录不存在或无权限"}
        merged = {**dict(row), **clean}
        # 改动后重放该标的全部流水, 破坏持仓一致性则拒绝
        others = _entries_for_code(user_id, merged["code"], exclude_id=entry_id)
        seq = others + [{**merged}]
        seq.sort(key=lambda e: (e["date"], e["id"]))
        err = _oversell_error(seq)
        if err:
            return {"error": f"修改将导致持仓不一致: {err}"}
        fields = ", ".join(f"{k}=?" for k in clean)
        conn.execute(
            f"UPDATE trade_entries SET {fields}, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (*clean.values(), entry_id))
        conn.commit()
        return {"id": entry_id, **merged}
    finally:
        conn.close()


def delete_entry(entry_id, user_id):
    _ensure_table()
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM trade_entries WHERE id=? AND user_id=?",
            (entry_id, user_id)).fetchone()
        if not row:
            return {"error": "记录不存在或无权限"}
        if dict(row)["side"] == "buy":
            seq = _entries_for_code(user_id, dict(row)["code"], exclude_id=entry_id)
            err = _oversell_error(seq)
            if err:
                return {"error": f"删除该买入将导致后续卖出超持仓: {err}"}
        conn.execute("DELETE FROM trade_entries WHERE id=?", (entry_id,))
        conn.commit()
        return {"deleted": entry_id}
    finally:
        conn.close()


def list_entries(user_id, code=None):
    _ensure_table()
    conn = get_db()
    try:
        sql = ("SELECT id, date, code, name, side, price, qty, fee, note,"
               " created_at, updated_at FROM trade_entries WHERE user_id=?")
        params = [user_id]
        if code:
            sql += " AND code=?"
            params.append(code)
        sql += " ORDER BY date DESC, id DESC"
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


# ─── 持仓与盈亏 (移动加权平均成本) ───────────────────────

def compute_positions(entries):
    """移动加权平均成本核算 (纯函数, 输入按 (date,id) 升序流水):

    买入: 新成本价 = (旧成本价×旧数量 + 买入价×数量 + 买入费用) / 新总数量  (费用摊入成本)
    卖出: 已实现盈亏 += (卖出价 − 成本价)×数量 − 卖出费用;  持仓成本价不变
    清仓(数量归0): 成本价重置为 0 (该标的全部盈亏均已结入 realized)
    """
    pos = {}
    for e in entries:
        p = pos.setdefault(e["code"], {
            "code": e["code"], "name": e.get("name", ""), "qty": 0,
            "avg_cost": 0.0, "realized": 0.0, "fees": 0.0,
            "buy_count": 0, "sell_count": 0,
        })
        if e.get("name"):
            p["name"] = e["name"]
        fee = float(e.get("fee") or 0)
        if e["side"] == "buy":
            new_qty = p["qty"] + e["qty"]
            p["avg_cost"] = (p["avg_cost"] * p["qty"] + e["price"] * e["qty"] + fee) / new_qty
            p["qty"] = new_qty
            p["buy_count"] += 1
        else:
            p["realized"] += (e["price"] - p["avg_cost"]) * e["qty"] - fee
            p["qty"] -= e["qty"]
            p["sell_count"] += 1
            if p["qty"] == 0:
                p["avg_cost"] = 0.0
        p["fees"] += fee
    return pos


def portfolio_view(user_id, price_getter):
    """持仓视图。price_getter(code) -> (最新收盘, 价格日期) 或 None (行情不可用)。

    real-only: 行情不可得时 quote_status=unavailable, 浮盈为 None (绝不以 0 冒充)。
    """
    entries = sorted(list_entries(user_id), key=lambda e: (e["date"], e["id"]))
    positions = compute_positions(entries)
    out = []
    total_cost = total_value = total_unreal = 0.0
    quote_ok = quote_total = 0
    for code, p in sorted(positions.items()):
        if p["qty"] <= 0 and abs(p["realized"]) < 1e-9 and p["buy_count"] == 0:
            continue
        row = {
            "code": code,
            "name": p["name"],
            "qty": p["qty"],
            "avg_cost": round(p["avg_cost"], 4) if p["qty"] > 0 else None,
            "cost_total": round(p["avg_cost"] * p["qty"], 2) if p["qty"] > 0 else 0.0,
            "realized_pnl": round(p["realized"], 2),
            "fees_total": round(p["fees"], 2),
            "quote_status": "unavailable",
            "last_price": None,
            "price_date": None,
            "market_value": None,
            "unrealized_pnl": None,
            "unrealized_pct": None,
        }
        if p["qty"] > 0:
            quote_total += 1
            quote = None
            try:
                quote = price_getter(code)
            except Exception:
                quote = None
            if quote:
                price, pdate = quote
                row.update(quote_status="ok", last_price=round(price, 2),
                           price_date=pdate,
                           market_value=round(price * p["qty"], 2),
                           unrealized_pnl=round((price - p["avg_cost"]) * p["qty"], 2),
                           unrealized_pct=round((price / p["avg_cost"] - 1) * 100, 2)
                           if p["avg_cost"] > 0 else None)
                quote_ok += 1
                total_value += price * p["qty"]
                total_unreal += (price - p["avg_cost"]) * p["qty"]
            total_cost += p["avg_cost"] * p["qty"]
        out.append(row)
    summary = {
        "positions_count": sum(1 for r in out if r["qty"] > 0),
        "total_cost": round(total_cost, 2),
        "total_market_value": round(total_value, 2) if quote_ok else None,
        "total_unrealized_pnl": round(total_unreal, 2) if quote_ok else None,
        "total_realized_pnl": round(sum(r["realized_pnl"] for r in out), 2),
        "quote_available": f"{quote_ok}/{quote_total}",
        "price_kind": "tushare 日线 EOD (最近一个有数据交易日的收盘价, 非实时)",
    }
    return {"positions": out, "summary": summary,
            "note": "本页为纯手动记账与算账视图, 不构成任何投资建议"}


# ─── Flask 视图安装 ────────────────────────────────────

def install_trade_journal(app, port):
    """接管 trade 蓝图的 /api/trades 四个 handler 为流水簿记实现,
    并新增 /api/trades/portfolio。仅替换既有 handler, 不新增写面。"""
    from flask import request, jsonify
    from shared.auth import require_user

    hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}

    def _boundary():
        # 写路径仅本机 (服务本身仅监听 loopback, 此处双保险)
        if request.host not in hosts:
            return jsonify(error='交易记录仅支持本机访问'), 403
        if request.content_length and request.content_length > 16384:
            return jsonify(error='请求过大'), 413
        if not request.is_json:
            return jsonify(error='请使用JSON提交'), 415
        return None

    def _name_of(code):
        try:
            from data import fetcher as _f
            basic = _f.get_stock_basic()
            if isinstance(basic, dict):
                for k, info in basic.items():
                    if str(k).split(".")[0] == code:
                        return (info or {}).get("name", "") or ""
        except Exception:
            pass
        return ""

    def _price_getter(code):
        from data import fetcher as _f
        df = _f.get_kline(code, days=5)
        if df is None or len(df) == 0:
            return None
        return (float(df["close"].iloc[-1]), str(df["date"].iloc[-1]))

    def get_entries():
        user = require_user()
        if not user:
            return jsonify({"error": "未登录或登录已过期"}), 401
        entries = list_entries(user["id"], code=request.args.get("code") or None)
        seq = sorted(entries, key=lambda e: (e["date"], e["id"]))
        positions = compute_positions(seq)
        realized = round(sum(p["realized"] for p in positions.values()), 2)
        return jsonify({
            "entries": entries,
            "summary": {
                "total_entries": len(entries),
                "open_positions": sum(1 for p in positions.values() if p["qty"] > 0),
                "total_realized_pnl": realized,
            },
            "note": "纯手动记账, 不构成任何投资建议",
        })

    def post_entry():
        user = require_user()
        if not user:
            return jsonify({"error": "未登录或登录已过期"}), 401
        bad = _boundary()
        if bad:
            return bad
        data = request.get_json(silent=True) or {}
        if not (data.get("name") or "").strip() and (data.get("code") or "").strip():
            data = {**data, "name": _name_of(str(data["code"]).strip())}
        result = add_entry(user["id"], data)
        return (jsonify(result), 400) if "error" in result else (jsonify(result), 201)

    def put_entry(trade_id):
        user = require_user()
        if not user:
            return jsonify({"error": "未登录或登录已过期"}), 401
        bad = _boundary()
        if bad:
            return bad
        data = request.get_json(silent=True) or {}
        result = update_entry(trade_id, user["id"], data)
        if "error" in result:
            status = 404 if "不存在" in result["error"] else 400
            return jsonify(result), status
        return jsonify(result)

    def del_entry(trade_id):
        user = require_user()
        if not user:
            return jsonify({"error": "未登录或登录已过期"}), 401
        if request.host not in hosts:
            return jsonify(error='交易记录仅支持本机访问'), 403
        result = delete_entry(trade_id, user["id"])
        if "error" in result:
            status = 404 if "不存在" in result["error"] else 400
            return jsonify(result), status
        return jsonify(result)

    def portfolio():
        user = require_user()
        if not user:
            return jsonify({"error": "未登录或登录已过期"}), 401
        return jsonify(portfolio_view(user["id"], _price_getter))

    app.view_functions['trade.api_get_trades'] = get_entries
    app.view_functions['trade.api_add_trade'] = post_entry
    app.view_functions['trade.api_update_trade'] = put_entry
    app.view_functions['trade.api_delete_trade'] = del_entry
    app.add_url_rule('/api/trades/portfolio', 'trade_journal_portfolio',
                     portfolio, methods=['GET'])
