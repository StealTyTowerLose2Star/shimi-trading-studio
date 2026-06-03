#!/usr/bin/env python3
"""
翻倍潜力股推荐 — 最终报告
过滤ST股，补充仓位管理，输出可操作建议
"""
import tushare as ts
import json
import sys
from datetime import datetime

sys.path.insert(0, '/root/shi-mi-dashboard')
from config import TUSHARE_TOKEN

pro = ts.pro_api(TUSHARE_TOKEN)

with open("/root/shi-mi-dashboard/current_month_picks.json", "r") as f:
    data = json.load(f)

top30 = data["top30"]

# 过滤 ST/*ST
clean = [c for c in top30 if "ST" not in c["name"] and "*ST" not in c["name"]]
print(f"过滤ST后: {len(clean)} 只 (原{len(top30)}只)")

# 按评分重新排序
clean.sort(key=lambda x: -x["score"])

print("=" * 60)
print("🎯 6月翻倍潜力股推荐报告")
print("   拾米交易工作室")
print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M')}")
print("=" * 60)

# 最终推荐
final_picks = clean[:12]

print(f"\n💎 核心推荐池 ({len(final_picks)}只)")
print("-" * 70)
print(f"{'#':<3} {'代码':<8} {'名称':<8} {'现价':<8} {'行业':<8} {'流通市值':<10} {'涨幅':<8} {'换手':<6} {'评分':<4}")
print("-" * 70)

for i, c in enumerate(final_picks):
    print(f"{i+1:<3} {c['code']:<8} {c['name']:<8} ¥{c['close']:<7.2f} "
          f"{c['industry']:<8} {c['circ_mv_yi']:<8.1f}亿 "
          f"{c['pct_chg']:>+6.2f}% {c['turnover']:>5.1f}% {c['score']:<4}")

# ============================================================
# 仓位管理方案 (1W启动资金)
# ============================================================
print(f"\n{'='*60}")
print(f"💰 仓位管理方案 (启动资金: ¥10,000)")
print(f"{'='*60}")

# 基于历史翻倍股分析: 113只月度翻倍, 每月3-16只不等
# 策略: 分散到3-5只最有潜力的, 保留机动仓
total_capital = 10000

# 分两档: 
# Tier1 (评分≥90): 仓位25% = ¥2500
# Tier2 (评分≥85): 仓位15% = ¥1500

tier1 = [c for c in final_picks if c["score"] >= 90]
tier2 = [c for c in final_picks if 85 <= c["score"] < 90]

print("\n📊 两档仓位分配:")
print(f"  Tier1 (评分≥90, 高确定性): {len(tier1)}只 → 每只25%仓位 = ¥2,500")
print(f"  Tier2 (评分85-89, 中等确定性): {len(tier2)}只 → 每只15%仓位 = ¥1,500")
print(f"  预留现金: 20-40% → 用于加仓/调仓")

# 推荐持仓组合
print(f"\n🎯 建议持仓组合 (3-5只):")
position_plan = []

# Tier1 picks first
recommended = tier1[:3] if len(tier1) >= 3 else tier1 + tier2[:3-len(tier1)]
if len(recommended) < 3:
    recommended = final_picks[:3]

total_allocated = 0
for i, c in enumerate(recommended):
    if i < 2:
        alloc = 2500  # Tier1
        pct = "25%"
    else:
        alloc = 2000  # 略降
        pct = "20%"
    total_allocated += alloc
    
    # 计算可买股数(按100股整数)
    shares = int(alloc / c["close"] / 100) * 100
    actual_cost = shares * c["close"]
    actual_pct = actual_cost / total_capital * 100
    
    sb = c["score_breakdown"]
    position_plan.append({
        "code": c["code"], "name": c["name"], "price": c["close"],
        "shares": shares, "cost": round(actual_cost, 2), "pct": f"{actual_pct:.1f}%",
        "score": c["score"],
        "reason": f"行业{c['industry']}({sb['industry']}分) + 小市值({c['circ_mv_yi']:.1f}亿) + 量能{int(c['vol_ratio'])}倍"
    })
    
    print(f"  #{i+1} {c['code']} {c['name']:<8s} ¥{c['close']:.2f} × {shares}股 "
          f"= ¥{actual_cost:.0f} ({actual_pct:.1f}%) | "
          f"理由: {position_plan[-1]['reason']}")

remaining = total_capital - total_allocated
print(f"\n  已分配: ¥{total_allocated} ({total_allocated/total_capital*100:.0f}%)")
print(f"  预留现金: ¥{remaining} ({remaining/total_capital*100:.0f}%) → 用于强势股加仓/新机会")

# 风控规则
print(f"\n{'='*60}")
print(f"🛡️ 风控规则")
print(f"{'='*60}")
print(f"""
  1. 单只最大亏损: 8% (约¥80-200/只)
  2. 总资金最大回撤: 15% (¥1,500)  → 触发全面减仓
  3. 盈利保护:
     - 涨幅≥20%: 减仓30%, 止损移到成本价
     - 涨幅≥40%: 减仓50%, 止损移到+10%位置
     - 涨幅≥80%: 清仓80%, 留20%看翻倍
  4. 止损原则:
     - 买入后3日内未涨反跌>5%: 止损
     - 涨停板次日低开>3%: 集合竞价止损
     - 单日跌幅>7%: 无条件止损
  5. 禁止行为:
     - 禁止补仓亏损股(不摊平)
     - 禁止ST/*ST/退市风险股
     - 禁止单月换手超过2次(避免追涨杀跌)
  """)

# 翻倍股历史月历
print(f"{'='*60}")
print(f"📅 翻倍股月历参考 (2025.01 - 2026.05)")
print(f"{'='*60}")
print(f"""
  月份    翻倍数  代表股
  ─────  ──────  ──────────────────────────
  202502   10只   万达轴承+221%  杭钢股份+132%
  202503    4只   中毅达+133%    美力科技+104%  ← 低谷
  202504    2只   联合化学+156%  国芳集团+112%  ← 低谷
  202505    3只   舒泰神+151%    中邮科技+151%
  202506    5只   昂利康+124%    科恒股份+123%  ★ 去年6月
  202507   12只   广生堂+213%    恒立钻具+188%  ← 高峰
  202508   13只   开普云+140%    寒武纪+117%   ← 高峰
  202509    7只   首开股份+180%  海博思创+159%
  202510    3只   海峡创新+114%  平潭发展+103%
  202511    6只   海科新源+158%  华盛锂电+137%
  202512   16只   嘉美包装+228%  飞沃科技+195%  ← 全年最高
  202601    9只   志特新材+178%  湖南白银+150%
  202602    3只   豫能控股+118%  江钨装备+110%
  202603    2只   华电辽能+154%  华电能源+137%  ← 低谷
  202604    9只   品高股份+128%  博云新材+123%
  202605    9只   宝鼎科技+153%  长盈通+150%
  ─────────────────────────────────────
  总计: 113只 | 月均: 7.1只 | 最强月: 12月(16只)
  """)

print(f"\n{'='*60}")
print(f"⚠️ 重要提示")
print(f"{'='*60}")
print(f"""
  1. 以上基于量化模型筛选，非投资建议
  2. 翻倍股月均7只，但分布极不均匀 (2-16只/月)
  3. 6月历史翻倍数: 5只 (2025年)，主要集中在医药和电气设备
  4. 当前市场评分模型以"潜力"评估，实盘需结合盘中走势
  5. 建议先用模拟盘验证1-2个月，再上实盘
  6. 1W资金重在风控，不求一把翻倍；分3-5只分散风险
  """)

print("✅ 报告生成完毕")
