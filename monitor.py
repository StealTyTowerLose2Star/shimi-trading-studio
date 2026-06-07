"""
拾米交易工作室 - 服务器资源监控
检测 CPU、内存、磁盘、网络资源使用情况
"""
import os
import time
from typing import Dict

# ─── 告警阈值 ─────────────────────────────────────
ALERT_THRESHOLDS = {
    "cpu_percent": 80,           # CPU 使用率 > 80%
    "memory_percent": 85,        # 内存使用率 > 85%
    "disk_percent": 90,          # 磁盘使用率 > 90%
    "load_15min": 4.0,           # 15分钟负载 > 4.0
    "swap_percent": 50,          # Swap 使用率 > 50%
}

# ─── 读取 /proc 文件系统（无需额外依赖）─────────────────

def _read_proc(path: str) -> str:
    """安全读取 /proc 文件"""
    try:
        with open(path) as f:
            return f.read()
    except:
        return ""


def _parse_proc_stat() -> Dict:
    """解析 /proc/stat 计算 CPU 使用率"""
    data = _read_proc("/proc/stat")
    if not data:
        return {"cpu_percent": 0, "cpu_cores": 0}

    lines = data.strip().split("\n")
    cpu_lines = [l for l in lines if l.startswith("cpu") and l[3].isdigit()]
    total_cores = len(cpu_lines)

    # 取第一行 (总CPU)
    parts = lines[0].split()
    if len(parts) < 5:
        return {"cpu_percent": 0, "cpu_cores": total_cores}

    # user+nice+system+idle+iowait+irq+softirq+steal
    fields = [int(v) for v in parts[1:]]
    total = sum(fields)
    idle = fields[3]  # idle

    # 读取上一次的采样值（跨调用计算）
    _last = getattr(_parse_proc_stat, "_last", None)
    if _last:
        total_delta = total - _last["total"]
        idle_delta = idle - _last["idle"]
        cpu_percent = round((1 - idle_delta / max(total_delta, 1)) * 100, 1)
    else:
        cpu_percent = 0.0

    _parse_proc_stat._last = {"total": total, "idle": idle}

    return {"cpu_percent": cpu_percent, "cpu_cores": total_cores}


def _get_memory() -> Dict:
    """解析 /proc/meminfo 获取内存使用率"""
    data = _read_proc("/proc/meminfo")
    if not data:
        return {"total_mb": 0, "used_mb": 0, "percent": 0, "swap_percent": 0}

    lines = data.strip().split("\n")
    mem = {}
    for line in lines:
        parts = line.split(":")
        if len(parts) == 2:
            key = parts[0].strip()
            val_str = parts[1].strip().split()[0]
            try:
                mem[key] = int(val_str) // 1024  # KB → MB
            except:
                pass

    total = mem.get("MemTotal", 0)
    free = mem.get("MemFree", 0)
    buffers = mem.get("Buffers", 0)
    cached = mem.get("Cached", 0)
    sreclaimable = mem.get("SReclaimable", 0)  # kernel slab reclaimable

    # 实际使用 = total - free - buffers - cached - reclaimable
    used = max(0, total - free - buffers - cached - sreclaimable)
    percent = round(used / max(total, 1) * 100, 1)

    # Swap
    swap_total = mem.get("SwapTotal", 0)
    swap_free = mem.get("SwapFree", 0)
    swap_percent = round((swap_total - swap_free) / max(swap_total, 1) * 100, 1)

    return {
        "total_mb": total,
        "used_mb": used,
        "free_mb": total - used,
        "percent": percent,
        "swap_total_mb": swap_total,
        "swap_used_mb": swap_total - swap_free,
        "swap_percent": swap_percent,
    }


def _get_disk() -> Dict:
    """获取磁盘使用率"""
    try:
        import shutil
        usage = shutil.disk_usage("/")
        total_gb = round(usage.total / (1024**3), 1)
        used_gb = round(usage.used / (1024**3), 1)
        free_gb = round(usage.free / (1024**3), 1)
        percent = round(usage.used / usage.total * 100, 1)
        return {
            "total_gb": total_gb,
            "used_gb": used_gb,
            "free_gb": free_gb,
            "percent": percent,
        }
    except:
        return {"total_gb": 0, "used_gb": 0, "free_gb": 0, "percent": 0}


def _get_load() -> Dict:
    """获取系统负载"""
    data = _read_proc("/proc/loadavg")
    if not data:
        return {"load_1min": 0, "load_5min": 0, "load_15min": 0}
    parts = data.strip().split()[:3]
    return {
        "load_1min": float(parts[0]) if parts else 0,
        "load_5min": float(parts[1]) if len(parts) > 1 else 0,
        "load_15min": float(parts[2]) if len(parts) > 2 else 0,
    }


def _get_uptime() -> int:
    """获取系统运行时间（秒）"""
    data = _read_proc("/proc/uptime")
    if data:
        try:
            return int(float(data.strip().split()[0]))
        except:
            pass
    return 0


def _get_network() -> Dict:
    """获取网络流量（简化版：取总收发字节数）"""
    data = _read_proc("/proc/net/dev")
    if not data:
        return {"rx_bytes": 0, "tx_bytes": 0, "rx_mb": 0, "tx_mb": 0}

    rx_total = 0
    tx_total = 0
    for line in data.strip().split("\n")[2:]:  # skip headers
        parts = line.split()
        if len(parts) >= 10 and parts[0].strip(":") != "lo":
            rx_total += int(parts[1])  # receive bytes
            tx_total += int(parts[9])  # transmit bytes

    return {
        "rx_bytes": rx_total,
        "tx_bytes": tx_total,
        "rx_mb": round(rx_total / (1024**2), 1),
        "tx_mb": round(tx_total / (1024**2), 1),
    }


# ─── 统一接口 ─────────────────────────────────────

# 缓存上次监控数据（用于计算变化率）
_last_monitor = {}


def get_monitor_status() -> Dict:
    """获取服务器资源状态"""
    global _last_monitor

    cpu = _parse_proc_stat()
    mem = _get_memory()
    disk = _get_disk()
    load = _get_load()
    uptime_sec = _get_uptime()
    net = _get_network()

    # 网络速率（MB/s）
    rx_rate = 0
    tx_rate = 0
    if _last_monitor:
        elapsed = time.time() - _last_monitor.get("time", time.time())
        if elapsed > 0:
            rx_rate = round(
                (net["rx_bytes"] - _last_monitor.get("rx_bytes", net["rx_bytes"]))
                / elapsed / (1024**2), 2
            )
            tx_rate = round(
                (net["tx_bytes"] - _last_monitor.get("tx_bytes", net["tx_bytes"]))
                / elapsed / (1024**2), 2
            )

    _last_monitor = {
        "time": time.time(),
        "rx_bytes": net["rx_bytes"],
        "tx_bytes": net["tx_bytes"],
    }

    # 告警检查
    alerts = []
    thresholds = ALERT_THRESHOLDS

    if cpu["cpu_percent"] > thresholds["cpu_percent"] and cpu["cpu_percent"] > 0:
        alerts.append({
            "level": "warning",
            "type": "cpu",
            "message": f"CPU 使用率 {cpu['cpu_percent']}% 超过阈值 {thresholds['cpu_percent']}%",
        })
    if mem["percent"] > thresholds["memory_percent"]:
        alerts.append({
            "level": "warning",
            "type": "memory",
            "message": f"内存使用率 {mem['percent']}% 超过阈值 {thresholds['memory_percent']}%",
        })
    if disk["percent"] > thresholds["disk_percent"]:
        alerts.append({
            "level": "critical",
            "type": "disk",
            "message": f"磁盘使用率 {disk['percent']}% 超过阈值 {thresholds['disk_percent']}%",
        })
    if load["load_15min"] > thresholds["load_15min"]:
        alerts.append({
            "level": "warning",
            "type": "load",
            "message": f"系统负载 {load['load_15min']} 超过阈值 {thresholds['load_15min']}",
        })
    if mem["swap_percent"] > thresholds["swap_percent"]:
        alerts.append({
            "level": "warning",
            "type": "swap",
            "message": f"Swap 使用率 {mem['swap_percent']}% 超过阈值 {thresholds['swap_percent']}%",
        })

    # 格式化时间
    days = uptime_sec // 86400
    hours = (uptime_sec % 86400) // 3600
    mins = (uptime_sec % 3600) // 60

    return {
        "cpu": {
            "percent": cpu["cpu_percent"],
            "cores": cpu["cpu_cores"],
        },
        "memory": mem,
        "disk": disk,
        "load": load,
        "network": {
            "rx_rate_mbps": rx_rate,
            "tx_rate_mbps": tx_rate,
            "rx_total_mb": net["rx_mb"],
            "tx_total_mb": net["tx_mb"],
        },
        "uptime": {
            "seconds": uptime_sec,
            "display": f"{days}d {hours}h {mins}m",
        },
        "alerts": alerts,
        "alert_count": len(alerts),
        "has_alerts": len(alerts) > 0,
        "timestamp": time.strftime("%H:%M:%S"),
    }


# ─── 外部依赖健康检查 ──────────────────────────────

def check_external_deps() -> Dict:
    """检查外部 API 依赖是否可达

    Returns:
        dict: {
            "tushare": {"reachable": bool, "latency_ms": float, "message": str},
            "finnhub":  {...},
            "yfinance": {...},
            "overall": "healthy" | "degraded" | "down"
        }
    """
    import requests
    import config

    results = {}
    healthy_count = 0
    total = 0

    # Tushare Pro
    total += 1
    try:
        t0 = time.time()
        r = requests.post("https://api.tushare.pro", json={
            "api_name": "trade_cal",
            "token": config.TUSHARE_TOKEN,
            "params": {"exchange": "SSE", "start_date": "20260101", "end_date": "20260101"},
        }, timeout=5)
        latency = (time.time() - t0) * 1000
        if r.status_code == 200:
            data = r.json()
            if data.get("code") == 0:
                results["tushare"] = {"reachable": True, "latency_ms": round(latency, 0), "message": "正常"}
                healthy_count += 1
            else:
                results["tushare"] = {"reachable": False, "latency_ms": round(latency, 0), "message": f"API错误: {data.get('msg', 'unknown')}"}
        else:
            results["tushare"] = {"reachable": False, "latency_ms": round(latency, 0), "message": f"HTTP {r.status_code}"}
    except Exception as e:
        results["tushare"] = {"reachable": False, "latency_ms": 0, "message": str(e)[:80]}

    # Finnhub
    total += 1
    try:
        finnhub_key = os.getenv("FINNHUB_KEY", "")
        t0 = time.time()
        r = requests.get(f"https://finnhub.io/api/v1/quote?symbol=AAPL&token={finnhub_key}", timeout=5)
        latency = (time.time() - t0) * 1000
        if r.status_code == 200:
            data = r.json()
            if "c" in data:
                results["finnhub"] = {"reachable": True, "latency_ms": round(latency, 0), "message": f"AAPL ${data.get('c', '?')}"}
                healthy_count += 1
            elif data.get("error"):
                results["finnhub"] = {"reachable": False, "latency_ms": round(latency, 0), "message": data.get("error", "API错误")}
            else:
                results["finnhub"] = {"reachable": True, "latency_ms": round(latency, 0), "message": "正常"}
                healthy_count += 1
        else:
            results["finnhub"] = {"reachable": False, "latency_ms": round(latency, 0), "message": f"HTTP {r.status_code}"}
    except Exception as e:
        results["finnhub"] = {"reachable": False, "latency_ms": 0, "message": str(e)[:80]}

    # yfinance (通过 Finnhub 间接验证，不做额外请求)
    total += 1
    try:
        import yfinance as yf
        t0 = time.time()
        tk = yf.Ticker("AAPL")
        info = tk.info
        latency = (time.time() - t0) * 1000
        if info and info.get("symbol"):
            results["yfinance"] = {"reachable": True, "latency_ms": round(latency, 0), "message": "正常"}
            healthy_count += 1
        else:
            results["yfinance"] = {"reachable": False, "latency_ms": round(latency, 0), "message": "返回空数据"}
    except Exception as e:
        results["yfinance"] = {"reachable": False, "latency_ms": 0, "message": str(e)[:80]}

    # 综合状态
    if healthy_count == total:
        overall = "healthy"
    elif healthy_count > 0:
        overall = "degraded"
    else:
        overall = "down"

    results["overall"] = overall
    results["healthy_count"] = healthy_count
    results["total_count"] = total
    results["timestamp"] = time.strftime("%Y-%m-%d %H:%M:%S")

    return results

