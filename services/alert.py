"""
拾米交易工作室 - 条件预警引擎
职责: 规则定义 + 条件检查 + 告警生成 + 消息队列投递

预警类型:
  price_break   — 价格突破 (上穿/下穿阈值)
  pct_change    — 涨跌幅超限
  volume_surge  — 成交量异常放大
  strategy      — 策略信号触发
"""

import json
import os
import time
from typing import Dict, List, Optional
from datetime import datetime

# ─── 预警存储 ──────────────────────────────────────
ALERT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "alerts.json")


def _load_alerts() -> List[Dict]:
    """加载已存储的预警规则"""
    if os.path.exists(ALERT_FILE):
        try:
            with open(ALERT_FILE) as f:
                return json.load(f)
        except:
            pass
    return []


def _save_alerts(alerts: List[Dict]):
    """保存预警规则"""
    os.makedirs(os.path.dirname(ALERT_FILE), exist_ok=True)
    with open(ALERT_FILE, "w") as f:
        json.dump(alerts, f, indent=1, ensure_ascii=False)


def _next_id(alerts: List[Dict]) -> int:
    return max([a.get("id", 0) for a in alerts], default=0) + 1


# ─── 规则定义 ──────────────────────────────────────

ALERT_TYPES = {
    "price_break": {
        "name": "价格突破",
        "params": {"code": "str", "threshold": "float", "direction": "above|below"},
        "desc": "当股价突破设定阈值时触发",
    },
    "pct_change": {
        "name": "涨跌幅预警",
        "params": {"code": "str", "threshold_pct": "float", "direction": "up|down"},
        "desc": "当日涨跌幅超过阈值时触发",
    },
    "volume_surge": {
        "name": "成交量异动",
        "params": {"code": "str", "ratio": "float"},
        "desc": "成交量超过N日均量的 ratio 倍时触发",
    },
    "strategy_signal": {
        "name": "策略信号",
        "params": {"strategy": "trend|hybrid|dragon", "min_score": "int"},
        "desc": "策略评分超过阈值时触发",
    },
}


# ─── CRUD ──────────────────────────────────────────

def create_alert(alert_type: str, params: Dict, enabled: bool = True) -> Dict:
    """创建预警规则

    Args:
        alert_type: price_break | pct_change | volume_surge | strategy_signal
        params: 规则参数
        enabled: 是否启用

    Returns:
        {"id": int, "type": str, "params": dict, "enabled": bool, "created": str}
    """
    if alert_type not in ALERT_TYPES:
        return {"error": f"unknown alert type: {alert_type}"}

    alerts = _load_alerts()
    alert = {
        "id": _next_id(alerts),
        "type": alert_type,
        "params": params,
        "enabled": enabled,
        "created": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "last_triggered": None,
        "trigger_count": 0,
    }
    alerts.append(alert)
    _save_alerts(alerts)
    return alert


def list_alerts() -> List[Dict]:
    return _load_alerts()


def get_alert(alert_id: int) -> Optional[Dict]:
    for a in _load_alerts():
        if a["id"] == alert_id:
            return a
    return None


def update_alert(alert_id: int, updates: Dict) -> Optional[Dict]:
    alerts = _load_alerts()
    for a in alerts:
        if a["id"] == alert_id:
            a.update(updates)
            _save_alerts(alerts)
            return a
    return None


def delete_alert(alert_id: int) -> bool:
    alerts = _load_alerts()
    filtered = [a for a in alerts if a["id"] != alert_id]
    if len(filtered) < len(alerts):
        _save_alerts(filtered)
        return True
    return False


# ─── 条件检查引擎 ──────────────────────────────────

def check_alerts(force: bool = False) -> List[Dict]:
    """检查所有启用的预警规则，返回触发的告警列表

    Args:
        force: 强制检查 (忽略冷却时间)

    Returns:
        [{"alert_id": int, "type": str, "message": str, "data": dict, "time": str}]
    """
    alerts = _load_alerts()
    triggered = []

    for alert in alerts:
        if not alert.get("enabled", True):
            continue

        # 冷却检查 (5分钟内不重复触发)
        last = alert.get("last_triggered")
        if last and not force:
            try:
                last_dt = datetime.strptime(last, "%Y-%m-%d %H:%M")
                if (datetime.now() - last_dt).total_seconds() < 300:
                    continue
            except:
                pass

        result = _check_single(alert)
        if result:
            triggered.append(result)
            alert["last_triggered"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            alert["trigger_count"] = alert.get("trigger_count", 0) + 1

    if triggered:
        _save_alerts(alerts)
        # 投递到消息队列 (供通讯员发送)
        _push_to_queue(triggered)

    return triggered


def _check_single(alert: Dict) -> Optional[Dict]:
    """检查单条预警规则"""
    atype = alert["type"]
    params = alert["params"]
    aid = alert["id"]

    if atype == "price_break":
        return _check_price_break(aid, params)
    elif atype == "pct_change":
        return _check_pct_change(aid, params)
    elif atype == "volume_surge":
        return _check_volume_surge(aid, params)
    elif atype == "strategy_signal":
        return _check_strategy_signal(aid, params)

    return None


def _check_price_break(aid: int, params: Dict) -> Optional[Dict]:
    """检查价格突破"""
    code = params.get("code", "")
    threshold = float(params.get("threshold", 0))
    direction = params.get("direction", "above")

    if not code or threshold <= 0:
        return None

    try:
        from data.fetcher_core import get_ts, get_daily
        daily = get_daily()
        if daily is None or isinstance(daily, (dict, str)):
            return None
        ts_code = code + (".SZ" if code.startswith(("0", "3")) else ".SH")
        if code.startswith("9"):
            ts_code = code + ".BJ"
        row = daily[daily["ts_code"] == ts_code]
        if row.empty:
            return None
        price = float(row.iloc[0]["close"])
    except:
        return None

    if (direction == "above" and price >= threshold) or \
       (direction == "below" and price <= threshold):
        return {
            "alert_id": aid,
            "type": "price_break",
            "message": f"{code} 价格{'突破' if direction=='above' else '跌破'} {threshold} (当前 {price})",
            "data": {"code": code, "price": price, "threshold": threshold, "direction": direction},
            "time": datetime.now().strftime("%H:%M:%S"),
        }
    return None


def _check_pct_change(aid: int, params: Dict) -> Optional[Dict]:
    """检查涨跌幅"""
    code = params.get("code", "")
    threshold_pct = float(params.get("threshold_pct", 5))
    direction = params.get("direction", "up")

    if not code:
        return None

    try:
        from data.fetcher_core import get_daily
        daily = get_daily()
        if daily is None or isinstance(daily, (dict, str)):
            return None
        ts_code = code + (".SZ" if code.startswith(("0", "3")) else ".SH")
        if code.startswith("9"):
            ts_code = code + ".BJ"
        row = daily[daily["ts_code"] == ts_code]
        if row.empty:
            return None
        pct = float(row.iloc[0]["pct_chg"])
    except:
        return None

    if (direction == "up" and pct >= threshold_pct) or \
       (direction == "down" and pct <= -threshold_pct):
        return {
            "alert_id": aid,
            "type": "pct_change",
            "message": f"{code} {'涨幅' if pct>0 else '跌幅'} {abs(pct):.1f}% {'超过' if abs(pct)>=threshold_pct else ''}阈值 {threshold_pct}%",
            "data": {"code": code, "pct_chg": pct, "threshold": threshold_pct},
            "time": datetime.now().strftime("%H:%M:%S"),
        }
    return None


def _check_volume_surge(aid: int, params: Dict) -> Optional[Dict]:
    """检查成交量异动"""
    code = params.get("code", "")
    ratio = float(params.get("ratio", 3))

    if not code:
        return None

    try:
        from data.fetcher_core import get_ts, get_daily_basic
        basic = get_daily_basic()
        if basic is None or isinstance(basic, (dict, str)):
            return None
        ts_code = code + (".SZ" if code.startswith(("0", "3")) else ".SH")
        if code.startswith("9"):
            ts_code = code + ".BJ"
        row = basic[basic["ts_code"] == ts_code]
        if row.empty:
            return None
        vol_ratio = float(row.iloc[0].get("volume_ratio", 0))
    except:
        return None

    if vol_ratio >= ratio:
        return {
            "alert_id": aid,
            "type": "volume_surge",
            "message": f"{code} 成交量异动 (量比 {vol_ratio:.1f}x, 阈值 {ratio}x)",
            "data": {"code": code, "vol_ratio": vol_ratio, "threshold": ratio},
            "time": datetime.now().strftime("%H:%M:%S"),
        }
    return None


def _check_strategy_signal(aid: int, params: Dict) -> Optional[Dict]:
    """检查策略信号"""
    strategy = params.get("strategy", "trend")
    min_score = int(params.get("min_score", 50))

    try:
        from services.strategy import run_trend_scan, run_hybrid_scan, run_dragon_scan
        fns = {"trend": run_trend_scan, "hybrid": run_hybrid_scan, "dragon": run_dragon_scan}
        result = fns[strategy]()
        if not result or "picked" not in result:
            return None
    except:
        return None

    high_scores = [p for p in result.get("picked", []) if p.get("total_score", p.get("score", 0)) >= min_score]
    if high_scores:
        top = high_scores[0]
        return {
            "alert_id": aid,
            "type": "strategy_signal",
            "message": f"{strategy}策略: {len(high_scores)}只评分≥{min_score} | Top: {top.get('code')} {top.get('name','')} {top.get('total_score',top.get('score',0))}分",
            "data": {"strategy": strategy, "count": len(high_scores), "top": top},
            "time": datetime.now().strftime("%H:%M:%S"),
        }
    return None


# ─── 消息队列投递 ──────────────────────────────────

def _push_to_queue(triggered: List[Dict]):
    """将触发的告警推送到消息队列，供通讯员发送"""
    try:
        queue_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "message_queue.json")
        queue = []
        if os.path.exists(queue_path):
            with open(queue_path) as f:
                queue = json.load(f)

        for t in triggered:
            queue.append({
                "source": "alert",
                "priority": "high" if t["type"] in ("price_break", "pct_change") else "normal",
                "title": t["type"],
                "message": t["message"],
                "data": t["data"],
                "time": t["time"],
                "status": "pending",
            })

        with open(queue_path, "w") as f:
            json.dump(queue, f, indent=1, ensure_ascii=False)
    except Exception as e:
        print(f"[alert] 消息队列投递失败: {e}")
