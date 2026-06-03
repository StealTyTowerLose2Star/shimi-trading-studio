#!/bin/bash
cd /root/shi-mi-dashboard
source venv/bin/activate
python3 daily_reflect.py "$@" 2>&1 | tee -a /root/shi-mi-dashboard/reflect.log
