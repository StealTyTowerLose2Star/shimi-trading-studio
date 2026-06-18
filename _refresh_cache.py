#!/usr/bin/env python3
"""Refresh API cache after scan."""
import sys, os, json
sys.path.insert(0, '/root/shimi-trading-studio')
os.chdir('/root/shimi-trading-studio')

from dotenv import load_dotenv
load_dotenv('/root/shimi-trading-studio/.env')

from cache import cache_delete, cache_set

# Delete old cache to force refresh
cache_delete('doubler_recommend_v4')

# Re-load scan result and populate cache
with open('current_month_picks_v2.json') as f:
    result = json.load(f)
cache_set('doubler_recommend_v4', result, 300)
print('✅ API缓存已刷新 (doubler_recommend_v4)')
