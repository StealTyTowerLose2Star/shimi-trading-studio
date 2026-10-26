#!/usr/bin/env bash
# real 模式服务端验证 (在 http://127.0.0.1:17890 上执行; 数据源 = Tushare Pro 日线 EOD)
# 注意: /api/us/quote 的 us_daily 接口官方限 1 次/小时, 撞限时返回明确错误也属合规行为
B=http://127.0.0.1:17890
echo "== 1 模式头 =="
curl -s -D- -o /dev/null --max-time 10 $B/api/health | grep -i "x-shimi"
echo "== 2 健康检查 (仅服务存活, 不触发数据拉取) =="
curl -s --max-time 10 $B/api/health
echo; echo "== 3 A股指数真实数据 (EOD 最近收盘) =="
curl -s --max-time 60 $B/api/indices | venv/Scripts/python.exe -X utf8 -c "
import sys, json
for r in json.load(sys.stdin):
    print(' ', r['name'], r['price'], f\"{r['change']}%\", '|', r['trade_date'], '|', r['source'])
"
sleep 2
echo "== 4 贵州茅台 600519 K线 (最近3日真实收盘) =="
curl -s --max-time 60 "$B/api/stock/600519/kline?days=10" | venv/Scripts/python.exe -X utf8 -c "
import sys, json
d = json.load(sys.stdin)
print('  name:', d.get('name'), ' days:', d.get('days'))
for r in d.get('kline', [])[-3:]:
    print(' ', r['date'], 'close:', r['close'], 'volume:', r['volume'])
"
sleep 2
echo "== 5 美股 AAPL 最近收盘 (EOD; 限1次/小时) =="
curl -s --max-time 30 "$B/api/us/quote?symbol=AAPL"
sleep 2
echo; echo "== 6 美股非法代码 (应 400) =="
curl -s -o /dev/null -w "%{http_code}\n" --max-time 10 "$B/api/us/quote?symbol=../../etc"
echo "== 7 搜索 lookup 600519 =="
curl -s --max-time 30 "$B/api/stock/lookup?q=600519" | head -c 200
sleep 2
echo; echo "== 8 敏感路径不回退 (应全 404) =="
for p in /.env /.local_token /config.py /daily_store.db /.git/config /data/users.db; do
  echo "  $p -> $(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$B$p")"
done
echo "== 9 交易/推送保持禁用 (应全 403) =="
curl -s -o /dev/null -w "GET /api/trades -> %{http_code}\n" --max-time 5 $B/api/trades
curl -s -o /dev/null -w "POST /api/trades -> %{http_code}\n" --max-time 5 -X POST $B/api/trades
curl -s -o /dev/null -w "POST /api/strategy/trend/refresh -> %{http_code} (应403, 仅GET白名单)\n" --max-time 5 -X POST $B/api/strategy/trend/refresh
curl -s -o /dev/null -w "GET /api/market/events -> %{http_code} (应403, news系积分不足)\n" --max-time 5 $B/api/market/events
echo "== 10 首页横幅 (凭据态) =="
curl -s --max-time 10 $B/ | grep -oE "(ℹ️|⚠️) 仅展示真实来源数据[^<]{0,50}" | head -1
echo "== 11 模块状态 =="
curl -s --max-time 10 $B/api/local/modules | venv/Scripts/python.exe -X utf8 -c "
import sys, json
d = json.load(sys.stdin)
print('  mode:', d['mode'], '| backend:', d['data_backend'],
      '| real_data_only:', d['real_data_only'], '| trading:', d['trading_enabled'])
"
echo "== 12 情绪四维 (真实全市场统计) =="
curl -s --max-time 90 $B/api/sentiment | venv/Scripts/python.exe -X utf8 -c "
import sys, json
d = json.load(sys.stdin)
print('  phase:', d.get('phase'), '| score:', d.get('total_score'),
      '| up/down:', d.get('up'), '/', d.get('down'),
      '| 涨停/跌停:', d.get('limit_up'), '/', d.get('limit_down'), '| date:', d.get('date'))
"

echo "== 13 新启用模块真实样例 (策略/雷达/翻倍/两融/复盘/缓存/监测) =="
for strat in trend hybrid dragon; do
echo "-- 13.x 策略 $strat (首次冷跑可达80s) --"
curl -s --max-time 240 $B/api/strategy/$strat | venv/Scripts/python.exe -X utf8 -c "
import sys, json
d = json.load(sys.stdin)
picked = d.get('picked') or []
print('  picked:', len(picked), '只 | total_scanned:', d.get('total_scanned'), '| engine:', d.get('engine'))
for p in picked[:3]:
    score = p.get('trend_score', p.get('score', p.get('leader_score')))
    print('  ', p.get('code'), p.get('name'), 'score:', score, 'price:', p.get('price'),
          '| grade:', p.get('grade', p.get('stage', '')))
if 'error' in d: print('  ERROR:', d['error'])
"
sleep 2
done
sleep 2
echo "-- 13.4 个股雷达 600519 (含真实ROE) --"
curl -s --max-time 90 $B/api/stock/600519/radar | venv/Scripts/python.exe -X utf8 -c "
import sys, json
d = json.load(sys.stdin)
r = d.get('radar') or {}
print('  综合:', r.get('综合'), '| label:', (d.get('conclusion') or {}).get('label'))
f = d.get('fundamental') or {}
print('  ROE:', f.get('roe'), '| 毛利率:', f.get('gross_margin'), '| 期:', f.get('period'))
print('  price:', d.get('price'))
if 'error' in d: print('  ERROR:', d['error'])
"
sleep 2
echo "-- 13.5 月度翻倍推荐 (首次约2分钟) --"
curl -s --max-time 300 $B/api/doubler/recommend | venv/Scripts/python.exe -X utf8 -c "
import sys, json
d = json.load(sys.stdin)
elite = d.get('elite_picks') or []
print('  elite_picks:', len(elite), '只 | month:', d.get('month'))
for p in elite[:3]:
    print('  ', p.get('code'), p.get('name'), 'total:', p.get('total_score'))
if 'error' in d: print('  ERROR:', d['error'])
"
sleep 2
echo "-- 13.6 两融 600519 (真实余额) --"
curl -s --max-time 60 "$B/api/margin?code=600519" | venv/Scripts/python.exe -X utf8 -c "
import sys, json
d = json.load(sys.stdin)
rows = d.get('data') or []
print('  rows:', len(rows))
for r in rows[:2]:
    print('  ', r.get('trade_date'), r.get('name'), 'rzye:', r.get('rzye'), 'rqye:', r.get('rqye'), '| src:', r.get('source'))
"
sleep 2
echo "-- 13.7 日复盘 GET (无3天前推荐时显式提示, 不算缺陷) --"
curl -s --max-time 30 $B/api/review/daily | head -c 300
echo
sleep 2
echo "-- 13.8 A股缓存摘要 --"
curl -s --max-time 30 $B/api/a-stock/cache/summary | venv/Scripts/python.exe -X utf8 -c "
import sys, json
d = json.load(sys.stdin)
print('  latest_trade_date:', d.get('latest_trade_date'), '| keys:', len(d.get('keys') or d.get('items') or []))
print('  raw:', json.dumps(d, ensure_ascii=False)[:240])
"
sleep 2
echo "-- 13.9 运行监测 --"
curl -s --max-time 30 $B/api/monitor | venv/Scripts/python.exe -X utf8 -c "
import sys, json
d = json.load(sys.stdin)
print('  ', json.dumps(d, ensure_ascii=False)[:300])
"
echo "== 13 完成 =="
