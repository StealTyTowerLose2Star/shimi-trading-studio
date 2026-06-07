#!/bin/bash
# ───────────────────────────────────────────────
# 拾米交易工作室 · 启动脚本
# 建筑师基础设施: 环境检查 + 日志 + 错误退出
# ───────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

_pass()  { echo -e "${GREEN}✅${NC} $1"; }
_warn() { echo -e "${YELLOW}⚠️${NC}  $1"; }
_fail() { echo -e "${RED}❌${NC} $1"; exit 1; }
_info() { echo -e "${CYAN}▪️${NC}  $1"; }

# ─── 环境检查 ──────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║   🏛️  拾米交易工作室 · ShiMi Trading Studio       ║"
echo "║   建筑师启动检查 ...                               ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""

# Python
_info "Python: $(python3 --version 2>&1)"
python3 -c "import sys; sys.exit(0)" || _fail "Python3 不可用"

# 项目依赖
python3 -c "import flask" 2>/dev/null && _pass "Flask" || _fail "Flask 未安装: pip install flask"
python3 -c "import numpy" 2>/dev/null && _pass "NumPy" || _fail "NumPy 未安装"
python3 -c "import tushare" 2>/dev/null && _pass "Tushare" || _warn "Tushare 未安装 (A股数据不可用)"
python3 -c "import yfinance" 2>/dev/null && _pass "yfinance" || _warn "yfinance 未安装 (美股数据不可用)"
python3 -c "import requests" 2>/dev/null && _pass "requests" || _fail "requests 未安装"

# 配置文件
[ -f ".env" ] && _pass ".env" || _warn ".env 不存在, 请创建并配置密钥"
[ -f "config.py" ] && _pass "config.py" || _fail "config.py 不存在"

# 日志目录
mkdir -p logs && _pass "日志目录: logs/"

# 密钥检查
python3 -c "
import os
from dotenv import load_dotenv
load_dotenv()
ts = os.getenv('TUSHARE_TOKEN','')
fh = os.getenv('FINNHUB_KEY','')
if ts and ts != 'your_token_here':
    print(f'✅ Tushare Token 已配置 ({ts[:6]}...)')
else:
    print('⚠️  Tushare Token 未配置 (A股数据不可用)')
if fh:
    print(f'✅ Finnhub Key 已配置 ({fh[:6]}...)')
else:
    print('⚠️  Finnhub Key 未配置 (美股数据不可用)')
"

# 端口
PORT="${SHIMI_PORT:-7890}"
python3 -c "
import socket
s = socket.socket()
try:
    s.bind(('0.0.0.0', $PORT))
    s.close()
    print(f'✅ 端口 $PORT 可用')
except:
    print(f'⚠️  端口 $PORT 已被占用')
"

echo ""
echo "──────────────────────────────────────────────────"
echo "  🚀 启动服务..."
echo "  🏛️  http://localhost:$PORT"
echo "  📋 日志: logs/shimi.log"
echo "  ❌ 错误: logs/error.log"
echo "──────────────────────────────────────────────────"
echo ""

# ─── 启动 ──────────────────────────────────────
exec python3 backend.py
