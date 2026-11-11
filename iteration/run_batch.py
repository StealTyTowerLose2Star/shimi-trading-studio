# -*- coding: utf-8 -*-
"""单进程批量实验 (2026-11-10): 一次数据加载, 多变体连续评估
解决每个变体独立进程重复拉取全市场面板(~150次API调用)的问题。
用法: venv/Scripts/python.exe iteration/run_batch.py <batch.json>
batch.json: [{"tag":"regime20-stoploss7","hold":3,"set":{"stop_loss_pct":-0.07}}, ...]
公共参数(如 regime20)写在公共 "base": {"set": {...}} 中, 各变体叠加。
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "iteration"))

import local_backtest as lb  # noqa: E402
from run_experiment import _load_real_inputs  # noqa: E402


def _data_date_safe():
    import local_tushare_data as _real
    try:
        return _real._data_date()
    except Exception:
        return None


def main():
    batch_path = Path(sys.argv[1])
    spec = json.loads(batch_path.read_text(encoding="utf-8"))
    base_override = spec.get("base", {}).get("set", {})
    variants = spec["variants"]
    d0 = spec.get("d0")

    t0 = time.time()
    print("loading real inputs once...", flush=True)
    frames, name_map, st_codes, bench = _load_real_inputs(d0)
    print(f"loaded in {time.time() - t0:.0f}s, running {len(variants)} variants", flush=True)

    for v in variants:
        tag = v["tag"]
        override = dict(base_override)
        override.update(v.get("set", {}))
        t = time.time()
        wf = lb.run_walk_forward("trend", frames, n_windows=v.get("windows", 6),
                                 hold=v.get("hold", 5), top_n=v.get("top_n", 5),
                                 benchmark=bench, name_map=name_map, st_codes=st_codes,
                                 include_trades=True, spec_override=override or None)
        outdir = ROOT / "iteration" / f"{time.strftime('%Y%m%d-%H%M%S')}-trend-{tag}"
        outdir.mkdir(parents=True, exist_ok=False)
        s = wf["summary"]
        (outdir / "result.json").write_text(
            json.dumps(wf, ensure_ascii=False), encoding="utf-8")
        (outdir / "experiment.json").write_text(json.dumps({
            "strategy": "trend", "n_windows": v.get("windows", 6),
            "hold": v.get("hold", 5), "top_n": v.get("top_n", 5),
            "spec_override": override or None,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "data_date": _data_date_safe(),
            "universe_note": "候选池=当前名录(幸存者偏差); ST当前名录回填历史(时点偏差); EOD收盘值",
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        lines = [f"# walk-forward: trend ({tag})",
                 f"- 正超额窗口: {s['positive_excess_windows']}/{s['n_windows']} · "
                 f"中位超额: {s['median_excess_pp']}pp · 平均超额: {s['mean_excess_pp']}pp",
                 "", "| 窗口 | 起止 | 策略% | 基准% | 超额pp | 完成交易 |",
                 "|---|---|---|---|---|---|"]
        for w in wf["windows"]:
            lines.append(f"| {w['window']} | {w['start']}~{w['end']} | "
                         f"{w['strategy_return_pct']} | {w['benchmark_return_pct']} | "
                         f"{w['excess_pp']} | {w['trades_completed']} |")
        (outdir / "report.md").write_text("\n".join(lines), encoding="utf-8")
        print(f"[{tag}] pos={s['positive_excess_windows']}/6 median={s['median_excess_pp']} "
              f"mean={s['mean_excess_pp']} ({time.time() - t:.0f}s) -> {outdir.name}", flush=True)
    print(f"batch done in {time.time() - t0:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
