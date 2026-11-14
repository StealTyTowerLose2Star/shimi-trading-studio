# -*- coding: utf-8 -*-
"""I4 灰度观察名单: 每日生成"默认 vs 候选"信号对照, 只记录不改动线上输出。
观察结果提交本人评审, 不依据单一收益指标自动转正式。

候选 = 指数MA20 + MA20较5日前上升 + 指数20日涨幅3% + ATR6 + 回踩确认
默认 = _SIGNAL_SPEC 原参数 (仅代码路径与候选一致, 便于控制变量)
输出: iteration/observe/log.jsonl 追加 + 当日 JSON
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

CANDIDATE = {"require_index_above_ma": 20, "max_atr_pct": 6.0,
             "breakout_no_pullback": True,
             "require_index_ma_rising": 5, "require_index_gain_20d": 0.03}


def _data_governance(day):
    """数据时点治理字段: 业务日期 vs 抓取时间(UTC)+股票池口径"""
    import local_tushare_data as _real
    retrieved = None
    try:
        snap = _real._daily_snapshot(day.replace("-", ""))
        retrieved = getattr(snap, "attrs", {}).get("retrieved_at_utc") if snap is not None else None
    except Exception:
        retrieved = None
    return {
        "data_date": day,
        "retrieved_at_utc": retrieved,
        "universe_note": "候选池=当前名录(含幸存者偏差); ST为当前名录回填历史(时点偏差); EOD收盘值",
    }


def _prep(frames):
    dates = sorted({d for f in frames.values() for d in f["date"].tolist()})
    idx_maps = {c: {d: i for i, d in enumerate(f["date"].tolist())}
                for c, f in frames.items()}
    cols = {c: (f["open"].to_numpy(dtype=float), f["close"].to_numpy(dtype=float),
                f["volume"].to_numpy(dtype=float), f["pct_chg"].to_numpy(dtype=float))
            for c, f in frames.items()}
    return dates, idx_maps, cols


def _regime_ok(benchmark, day, spec=None):
    """观察名单与回测共用 index_regime_ok (I4返修: 五项门槛全部执行,
    不再只判收盘≥MA)"""
    return lb.index_regime_ok(benchmark, day, spec or CANDIDATE)


def main():
    frames, name_map, st_codes, bench, _industry_map = _load_real_inputs()
    dates, idx_maps, cols = _prep(frames)
    t = len(dates) - 1
    day = dates[t]
    spec_default = dict(lb._SIGNAL_SPEC["trend"])
    spec_cand = dict(spec_default)
    spec_cand.update(CANDIDATE)

    default_picks = lb._scan_day(spec_default, frames, cols, idx_maps, dates, t, st_codes)
    regime = lb.index_regime_ok(bench, day, spec_cand)
    cand_picks = (lb._scan_day(spec_cand, frames, cols, idx_maps, dates, t, st_codes)
                  if regime else [])
    rec = {
        "date": day,
        "regime_ok": regime,
        "default_picks": [{"code": lb._short(c), "score": round(s, 1)} for c, s in default_picks[:5]],
        "candidate_picks": [{"code": lb._short(c), "score": round(s, 1)} for c, s in cand_picks[:5]],
        "candidate_spec": CANDIDATE,
        "activity": {
            "default_signals": len(default_picks),
            "candidate_signals": len(cand_picks),
            "candidate_active": bool(regime and cand_picks),
        },
        "data_governance": _data_governance(day),
        "note": "I4灰度观察: 候选=指数MA20+MA20较5日前上升+指数20日涨幅3%+ATR6+回踩确认; 仅对照记录, 不自动转正式",
    }
    outdir = ROOT / "iteration" / "observe"
    outdir.mkdir(exist_ok=True)
    with open(outdir / "log.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    (outdir / f"{day}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rec, ensure_ascii=False, indent=2))
    return rec


if __name__ == "__main__":
    sys.exit(main())
