#!/bin/bash
# 拾米交易工作室 - 复盘定时发送包装脚本
# 被 cron 调用，先激活 venv 再执行
cd /root/shi-mi-dashboard
source venv/bin/activate
python3 review_sender.py "$1" 2>> /root/shi-mi-dashboard/review_sender.log
