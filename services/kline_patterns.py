"""
拾米交易工作室 - K线形态识别引擎
每日扫描市场，识别典型高胜率K线图形

形态清单:
  1. 十字星反包     — 横盘/回调后十字星+次日反包
  2. 看涨吞没       — 阴线次日阳线吞没
  3. 启明星         — 大阴→小K线→大阳过腰
  4. 锤头线         — 长下影小实体(底部)
  5. 多方炮         — 阳→小阴→阳(进攻中继)
  6. 突破缺口       — 放量跳空+不回补
  7. 均线粘合突破   — 均线重合+放量阳线(配合ma_convergence)
"""

import pandas as pd
import numpy as np
from typing import List, Dict, Optional

from data.fetcher import get_daily, get_stock_basic
from realtime_scorer import get_kline_batch, get_kline, ma_convergence_score
from cache import cache_or_fetch


# ============================================================
# 单股形态识别
# ============================================================

def detect_patterns(code: str, df: pd.DataFrame = None) -> Dict:
    """单只股票所有形态检测

    Args:
        code: 股票代码（短码，如 000001）
        df: K线DataFrame（>=30行），None时自动获取

    Returns:
        dict: {
            "code": str,
            "patterns": [匹配的形态列表],
            "details": [各形态的描述],
            "score": 综合评分(0-100)
        }
    """
    if df is None:
        df = get_kline(code, days=60)
    if df is None or len(df) < 15:
        return {"code": code, "patterns": [], "details": [], "score": 0}

    close = df["close"].values
    open_p = df["open"].values
    high = df["high"].values
    low = df["low"].values
    vol = df["volume"].values if "volume" in df.columns else np.ones(len(df))

    patterns = []
    details = []
    score = 0

    # ─── 工具函数 ───
    def _body(i): return abs(close[i] - open_p[i])
    def _upper(i): return high[i] - max(close[i], open_p[i])
    def _lower(i): return min(close[i], open_p[i]) - low[i]
    def _is_red(i): return close[i] > open_p[i]
    def _is_green(i): return close[i] < open_p[i]
    def _avg_vol(n): return float(np.mean(vol[-n-1:-1])) if len(vol) > n else 1

    i = -1  # 最后一天

    # 1. 十字星反包 (Doji Reversal)
    body_i = _body(i)
    total_i = high[i] - low[i]
    if total_i > 0:
        doji_ratio = body_i / total_i
        # 前一天是十字星，今日反包
        if len(df) >= 2:
            j = i - 1
            body_j = _body(j)
            total_j = high[j] - low[j]
            doji_j = body_j / total_j if total_j > 0 else 0
            if doji_j < 0.25 and not _is_green(j):
                if _is_red(i) and close[i] > high[j]:
                    patterns.append("十字星反包")
                    details.append(f"←十字星→阳线反包前高¥{high[j]:.2f}")
                    score += 25

    # 2. 看涨吞没 (Bullish Engulfing)
    if len(df) >= 2:
        j = i - 1
        if _is_green(j) and _is_red(i):
            if close[i] > open_p[j] and open_p[i] < close[j]:
                patterns.append("看涨吞没")
                details.append(f"阳吞阴({close[j]:.2f}→{close[i]:.2f})")
                score += 20

    # 3. 锤头线 (Hammer) — 下影线>=2×实体，在回调低位
    if _is_red(i) or True:  # 红锤绿锤都算
        lower_i = _lower(i)
        if lower_i > body_i * 2 and _upper(i) < body_i * 0.5 and lower_i > 0:
            # 检查是否在近期低位
            min_10 = float(np.min(close[-10:]))
            if close[i] < min_10 * 1.05 or close[i] < close[max(0, i-5)] * 1.02:
                patterns.append("锤头线")
                details.append(f"下影{lower_i:.2f}实体{body_i:.2f}")
                score += 18

    # 4. 启明星 (Morning Star) — 3天形态
    if len(df) >= 3:
        a, b, c = i - 2, i - 1, i
        if _is_green(a) and _body(a) > _body(b) * 1.5:
            if _body(b) < _body(a) * 0.4:  # 星线
                midpoint_a = (close[a] + open_p[a]) / 2
                if _is_red(c) and close[c] > midpoint_a:
                    patterns.append("启明星")
                    details.append(f"大阴→星线→阳过¥{midpoint_a:.2f}")
                    score += 22

    # 5. 多方炮 (Bullish Flag) — 阳→小阴→阳
    if len(df) >= 3:
        a, b, c = i - 2, i - 1, i
        if _is_red(a) and _is_green(b) and _is_red(c):
            if _body(b) < _body(a) * 0.6 and close[c] > high[a]:
                patterns.append("多方炮")
                details.append(f"阳→小阴→阳突破前高")
                score += 20

    # 6. 突破缺口 (Breakaway Gap) — 跳空+放量+不回补
    if len(df) >= 2:
        j = i - 1
        gap_up = low[i] > high[j]
        if gap_up and _is_red(i):
            vol_ratio = vol[i] / max(_avg_vol(20), 1)
            if vol_ratio > 1.3:
                patterns.append("突破缺口")
                details.append(f"跳空¥{high[j]:.2f}→¥{low[i]:.2f} 量{vol_ratio:.1f}x")
                score += 23

    # 7. 均线粘合突破 (MA Convergence Breakout)
    try:
        _ma = ma_convergence_score(code)
        if _ma and _ma.get("score", 0) >= 50 and _is_red(i):
            vol_ratio = vol[i] / max(_avg_vol(20), 1)
            if vol_ratio > 1.2:
                patterns.append("均线粘合突破")
                details.append(f"{_ma['detail']} 放量突破")
                score += 22
    except Exception:
        pass

    score = min(100, score)
    return {
        "code": code,
        "patterns": patterns,
        "details": details,
        "score": score,
    }


# ============================================================
# 全市场扫描
# ============================================================

def scan_patterns(limit: int = 30) -> List[Dict]:
    """全市场扫描K线形态，返回有形态匹配的股票

    预筛选:
      1. 取当日涨幅 TOP 200 (有表现才有形态意义)
      2. 并行预取 K 线
      3. 逐个检测形态

    Args:
        limit: 最大返回数

    Returns:
        list[dict]: 按形态评分降序
    """
    daily = get_daily()
    basic = get_stock_basic()
    if daily is None or isinstance(daily, dict):
        return []

    # 排除北证
    daily = daily[~daily["ts_code"].str.endswith(".BJ")].copy()

    # 全量扫描：排除停牌(pct_chg=0)、超低价(<2元)、超高价(>500元)
    mask = (daily["pct_chg"].abs() > 0) & (daily["close"] >= 2) & (daily["close"] <= 500)
    candidates = daily[mask].copy()

    stock_codes = []
    for c in candidates["ts_code"]:
        short = c.replace(".SZ", "").replace(".SH", "").replace(".BJ", "").strip()
        stock_codes.append(short)

    if not stock_codes:
        return []

    total = len(stock_codes)

    # 分批并行预取 K 线（每次 50 只，避免 tushare 连接池爆炸）
    batch_size = 50
    for i in range(0, total, batch_size):
        batch = stock_codes[i:i + batch_size]
        get_kline_batch(batch, days=60)

    results = []
    for code in stock_codes:
        try:
            df = get_kline(code, days=60)
            if df is not None:
                r = detect_patterns(code, df)
                if r["score"] >= 15 and len(r["patterns"]) > 0:
                    src = daily[daily["ts_code"].str.contains(code, regex=False)]
                    name = ""
                    price = 0
                    change = 0
                    if not src.empty:
                        row = src.iloc[0]
                        name = basic.get(row["ts_code"], {}).get("name", "") if isinstance(basic, dict) else ""
                        price = float(row["close"]) if "close" in row else 0
                        change = float(row["pct_chg"]) if "pct_chg" in row else 0
                    results.append({
                        "code": code,
                        "name": name,
                        "price": round(price, 2),
                        "change": round(change, 2),
                        "score": r["score"],
                        "patterns": r["patterns"],
                        "details": " | ".join(r["details"]),
                    })
        except Exception:
            continue

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:limit]
