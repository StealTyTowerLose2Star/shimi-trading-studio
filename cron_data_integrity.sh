#!/bin/bash
# ───────────────────────────────────────────────
# 拾米交易工作室 · 哨兵 · 数据完整性检查
# cron: 0 1 * * * /root/shimi-trading-studio/cron_data_integrity.sh
# ───────────────────────────────────────────────
set -euo pipefail

# Hermes cron 从 /root/.hermes/scripts/ 执行本脚本副本，必须硬编码项目路径
PROJECT_DIR="/root/shimi-trading-studio"
cd "$PROJECT_DIR"

PASS=0; WARN=0; FAIL=0
_pass() { echo "  ✅ $1"; PASS=$((PASS+1)); }
_warn() { echo "  ⚠️  $1"; WARN=$((WARN+1)); }
_fail() { echo "  ❌ $1"; FAIL=$((FAIL+1)); }

echo "[$(date '+%Y-%m-%d %H:%M')] 哨兵 · 数据完整性检查"

# 1. 数据库文件
[ -f shimi.db ] && _pass "shimi.db存在" || _fail "shimi.db缺失"
DB_SIZE=$(du -h shimi.db 2>/dev/null | cut -f1)
echo "     大小: $DB_SIZE"

# 2. 缓存文件
[ -f services/alerts.json ] && _pass "alerts.json存在" || _warn "alerts.json缺失"
[ -f message_queue.json ] && _pass "message_queue.json存在" || _warn "message_queue.json缺失"

# 3. 环境变量
python3 -c "
import os; from dotenv import load_dotenv; load_dotenv()
ts=os.getenv('TUSHARE_TOKEN',''); fh=os.getenv('FINNHUB_KEY','')
if ts and len(ts)>10: print('TUSHARE_TOKEN: OK')
else: print('TUSHARE_TOKEN: MISSING')
if fh and len(fh)>10: print('FINNHUB_KEY: OK')
else: print('FINNHUB_KEY: MISSING')
" 2>/dev/null | while read line; do
    [[ "$line" =~ OK ]] && _pass "$line" || _warn "$line"
done

# 4. 关键Python模块
for mod in flask numpy pandas tushare yfinance; do
    python3 -c "import $mod" 2>/dev/null && _pass "$mod可导入" || _fail "$mod缺失"
done

# 5. 备份检查
BACKUP_DIR="/root/shimi-backups"
if [ -d "$BACKUP_DIR" ]; then
    COUNT=$(ls "$BACKUP_DIR"/shimi_*.db.gz 2>/dev/null | wc -l)
    [ "$COUNT" -gt 0 ] && _pass "备份: ${COUNT}个" || _warn "无备份文件"
else
    _warn "备份目录不存在"
fi

echo ""
echo "──────────────────────────────────────────"
echo "  通过:$PASS  警告:$WARN  失败:$FAIL"
[ "$FAIL" -gt 0 ] && echo "  ⚠️ 存在失败项，请检查"
echo "──────────────────────────────────────────"
