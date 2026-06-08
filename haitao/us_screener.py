"""海淘掘金 — 全市场筛股引擎 v2 (实战版)
从8875只NASDAQ/NYSE/AMEX美股中，基于行情数据筛选黄金股

流程:
  1. 拉全量股票列表 → 过滤主要交易所普通股 (~4900只)
  2. 分批获取行情 (0.3s/只 = 300只/100s)
  3. 多因子评分 (价格/涨跌/波动/流动性)
  4. 增量保存 → 断点续传 → 每天刷新
"""
import os, time, json, logging
from datetime import datetime
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

FINNHUB_KEY = os.environ.get("FINNHUB_KEY", "")
FH_BASE = "https://finnhub.io/api/v1"
SCAN_DIR = "/root/shi-mi-dashboard/haitao/cache"
CACHE_FILE = os.path.join(SCAN_DIR, "gold_picks.json")

# ─── 筛选配置 ───────────────────────────
MIN_PRICE, MAX_PRICE = 3.0, 500.0
SCORE_THRESHOLD = 40
BATCH_SIZE = 300
REQ_DELAY = 0.3
HOT_SYMBOLS = set("AAPL MSFT GOOGL AMZN META NVDA TSLA AMD AVGO PLTR SPY QQQ IWM BABA JD PDD NIO XPEV BIDU".split())

os.makedirs(SCAN_DIR, exist_ok=True)

def get_all_stocks():
    """获取全量过滤后的美股列表"""
    url = f"{FH_BASE}/stock/symbol?exchange=US&token={FINNHUB_KEY}"
    r = requests.get(url, timeout=15)
    all_stocks = r.json() if isinstance(r.json(), list) else []
    
    filtered = []
    for s in all_stocks:
        if s.get('type') != 'Common Stock': continue
        if s.get('currency') != 'USD': continue
        if '.' in s.get('symbol', ''): continue
        if len(s.get('symbol', '')) > 5: continue
        filtered.append(s)
    return filtered

def scan_batch(stocks: List[dict], start_idx: int = 0) -> List[dict]:
    """扫描一批股票，返回评分结果"""
    batch = stocks[start_idx:start_idx + BATCH_SIZE]
    results = []
    
    for i, s in enumerate(batch):
        sym = s['symbol']
        time.sleep(REQ_DELAY)
        
        try:
            r = requests.get(f"{FH_BASE}/quote?symbol={sym}&token={FINNHUB_KEY}", timeout=8)
            q = r.json()
        except Exception:
            continue
        
        if not isinstance(q, dict) or not q.get('c'): continue
        
        price = float(q['c'])
        chg = (q.get('dp') or 0)
        vol = (q.get('v') or 0)
        
        if not (MIN_PRICE <= price <= MAX_PRICE): continue
        
        score = 0
        signals = []
        
        # Price action (30pts)
        if 1 <= chg <= 5: score += 25; signals.append('温和上涨')
        elif 5 < chg <= 15: score += 20; signals.append('强势启动')
        elif chg > 15: score += 10; signals.append('暴涨(注意)')
        elif 0 <= chg < 1: score += 15; signals.append('平稳')
        elif -3 <= chg < 0: score += 12; signals.append('微调(关注)')
        elif -10 <= chg < -3: score += 8; signals.append('回调(关注反弹)')
        elif chg < -10: score += 3; signals.append('深度下跌')
        
        # Price zone (25pts)
        if 5 <= price <= 20: score += 25; signals.append('低价小盘')
        elif 20 < price <= 50: score += 20; signals.append('小盘成长')
        elif 50 < price <= 150: score += 15; signals.append('中盘稳健')
        elif 150 < price <= 300: score += 8; signals.append('大盘蓝筹')
        else: score += 3
        
        # Volume (15pts)
        if vol and vol > 5000000: score += 15; signals.append('超高流动性')
        elif vol and vol > 1000000: score += 12
        elif vol and vol > 300000: score += 8
        else: score += 5
        
        # Day range (10pts)
        h, l = float(q.get('h', price)), float(q.get('l', price))
        dr = (h - l) / price * 100 if price > 0 else 0
        if 0 < dr <= 3: score += 10; signals.append('窄幅酝酿')
        elif 3 < dr <= 6: score += 5
        elif dr > 10: score -= 3; signals.append('剧烈波动')
        
        # Above prev close (5pts)
        prev = float(q.get('pc', price))
        if price > prev: score += 5
        
        if score >= SCORE_THRESHOLD:
            results.append({
                'symbol': sym,
                'name': s.get('description', sym)[:30],
                'price': round(price, 2),
                'change_pct': round(chg, 2),
                'volume': vol,
                'day_range_pct': round(dr, 1),
                'score': score,
                'signals': signals,
                'exchange': s.get('mic', ''),
                'scanned_at': datetime.now().strftime('%H:%M:%S'),
            })
        
        if (i + 1) % 30 == 0:
            logger.info(f"  ... {i+1}/{len(batch)} ({len(results)} picks)")
    
    return results

def full_scan(max_batches: int = 2):
    """执行全市场扫描（支持断点续传）"""
    stocks = get_all_stocks()
    logger.info(f"Total stocks: {len(stocks)}")
    
    # Load existing results
    all_picks = []
    existing_symbols = set()
    progress = 0
    
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE) as f:
                saved = json.load(f)
                all_picks = saved.get('results', [])
                progress = saved.get('progress', 0)
                existing_symbols = {p['symbol'] for p in all_picks}
                logger.info(f"Loaded {len(all_picks)} existing picks, progress={progress}")
        except Exception:
            pass
    
    # Scan next batches
    for bi in range(max_batches):
        offset = progress + bi * BATCH_SIZE
        if offset >= len(stocks):
            logger.info("All stocks scanned!")
            break
        
        batch_stocks = stocks[offset:offset + BATCH_SIZE]
        logger.info(f"Scanning batch {bi+1}/{max_batches} (offset={offset}, {len(batch_stocks)} stocks)")
        
        new_picks = scan_batch(batch_stocks, start_idx=0)
        
        # Deduplicate
        for p in new_picks:
            if p['symbol'] not in existing_symbols:
                all_picks.append(p)
                existing_symbols.add(p['symbol'])
        
        # Deduplicate and sort
        seen = set()
        deduped = []
        for p in sorted(all_picks, key=lambda x: x['score'], reverse=True):
            if p['symbol'] not in seen:
                seen.add(p['symbol'])
                deduped.append(p)
        
        # Save after each batch
        with open(CACHE_FILE, 'w') as f:
            json.dump({
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M'),
                'total_scanned': offset + BATCH_SIZE,
                'progress': offset + BATCH_SIZE,
                'total_stocks': len(stocks),
                'results': deduped,
                'gold_picks': [p for p in deduped if p['score'] >= 70],
                'silver_picks': [p for p in deduped if 50 <= p['score'] < 70],
            }, f, indent=1, ensure_ascii=False)
        
        logger.info(f"Saved! {len(deduped)} total picks ({len([p for p in deduped if p['score']>=70])} gold)")
    
    return get_latest_results()

def get_latest_results() -> Optional[Dict]:
    """获取最新扫描结果"""
    if not os.path.exists(CACHE_FILE):
        return None
    with open(CACHE_FILE) as f:
        return json.load(f)
