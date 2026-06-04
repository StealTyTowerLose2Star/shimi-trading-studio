"""补填交易记录中缺失的股票名称"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from db import get_db
import tushare as ts

with open("/tmp/git_token_raw") as f:
    TOKEN = f.read().strip()
pro = ts.pro_api(TOKEN)

conn = get_db()
rows = conn.execute("SELECT id, code FROM trades WHERE (name IS NULL OR name = '') AND code IS NOT NULL AND code != ''").fetchall()
print(f"共 {len(rows)} 条记录名称缺失")

fixed = 0
for r in rows:
    code = r["code"]
    # Build ts_code
    if code.startswith(("0", "3")):
        ts_code = code + ".SZ"
    elif code.startswith(("6")):
        ts_code = code + ".SH"
    else:
        ts_code = code + ".BJ"
    try:
        df = pro.stock_basic(ts_code=ts_code, fields="name")
        if df is not None and not df.empty:
            name = df.iloc[0]["name"]
            conn.execute("UPDATE trades SET name=? WHERE id=?", (name, r["id"]))
            print(f"  ✅ {code} → {name}")
            fixed += 1
        else:
            print(f"  ❌ {code} → 未查到")
    except Exception as e:
        print(f"  ❌ {code} → {str(e)[:40]}")

conn.commit()
conn.close()
print(f"\n修复完成: {fixed} 条")
