#!/usr/bin/env python3
"""Check 1W plan status from SQLite."""
import sqlite3

conn = sqlite3.connect('/root/shimi-trading-studio/shimi.db')
conn.row_factory = sqlite3.Row

# Active positions
rows = conn.execute('''
    SELECT id, code, name, buy_price, current_price, shares,
           pnl, status, early_pattern, plan_date
    FROM plan_1w WHERE status != 'closed'
    ORDER BY plan_date DESC
''').fetchall()

print(f'1W实盘跟踪: {len(rows)} 只活跃')
for r in rows:
    pnl = r['pnl'] or 0
    bp = r['buy_price'] or 0
    print(f'  {r["code"]} {r["name"]} 买入{bp} 现价{r["current_price"]} 盈亏{pnl:.0f}元 ({r["status"]}) [{r["early_pattern"]}]')

# Closed positions
closed = conn.execute('''
    SELECT code, name, buy_price, close_price, pnl, close_reason
    FROM plan_1w WHERE status = 'closed'
    ORDER BY id DESC LIMIT 5
''').fetchall()
print(f'\n已平仓: {len(closed)} 只')
for r in closed:
    print(f'  {r["code"]} {r["name"]} 买{r["buy_price"]}→卖{r["close_price"]} 盈亏{r["pnl"]:.0f}元 ({r["close_reason"]})')

conn.close()
