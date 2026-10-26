// Pure code/DOM-stub tests, NOT browser automation or a real Chrome visual test.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname, 'public/index.html'), 'utf8');
let compiled = 0;
for (const match of html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)) {
  if (/\bsrc\s*=/.test(match[1])) continue;
  new vm.Script(match[2]);
  compiled++;
}
function segment(start, end) {
  const a = html.indexOf(start), b = html.indexOf(end, a);
  assert(a >= 0 && b > a);
  return html.slice(a, b);
}
const nodes = new Map();
function element(tagName='DIV') {
  return {tagName, innerHTML: '', textContent: '', style: {}, children: [],
    appendChild(child) {this.children.push(child); this.textContent=this.children.map(c=>c.textContent).join('');},
    replaceChildren(...children) {this.children = children; this.textContent = children.map(c=>c.textContent).join('');},
    querySelectorAll() {return this.cards || [];}};
}
const document = {getElementById(id) {
  if (!nodes.has(id)) nodes.set(id, element(id.endsWith('Body')?'TBODY':'DIV'));
  return nodes.get(id);
}, createElement: tag => element(tag.toUpperCase())};
const ctx = vm.createContext({document, console, fetch: async()=>{throw new Error('network forbidden');}});
vm.runInContext(segment('function $(id)', '// ===== Clock =====') +
  segment('async function fetchJSON', 'async function loadModuleReadiness') +
  segment('function renderIndices', '// ===== Main Load =====') +
  segment('// ===== 市场切换', '// ─── 海淘面板数据加载') +
  segment('function showLoginPrompt()', 'function setUser('), ctx);
// switchMarket 美股分支的下游加载函数 — 桩为空函数, 不发任何请求
ctx.refreshHaitao=()=>{}; ctx.loadProphetSignals=()=>{}; ctx.loadUSMarketEvents=()=>{};
let passed = 0;
function test(name, fn) {fn(); passed++; console.log('PASS '+name);}
test('unknown values do not format as zero', () => {
  for(const value of [null, undefined, false, true, NaN, Infinity, '', '0']) assert.equal(ctx.fmt(value), '--');
  assert.equal(ctx.fmt(0), '0.00'); assert.equal(ctx.fmt(-1), '-1.00');
});
test('change labels preserve real zero and negative one percent', () => {
  assert.equal(ctx.changeLabel(null), '--');
  assert(ctx.changeLabel(-1).includes('1.00%')); assert(ctx.changeLabel(0).includes('0.00%'));
});
test('indices clear stale missing cards and label EOD source', () => {
  const root=document.getElementById('indicesRow'); root.cards=[element(),element(),element(),element()];
  root.cards.forEach(c=>c.innerHTML='STALE');
  ctx.renderIndices([{name:'<unsafe>',price:10,change:-1,source:'tushare-index_daily',trade_date:'2026-10-22'}]);
  assert(root.cards[0].innerHTML.includes('&lt;unsafe&gt;'));
  assert(!root.cards[1].innerHTML.includes('STALE'));
  assert(document.getElementById('dataSourceLabel').textContent.includes('日线'));
  ctx.renderIndices([]); assert(root.cards.every(c=>c.innerHTML.includes('--')));
});
test('hot stocks distinguish missing values and real zero', () => {
  ctx.renderHotStocks([{code:'fictional',name:'demo',price:10,change:0,volume:0,turnover:null}]);
  const value=document.getElementById('hotStockBody').innerHTML;
  assert(value.includes('0.0亿')); assert(value.includes('--')); assert(!value.includes('null'));
});
test('sector missing denominator is not invented zero', () => {
  ctx.renderSectors([{name:'demo',change:null,up_count:null,down_count:null}]);
  assert(document.getElementById('sectorBody').innerHTML.includes('--/--'));
});

test('sector totals require flat counts and reject inconsistent or invalid denominators', () => {
  assert.equal(ctx.sectorBreadthLabel({up_count:2,down_count:1}),'2/--');
  assert.equal(ctx.sectorBreadthLabel({up_count:2,down_count:1,flat_count:1}),'2/4');
  assert.equal(ctx.sectorBreadthLabel({up_count:2,down_count:1,flat_count:0}),'2/3');
  assert.equal(ctx.sectorBreadthLabel({up_count:0,down_count:0,flat_count:0}),'0/0');
  assert.equal(ctx.sectorBreadthLabel({up_count:2,down_count:1,flat_count:1,stock_count:3}),'2/--');
  for(const bad of [null,undefined,-1,0.5,true,NaN,Infinity,'0']) {
    assert.equal(ctx.sectorBreadthLabel({up_count:2,down_count:1,flat_count:bad}),'2/--');
  }
  ctx.renderSectors([{name:'test-only',up_count:2,down_count:1}]);
  assert(document.getElementById('sectorBody').innerHTML.includes('2/--'));
});

test('derived sector strength is not labelled as actual capital inflow', () => {
  assert(html.includes('板块涨跌强度 · 计算指标，非资金净流入'));
  assert(!html.includes('板块资金流 · 强度排行'));
});
test('sentiment error clears phase and all stale values', () => {
  for(const id of ['sentimentPhase','sentimentGrid','sentimentVS']) document.getElementById(id).textContent='STALE';
  ctx.renderSentiment(null);
  for(const id of ['sentimentPhase','sentimentGrid','sentimentVS']) assert(!document.getElementById(id).textContent.includes('STALE'));
});
test('sector zero denominator does not render null as neutral strength', () => {
  ctx.renderSectorFlow([{name:'demo',change:0,up_ratio:null,strength:null,stock_count:0,hot:null}]);
  const value=document.getElementById('sectorFlowBody').innerHTML;
  assert(!value.includes('null')); assert(value.includes('--')); assert(!value.includes('NaN'));
});

test('strategy errors and missing fields do not become zero scans or no signals', () => {
  for (const [fn,prefix] of [[ctx.renderTrend,'trend'],[ctx.renderHybrid,'hybrid'],[ctx.renderDragon,'dragon']]) {
    for(const data of [null,{}, {picked:[]}, {picked:null,total_scanned:0},
      {picked:[],total_scanned:null}, {picked:[],total_scanned:NaN},
      {picked:[],total_scanned:1.5}, {picked:[],total_scanned:-1},
      {picked:[{}],total_scanned:0}, {picked:[],total_scanned:0,_mock:true}]) {
      document.getElementById(prefix+'Body').innerHTML='STALE';
      document.getElementById(prefix+'Count').textContent='STALE';
      fn(data);
      assert.equal(document.getElementById(prefix+'Count').textContent,'未接通');
      assert(document.getElementById(prefix+'Body').textContent.includes('不能判断无信号'));
    }
  }
});

test('valid zero scans and valid empty results stay distinguishable from failure', () => {
  ctx.renderTrend({picked:[],total_scanned:0});
  assert.equal(document.getElementById('trendCount').textContent,'扫描 0 条');
  assert(document.getElementById('trendBody').innerHTML.includes('暂无信号'));
  ctx.renderHybrid({picked:[],total_scanned:4});
  assert.equal(document.getElementById('hybridCount').textContent,'扫描 4 条');
});

test('unavailable trade history never claims zero activity or zero profit', () => {
  ctx.showLoginPrompt();
  for(const id of ['tjTotal','tjWinRate','tjPnl']) assert.equal(document.getElementById(id).textContent,'--');
  assert(document.getElementById('tradeJournalBody').innerHTML.includes('不代表零交易或零盈亏'));
});

test('trade placeholders are unknown even before JavaScript runs', () => {
  for(const id of ['tjTotal','tjWinRate','tjPnl']) {
    const match=html.match(new RegExp('id="'+id+'"[^>]*>([^<]+)<'));
    assert(match); assert.equal(match[1],'--');
  }
});

test('calculated sentiment is not called simulated or exchange-published', () => {
  ctx.renderSentiment({phase:'test-only',total_score:50});
  const value=document.getElementById('sentimentVS').innerHTML;
  assert(value.includes('非交易所直接发布')); assert(!value.includes('模拟演示'));
});

// ─── 切换市场不制造行情时间 (20261023-1756) ───
const clockRe = /\d{1,2}:\d{2}/;
test('switch back to A-share without any successful fetch never shows local clock time', () => {
  vm.runInContext('lastAshareSourceLabel = null', ctx); // 从未取得的历史状态
  const label = document.getElementById('dataSourceLabel');
  label.textContent = 'A 股 · 加载中';
  ctx.switchMarket('ashare');
  assert(!clockRe.test(label.textContent), '不得出现本机时分秒');
  assert(label.textContent.includes('未取得'));
});
test('EOD source label survives switching to US and back', () => {
  const root=document.getElementById('indicesRow'); root.cards=[element(),element(),element(),element()];
  ctx.renderIndices([{name:'demo',price:10,change:0,source:'tushare-index_daily',trade_date:'2026-10-22'}]);
  const eod = document.getElementById('dataSourceLabel').textContent;
  assert(eod.includes('日线'));
  ctx.switchMarket('us');
  assert(document.getElementById('dataSourceLabel').textContent.includes('海淘'));
  ctx.switchMarket('ashare');
  assert.equal(document.getElementById('dataSourceLabel').textContent, eod);
});
test('A-share async render during US view does not pollute the current market label', () => {
  vm.runInContext('lastAshareSourceLabel = null', ctx);
  ctx.switchMarket('us');
  const us = document.getElementById('dataSourceLabel').textContent;
  ctx.renderIndices([{name:'demo',price:10,change:1,source:'tushare-index_daily',trade_date:'2026-10-22'}]);
  assert.equal(document.getElementById('dataSourceLabel').textContent, us); // 美股标签不被A股渲染覆盖
  ctx.switchMarket('ashare'); // 切回呈现已记录的A股EOD标签, 而非切换时刻
  const back = document.getElementById('dataSourceLabel').textContent;
  assert(back.includes('日线')); assert(!clockRe.test(back));
});
test('failure/unknown state survives repeated switching without inventing freshness', () => {
  ctx.renderIndices([]); // 记录"指数数据未取得"
  for (const target of ['us','ashare','us','ashare']) ctx.switchMarket(target);
  const label = document.getElementById('dataSourceLabel').textContent;
  assert(label.includes('未取得')); assert(!clockRe.test(label));
});

test('partial dashboard source failure invalidates saved EOD without overwriting US label', () => {
  ctx.switchMarket('ashare');
  ctx.renderIndices([{name:'fictional',price:10,change:0,source:'tushare-index_daily',trade_date:'2026-10-20'}]);
  ctx.switchMarket('us');
  const label = document.getElementById('dataSourceLabel');
  const us = label.textContent;
  ctx.moduleUnavailable(['indicesRow','dataSourceLabel'],new Error('offline source failure'));
  assert.equal(label.textContent, us);
  ctx.switchMarket('ashare');
  assert(label.textContent.includes('数据未取得'));
  assert(label.textContent.includes('offline source failure'));
  assert(!label.textContent.includes('日线收盘值'));
  assert(!clockRe.test(label.textContent));
});

(async()=>{
  ctx.fetch = async()=>({ok:false,status:502,json:async()=>({error:'source missing'})});
  await assert.rejects(ctx.fetchJSON('/synthetic-only'), /source missing/);
  passed++; console.log('PASS HTTP error cannot be successful payload');
  console.log(`PASS ${passed} synthetic display checks; ${compiled} inline scripts parsed; external I/O=0`);
})().catch(e=>{console.error(e);process.exitCode=1;});
