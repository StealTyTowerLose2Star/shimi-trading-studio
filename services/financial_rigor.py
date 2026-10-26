"""
金融严谨性工具 (估值复算/市值验算/多源交叉校验/三情景估值) — 纯标准库, 无网络无LLM。

移植自 ai-berkshire (MIT License):
  来源: https://github.com/xbtlin/ai-berkshire/blob/main/tools/financial_rigor.py
  上游作者: xbtlin / ai-berkshire
  许可证: MIT (详见 https://github.com/xbtlin/ai-berkshire/blob/main/LICENSE)

本地适配 (2026-10-26, 拾米本地部署):
  - 原文件为 CLI 工具 (print 报告 + argparse); 本移植保留全部计算语义
    (Decimal 精确引擎/容差判定/三情景复利), 将 print 输出重构为结构化 dict 返回,
    供 services/stock_radar.py 个股雷达「估值复算」维度调用
  - 不引入任何第三方依赖; 不发起网络请求; 不做数据来源判断 (由调用方供数)

MIT License 要求保留版权声明: Copyright (c) xbtlin (ai-berkshire),
本文件按 MIT 条款继续使用与修改, 同样以 MIT 提供。
"""

import math
from decimal import Decimal, Context, ROUND_HALF_EVEN

# ---------------------------------------------------------------------------
# Exact Decimal Engine (no floating-point drift) — 与上游一致
# ---------------------------------------------------------------------------

_CTX = Context(prec=28, rounding=ROUND_HALF_EVEN)


def exact(value) -> Decimal:
    """Convert any numeric to exact Decimal, avoiding float traps."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        return Decimal(str(value))
    return Decimal(str(value))


def fmt_number(d, unit: str = "") -> str:
    """Format large numbers in human-readable form (亿/万亿/B/T)."""
    v = float(d)
    abs_v = abs(v)
    if unit in ("亿", "亿元", "亿港元", "亿美元"):
        if abs_v >= 10000:
            return f"{v/10000:.2f}万亿{unit[1:] if len(unit) > 1 else ''}"
        return f"{v:.2f}{unit}"
    if abs_v >= 1e12:
        return f"{v/1e12:.2f}T"
    if abs_v >= 1e9:
        return f"{v/1e9:.2f}B"
    if abs_v >= 1e6:
        return f"{v/1e6:.2f}M"
    return f"{v:,.2f}"


# ---------------------------------------------------------------------------
# 1. Market Cap Verification (股价×总股本 vs 报告市值)
# ---------------------------------------------------------------------------

def market_cap_check(price, shares, reported_cap, tolerance_pct=5.0):
    """市值验算: calculated = price × shares, 与 reported_cap 比较偏差。

    Returns: {"calculated": float, "reported": float, "deviation_pct": float,
              "status": "pass"|"acceptable"|"fail"}
    判定与上游一致: <=1% pass; <=5%(tolerance) acceptable; 否则 fail。
    """
    p, s, r = exact(price), exact(shares), exact(reported_cap)
    calculated = _CTX.multiply(p, s)
    deviation = abs(float(calculated - r) / float(r)) * 100 if r != 0 else 0.0
    if deviation > tolerance_pct:
        status = "fail"
    elif deviation > 1:
        status = "acceptable"
    else:
        status = "pass"
    return {"calculated": float(calculated), "reported": float(r),
            "deviation_pct": round(deviation, 2), "status": status}


# ---------------------------------------------------------------------------
# 2. Valuation Metrics Verification (估值指标复算)
# ---------------------------------------------------------------------------

def valuation_metrics(price, eps=None, bvps=None, fcf_per_share=None,
                      dividend=None, revenue_per_share=None):
    """由原始输入复算估值指标 (任一输入 None 则跳过对应指标, 不补位)。

    Returns: dict, 存在的键: PE/PB/ROE/P_FCF/FCF_Yield/Dividend_Yield/PS
    语义与上游 verify_valuation 一致 (PE=price/eps; ROE=eps/bvps; ...)。
    """
    p = exact(price)
    results = {}
    if eps is not None:
        e = exact(eps)
        if e != 0:
            results["PE"] = float(_CTX.divide(p, e))
            results["Earnings_Yield"] = float(_CTX.divide(e, p) * 100)
    if bvps is not None:
        b = exact(bvps)
        if b != 0:
            results["PB"] = float(_CTX.divide(p, b))
            if eps is not None and float(exact(eps)) != 0:
                results["ROE"] = float(_CTX.divide(exact(eps), b) * 100)
    if fcf_per_share is not None:
        f = exact(fcf_per_share)
        if f != 0:
            results["P_FCF"] = float(_CTX.divide(p, f))
            results["FCF_Yield"] = float(_CTX.divide(f, p) * 100)
    if dividend is not None:
        d = exact(dividend)
        if p != 0:
            results["Dividend_Yield"] = float(_CTX.divide(d, p) * 100)
    if revenue_per_share is not None:
        r = exact(revenue_per_share)
        if r != 0:
            results["PS"] = float(_CTX.divide(p, r))
    return results


# ---------------------------------------------------------------------------
# 3. Cross-Source Data Validation (多源交叉校验)
# ---------------------------------------------------------------------------

def cross_validate(field_name, source_values: dict, tolerance_pct=2.0):
    """同一字段多来源取值对比, 以中位数为参考, 偏差超 tolerance_pct 标记。

    Returns: {"field": str, "median": float, "consensus": float,
              "all_consistent": bool,
              "sources": [{source, value, deviation_pct, ok}]}
    """
    values = {k: exact(v) for k, v in source_values.items()}
    nums = sorted(float(v) for v in values.values())
    n = len(nums)
    if n == 0:
        return {"field": field_name, "median": None, "consensus": None,
                "all_consistent": False, "sources": []}
    median = nums[n // 2] if n % 2 == 1 else (nums[n//2-1] + nums[n//2]) / 2
    sources = []
    all_ok = True
    for src, val in values.items():
        dev = abs(float(val) - median) / median * 100 if median != 0 else 0.0
        ok = dev <= tolerance_pct
        all_ok = all_ok and ok
        sources.append({"source": src, "value": float(val),
                        "deviation_pct": round(dev, 2), "ok": ok})
    return {"field": field_name, "median": median, "consensus": median,
            "all_consistent": all_ok, "sources": sources}


# ---------------------------------------------------------------------------
# 4. Benford's Law Quick Check (财务数据首位数分布检查)
# ---------------------------------------------------------------------------

_BENFORD = {d: math.log10(1 + 1/d) for d in range(1, 10)}


def benford_check(values: list):
    """Benford 首位数分布检查; 样本 <50 返回 None (不可靠, 与上游一致)。"""
    digits = []
    for v in values:
        v = abs(float(v))
        if v > 0:
            sig = 10 ** (math.log10(v) - math.floor(math.log10(v)))
            d = int(sig)
            if 1 <= d <= 9:
                digits.append(d)
    n = len(digits)
    if n < 50:
        return None
    counts = {}
    for d in digits:
        counts[d] = counts.get(d, 0) + 1
    observed = {d: counts.get(d, 0) / n for d in range(1, 10)}
    mad = sum(abs(observed.get(d, 0) - _BENFORD[d]) for d in range(1, 10)) / 9
    chi2 = sum((counts.get(d, 0) - _BENFORD[d] * n) ** 2 / (_BENFORD[d] * n)
               for d in range(1, 10))
    if mad < 0.006:
        conformity = "Close"
    elif mad < 0.012:
        conformity = "Acceptable"
    elif mad < 0.015:
        conformity = "Marginally Acceptable"
    else:
        conformity = "Nonconforming"
    return {"mad": mad, "chi2": chi2, "conformity": conformity,
            "is_conforming": mad < 0.015}


# ---------------------------------------------------------------------------
# 5. Three-Scenario Valuation (三情景估值)
# ---------------------------------------------------------------------------

def three_scenario_valuation(current_price, current_eps,
                             growth_optimistic, growth_neutral, growth_pessimistic,
                             pe_optimistic, pe_neutral, pe_pessimistic,
                             years=3):
    """三情景目标价 (精确十进制复利, 结果确定性可复现)。

    future_eps = current_eps × (1+g)^years; target_price = future_eps × target_pe
    Returns: {"years": int, "scenarios": [{name, growth, target_pe,
              target_eps, target_price, change_pct}]}
    """
    p = exact(current_price)
    eps = exact(current_eps)
    scenarios = [
        ("乐观", growth_optimistic, pe_optimistic),
        ("中性", growth_neutral, pe_neutral),
        ("悲观", growth_pessimistic, pe_pessimistic),
    ]
    out = []
    for name, growth, pe in scenarios:
        g = exact(growth)
        target_pe = exact(pe)
        future_eps = eps
        for _ in range(years):
            future_eps = _CTX.multiply(future_eps, _CTX.add(Decimal("1"), g))
        target_price = _CTX.multiply(future_eps, target_pe)
        change = float(target_price - p) / float(p) * 100
        out.append({"name": name, "growth": float(g), "target_pe": float(target_pe),
                    "target_eps": round(float(future_eps), 2),
                    "target_price": round(float(target_price), 2),
                    "change_pct": round(change, 1)})
    return {"years": years, "scenarios": out}
