#!/usr/bin/env python3 -u
"""增量扫描引擎 — 直接运行即可"""
import os, sys, json, time, requests

KEY = os.environ.get("FINNHUB_KEY", "")
BASE = "https://finnhub.io/api/v1"
CACHE = "/root/shi-mi-dashboard/haitao/cache/gold_picks.json"
BATCH = 300
DELAY = 0.3
MAX_BATCHES = 10  # 每轮3000只，用完日配额

print("Loading stock list...", flush=True)
r = requests.get(f"{BASE}/stock/symbol?exchange=US&token={KEY}", timeout=15)
all_stocks = r.json()
# Handle inconsistent Finnhub response
if isinstance(all_stocks, dict):
    err = all_stocks.get('error', str(all_stocks)[:80])
    print(f"Finnhub error: {err}", flush=True)
    sys.exit(1)
if not isinstance(all_stocks, list) or len(all_stocks) == 0:
    print(f"Unexpected response: {type(all_stocks)}", flush=True)
    sys.exit(1)
# Check if items are dicts or strings
if isinstance(all_stocks[0], str):
    print(f"Got string list ({len(all_stocks)} items) - API format changed, retrying...", flush=True)
    time.sleep(5)
    r = requests.get(f"{BASE}/stock/symbol?exchange=US&token={KEY}", timeout=15)
    all_stocks = r.json()

stocks = [
    s for s in all_stocks
    if s.get('type') == 'Common Stock'
    and s.get('currency') == 'USD'
    and '.' not in s.get('symbol', '')
    and len(s.get('symbol', '')) <= 5
]
print(f"Total: {len(stocks)} common stocks", flush=True)

# Load or init cache
if os.path.exists(CACHE):
    with open(CACHE) as f:
        data = json.load(f)
else:
    data = {"results": [], "gold_picks": [], "silver_picks": [], "progress": 0, "total_stocks": len(stocks)}

progress = data["progress"]
all_picks = data["results"]
existing = {p["symbol"] for p in all_picks}

start = time.time()

for bi in range(MAX_BATCHES):
    if progress >= len(stocks):
        print("All done!", flush=True)
        break

    batch = stocks[progress:progress + BATCH]
    new_picks = []

    for i, s in enumerate(batch):
        sym = s["symbol"]
        time.sleep(DELAY)

        try:
            r = requests.get(f"{BASE}/quote?symbol={sym}&token={KEY}", timeout=8)
            q = r.json()
        except Exception:
            continue

        if not isinstance(q, dict) or not q.get("c"):
            continue

        price = float(q["c"])
        if not (3 <= price <= 500):
            continue

        chg = q.get("dp") or 0
        vol = q.get("v") or 0
        hi = float(q.get("h", price))
        lo = float(q.get("l", price))

        # Score
        score = 0
        if 1 <= chg <= 5: score += 25
        elif 5 < chg <= 15: score += 20
        elif chg > 15: score += 10
        elif 0 <= chg < 1: score += 15
        elif -3 <= chg < 0: score += 12
        elif -10 <= chg < -3: score += 8
        else: score += 3

        if 5 <= price <= 20: score += 25
        elif 20 < price <= 50: score += 20
        elif 50 < price <= 150: score += 15
        elif 150 < price <= 300: score += 8
        else: score += 3

        if vol > 5000000: score += 15
        elif vol > 1000000: score += 12
        elif vol > 300000: score += 8
        else: score += 5

        dr = (hi - lo) / price * 100 if price > 0 else 0
        if 0 < dr <= 3: score += 10
        elif dr > 10: score -= 3

        if price > float(q.get("pc", price)):
            score += 5

        if score >= 40 and sym not in existing:
            new_picks.append({
                "symbol": sym,
                "name": s.get("description", sym)[:30],
                "price": round(price, 2),
                "change_pct": round(chg, 2),
                "score": score,
                "volume": vol,
                "day_range_pct": round(dr, 1),
            })
            existing.add(sym)

    progress += len(batch)
    all_picks.extend(new_picks)
    all_picks.sort(key=lambda x: x["score"], reverse=True)

    data["progress"] = progress
    data["results"] = all_picks
    data["gold_picks"] = [p for p in all_picks if p["score"] >= 70][:30]
    data["silver_picks"] = [p for p in all_picks if 50 <= p["score"] < 70][:50]

    with open(CACHE, "w") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)

    g = data["gold_picks"]
    pct = progress * 100 // len(stocks)
    print(f"[{bi+1}/{MAX_BATCHES}] {progress}/{len(stocks)}({pct}%) | picks:{len(all_picks)} gold:{len(g)}", flush=True)
    for x in g[:3]:
        print(f"  {x['symbol']:8s} {x['score']}d ${x['price']}", flush=True)

elapsed = int(time.time() - start)
print(f"Done in {elapsed}s. Progress: {progress}/{len(stocks)}", flush=True)
