#!/bin/bash
# ───────────────────────────────────────────────
# 拾米交易工作室 · 哨兵 · 数据库备份脚本
# cron: 0 2 * * * /root/shimi-trading-studio/cron_db_backup.sh
# ───────────────────────────────────────────────
set -euo pipefail

# Hermes cron 从 /root/.hermes/scripts/ 执行本脚本副本，必须硬编码项目路径
PROJECT_DIR="/root/shimi-trading-studio"
BACKUP_DIR="/root/shimi-backups"
DB_FILE="$PROJECT_DIR/shimi.db"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/shimi_$TIMESTAMP.db.gz"
KEEP_DAYS=30

mkdir -p "$BACKUP_DIR"

# ─── 备份 ──────────────────────────────────────
if [ -f "$DB_FILE" ]; then
    cp "$DB_FILE" "$BACKUP_DIR/shimi_$TIMESTAMP.db"
    gzip "$BACKUP_DIR/shimi_$TIMESTAMP.db"
    echo "[$(date '+%Y-%m-%d %H:%M')] ✅ 备份完成: $BACKUP_FILE ($(du -h "$BACKUP_FILE" | cut -f1))"
else
    echo "[$(date '+%Y-%m-%d %H:%M')] ⚠️ 数据库文件不存在: $DB_FILE"
    exit 1
fi

# ─── 清理旧备份 ────────────────────────────────
find "$BACKUP_DIR" -name "shimi_*.db.gz" -mtime +$KEEP_DAYS -delete 2>/dev/null || true
echo "[$(date '+%Y-%m-%d %H:%M')] 🧹 清理完成 (>${KEEP_DAYS}天)"

# ─── 备份计数 ──────────────────────────────────
COUNT=$(ls "$BACKUP_DIR"/shimi_*.db.gz 2>/dev/null | wc -l)
echo "[$(date '+%Y-%m-%d %H:%M')] 📦 当前备份数: $COUNT"
