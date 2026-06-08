#!/bin/bash
# ───────────────────────────────────────────────
# 拾米交易工作室 · 拾米A股 · 每日数据刷新
# Hermes cron: 0 16 * * * /root/shimi-trading-studio/cron_daily_refresh.sh
# 功能: 每日16:00 调 Tushare 拉全量数据 → 存本地 → 淘汰旧数据
# ───────────────────────────────────────────────
set -euo pipefail

## ─── 路径 ──────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$SCRIPT_DIR/logs"
LOG_FILE="$LOG_DIR/daily_refresh_$(date +%Y%m%d_%H%M%S).log"
VENV_PYTHON="${SCRIPT_DIR}/.venv/bin/python3"
SYSTEM_PYTHON="/usr/bin/python3"

## ─── 选择 Python ───────────────────────────────
PYTHON=""
for candidate in "$VENV_PYTHON" "$SYSTEM_PYTHON" "python3"; do
    if command -v "$candidate" &>/dev/null; then
        PYTHON="$candidate"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    echo "[$(date '+%Y-%m-%d %H:%M')] ❌ Python3 未找到"
    exit 1
fi

mkdir -p "$LOG_DIR"

## ─── Python 刷新脚本 ─────────────────────────
exec > "$LOG_FILE" 2>&1

echo "============================================"
echo "📅 每日数据刷新 | $(date '+%Y-%m-%d %H:%M:%S')"
echo "============================================"
echo "Python: $PYTHON"
echo "CWD:    $SCRIPT_DIR"
echo ""

# ─── 第1步: 刷新当日全量数据 ───────────────
echo "🔄 [1/3] 刷新当日全量缓存..."
cd "$SCRIPT_DIR"
$PYTHON -c "
import sys
sys.path.insert(0, '.')
import json
from data.fetcher_cached import refresh_daily, get_cache_summary
from data.daily_store import get_store

# 刷新
result = refresh_daily(market='all')
print(f'刷新增量: {result}')

# 淘汰过期数据 (daily/daily_basic 保留5天)
store = get_store()
pruned = store.prune('daily')
print(f'淘汰 daily: {pruned}')

pruned = store.prune('daily_basic')
print(f'淘汰 daily_basic: {pruned}')

# 淘汰其他类型
for t in ['sentiment', 'limit_up', 'limit_down', 'indices', 'sectors']:
    pruned = store.prune(t)
    if pruned.get(t, 0) > 0:
        print(f'淘汰 {t}: {pruned}')

# 报告
summary = get_cache_summary()
print(f'缓存总览: {summary[\"total_records\"]}条 / {summary[\"db_size_mb\"]}MB')
print(json.dumps(summary, ensure_ascii=False))
" 2>&1

if [ $? -eq 0 ]; then
    REFRESH_STATUS="✅ 成功"
else
    REFRESH_STATUS="❌ 失败"
fi

echo ""
echo "🔄 [2/3] 刷新股票基础信息 (周级)..."
$PYTHON -c "
import sys
sys.path.insert(0, '.')
from data.fetcher_cached import get_stock_basic_cached
data = get_stock_basic_cached(force_refresh=True)
print(f'股票基础: {len(data)} 只')
" 2>&1

echo ""
echo "🔄 [3/3] 报告缓存状态..."
$PYTHON -c "
import sys
sys.path.insert(0, '.')
from data.fetcher_cached import get_cache_summary
from data.daily_store import get_store
s = get_store().summary()
print(f'数据库: {s[\"db_path\"]}')
print(f'大小: {s[\"db_size_mb\"]}MB')
print(f'记录数: {s[\"total_records\"]}')
for t, c in s.get('by_type', {}).items():
    print(f'  {t}: {c}条')
" 2>&1

echo ""
echo "============================================"
echo "📊 刷新 | $REFRESH_STATUS"
echo "📋 日志 | $LOG_FILE"
echo "============================================"

## ─── 清理旧日志 (保留7天) ────────────────────
find "$LOG_DIR" -name "daily_refresh_*.log" -mtime +7 -delete 2>/dev/null || true
