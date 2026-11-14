# -*- coding: utf-8 -*-
"""I4 观察期分析: 从 observe/log.jsonl 汇总每周观察指标
用于 M3 判定: 允许交易窗口收益中位数>0 且持续活跃才转正式
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

LOG = Path("D:/Codex_Space/Jobs/shimi-local/iteration/observe/log.jsonl")


def main():
    if not LOG.exists():
        print("no observation log yet")
        return 1
    weeks = defaultdict(list)
    for ln in LOG.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(ln)
        except json.JSONDecodeError:
            continue
        day = r.get("date", "")
        year, week, _ = __import__("datetime").datetime.strptime(day, "%Y-%m-%d").isocalendar()
        weeks[(year, week)].append(r)

    print("week | days | regime_ok | candidate_active | default_signals | candidate_signals")
    for key in sorted(weeks):
        recs = weeks[key]
        days = len(recs)
        regime = sum(1 for r in recs if r.get("regime_ok"))
        active = sum(1 for r in recs if r.get("activity", {}).get("candidate_active"))
        ds = sum(r.get("activity", {}).get("default_signals", 0) for r in recs)
        cs = sum(r.get("activity", {}).get("candidate_signals", 0) for r in recs)
        print(f"{key[0]}-W{key[1]:02d} | {days} | {regime} | {active} | {ds} | {cs}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
