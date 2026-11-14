# -*- coding: utf-8 -*-
"""walk-forward 实验运行器 (2026-11-08 I1)

用法 (shimi-local 根目录):
  venv/Scripts/python.exe iteration/run_experiment.py --strategy trend --windows 6 --tag baseline

产出 iteration/<时间戳>-<strategy>-<tag>/:
  experiment.json  参数/代码版本/数据口径 (可复现依据)
  result.json      walk-forward 汇总+逐窗口明细+全量成交 (AI 归因数据源)
  report.md        人读摘要
数据: 复用 _run_local 同源真实面板 (tushare EOD); 不写线上配置, 不影响服务。
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import local_backtest as lb  # noqa: E402


def _load_real_inputs(d0=None):
    """与 _run_local 同源: 面板 + 名录 + ST + 基准 (真实数据, 失败向上抛不补位)。
    2026-11-14 本人要求数据本地化: 实验数据集一次性落盘 iteration/datasets/d0-*.pkl,
    同 d0 的后续实验(参数/指标调整)完全不再调用 API; 仅新 d0 或新增数据字段才联网"""
    import pickle
    ds_dir = ROOT / "iteration" / "datasets"
    ds_dir.mkdir(exist_ok=True)
    ds_path = ds_dir / f"d0-{d0 or 'latest'}.pkl"
    if ds_path.exists():
        with open(ds_path, "rb") as f:
            data = pickle.load(f)
            return data if len(data) == 5 else (*data, {})

    import local_tushare_data as _real
    frames = _real._cached(f"kline_panel:{lb.PANEL_DAYS}:{d0}", 600,
                           lambda: _real._kline_panel_frames(lb.PANEL_DAYS, d0))
    try:
        basic = _real.real_get_stock_basic()
    except Exception:
        basic = None
    name_map, st_codes = {}, set()
    if isinstance(basic, dict) and basic:
        for code, info in basic.items():
            short = str(code).split(".")[0]
            name = str((info or {}).get("name", ""))
            name_map[short] = name
            if "ST" in name.upper():
                st_codes.add(short)
    try:
        bench = _real.real_index_series("000300.SH", lb.PANEL_DAYS, name="沪深300", d0=d0)
    except Exception as e:
        bench = {"note": f"指数基准获取失败(不补0): {str(e)[:80]}"}
    industry_map = {c: (info or {}).get("industry", "") for c, info in (basic or {}).items()} if isinstance(basic, dict) else {}
    with open(ds_path, "wb") as f:
        pickle.dump((frames, name_map, st_codes, bench, industry_map), f)
    return frames, name_map, st_codes, bench, industry_map


def _data_date_safe():
    import local_tushare_data as _real
    try:
        return _real._data_date()
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", default="trend", choices=list(lb._STRATEGIES))
    ap.add_argument("--windows", type=int, default=6)
    ap.add_argument("--hold", type=int, default=5)
    ap.add_argument("--top-n", type=int, default=5)
    ap.add_argument("--tag", default="baseline")
    ap.add_argument("--set", dest="overrides", action="append", default=[],
                    help="单变量假设: --set threshold=35 (可重复; 仅覆盖已有策略参数)")
    args = ap.parse_args()

    spec_override = {}
    for item in args.overrides:
        key, _, val = item.partition("=")
        spec_override[key] = float(val) if "." in val else int(val)

    frames, name_map, st_codes, bench = _load_real_inputs()
    wf = lb.run_walk_forward(args.strategy, frames, n_windows=args.windows,
                             hold=args.hold, top_n=args.top_n,
                             benchmark=bench, name_map=name_map, st_codes=st_codes,
                             include_trades=True, spec_override=spec_override or None)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    outdir = ROOT / "iteration" / f"{stamp}-{args.strategy}-{args.tag}"
    outdir.mkdir(parents=True, exist_ok=False)
    try:
        git_hash = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                                  capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        git_hash = "unknown"
    experiment = {
        "strategy": args.strategy, "n_windows": args.windows,
        "hold": args.hold, "top_n": args.top_n,
        "spec_override": spec_override or None,
        "panel_days": lb.PANEL_DAYS, "git_hash": git_hash,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "data_date": _data_date_safe(),
        "st_filter": "applied" if st_codes else "degraded_no_namelist",
        "data_note": "Tushare EOD 真实面板; 候选池=当前名录(幸存者偏差披露见 notes); "
                     "窗口间无未来数据(eval_end截断); 基准=沪深300买入持有同窗口",
    }
    (outdir / "experiment.json").write_text(
        json.dumps(experiment, ensure_ascii=False, indent=2), encoding="utf-8")
    (outdir / "result.json").write_text(
        json.dumps(wf, ensure_ascii=False, indent=2), encoding="utf-8")

    s = wf["summary"]
    lines = [f"# walk-forward 实验报告: {args.strategy} ({args.tag})",
             f"- 时间: {experiment['created_at']} · 窗口数: {s['n_windows']} · 代码: {git_hash[:8]}",
             f"- **正超额窗口: {s['positive_excess_windows']}/{s['n_windows']} · "
             f"中位超额: {s['median_excess_pp']}pp · 平均超额: {s['mean_excess_pp']}pp**",
             "", "| 窗口 | 起止 | 策略% | 基准% | 超额pp | 完成交易 | 胜率 | 最大回撤 |",
             "|---|---|---|---|---|---|---|---|"]
    for w in wf["windows"]:
        lines.append(
            f"| {w['window']} | {w['start']}~{w['end']} | {w['strategy_return_pct']} | "
            f"{w['benchmark_return_pct']} | {w['excess_pp']} | {w['trades_completed']} | "
            f"{w['win_rate']} | {w['max_drawdown']} |")
    lines += ["", s["gate_note"], "", experiment["data_note"]]
    (outdir / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print("experiment dir:", outdir)
    print(json.dumps(s, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
