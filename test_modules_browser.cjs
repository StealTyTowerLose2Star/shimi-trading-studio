const {chromium}=require('playwright');
const path=require('path');
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 const context=await browser.newContext({viewport:{width:1365,height:1000}});
 const page=await context.newPage(); const errors=[]; const absentPanels=[];
 page.setDefaultTimeout(8000);
 page.on('pageerror', e=>errors.push(e.stack));
 try {
  await page.goto('http://127.0.0.1:17890/',{waitUntil:'domcontentloaded'});
  await page.waitForFunction(()=>document.querySelectorAll('#localModuleReadiness p').length===11);
  await page.waitForFunction(()=>document.querySelectorAll('#indicesRow .idx-price').length===4 && !document.querySelector('#indicesRow').textContent.includes('--'),null,{timeout:30000});
  for(const id of ['sectorBody','sectorFlowBody']) {
   const text=await page.locator('#'+id).textContent();
   if(!text || text.trim().length<2)throw new Error(id+'真实数据内容为空');
   if(/模拟|扫描中|undefined|NaN/.test(text))throw new Error(id+'错误状态: '+text);
  }
  for(const id of ['trendBody','hybridBody','dragonBody']) {
   const text=await page.locator('#'+id).textContent()||'';
   if(/模拟|扫描中|undefined|NaN/.test(text))throw new Error(id+'错误状态: '+text);  // 策略未启用: 允许空/说明文案, 禁止模拟或坏值
  }
  await page.locator('#stockSearch').fill('600');
  await page.evaluate(()=>searchStocks());
  // 真实数据 lookup 有网络延迟: 等待下拉真实结果渲染后再点击 (不再依赖瞬时 mock)
  await page.waitForFunction(()=>document.querySelector('#stockDropdown')?.innerHTML.includes('showKlineChart'),null,{timeout:15000});
  await page.locator('#stockDropdown > div').first().click();
  await page.locator('#klineChart canvas').waitFor({state:'visible',timeout:20000});
  await page.locator('#klineModal').getByRole('button',{name:'30日',exact:true}).click();
  await page.waitForFunction(()=>document.getElementById('klineLoading').style.display==='none');
  await page.locator('#klineModal').getByRole('button',{name:'📊 雷达分析',exact:true}).click();
  // 雷达 2026-10-25 起启用真实分析 (K线/基本面/两融): 等待真实渲染, 禁止空壳/undefined
  await page.waitForFunction(()=>document.getElementById('radarPanel').textContent.includes('快速摘要'),null,{timeout:40000});
  if((await page.locator('#radarPanel').textContent()).includes('undefined'))throw new Error('雷达错误伪装为评分');
  await page.locator('#klineModal').getByRole('button',{name:'✕',exact:true}).click();
  await page.evaluate(async()=>{await loadDoublerPanel();await loadCacheStatus();await loadMarketEvents(false);await loadProphetSignals('ashare',false);});
  // 翻倍/缓存 2026-10-25 起真实启用 (日级缓存, 服务端已预热): 应显示真实计数而非"未接通"
  await page.waitForFunction(()=>/\d+只精选/.test(document.getElementById('doublerCount').textContent),null,{timeout:180000});
  if(!/\d+条/.test(await page.locator('#cacheRecordCount').textContent()))throw new Error('cacheRecordCount 未显示真实缓存计数');
  // 事件/先知 保持未启用 (tushare news 系实测40203无权限): 必须显式"未接通"
  for(const id of ['marketEventsCount','prophetASigCount']){
   if(await page.locator('#'+id).count()===0){absentPanels.push(id);continue;}
   if(!(await page.locator('#'+id).textContent()).includes('未接通'))throw new Error(id+'未显示缺项');
  }
  await page.evaluate(async()=>{await loadReview('daily');await loadPortfolioAdvice();await renderCalendar();await loadMarginData('600519');});
  // 复盘已启用: 无推荐记录时显式说明 (T+3成熟), 不得吞错/未接通
  if(!(await page.locator('#reviewContent').textContent()).match(/暂无复盘报告|生成时间|复盘/))throw new Error('reviewContent 未显式反映复盘状态');
  // 两融已启用: 600519 真实融资余额行
  await page.waitForFunction(()=>document.getElementById('marginBody').textContent.includes('600519'),null,{timeout:30000});
  // 组合(交易)/盈亏日历 保持禁用: 必须显式"未接通"
  for(const id of ['portfolioBody','calGrid']) {
   if(!(await page.locator('#'+id).textContent()).includes('未接通'))throw new Error(id+'吞掉错误变空记录');
  }
  await page.locator('#tabUS').click();
  await page.evaluate(async()=>{await loadHaitaoDashboard();await loadDoublerDiscovery();await loadShortDiscovery();await loadUSMarketEvents();});
  for(const id of ['usHotCount','usDoublerCount','usShortCount','usMarketEventsCount']){
   if(!(await page.locator('#'+id).textContent()).includes('未接通'))throw new Error(id+'伪装为空数据');
  }
  await page.locator('#tabAshare').click();
  await page.evaluate(()=>refreshAll());
  await page.screenshot({path:path.join(__dirname,'runtime','modules-verified.png'),fullPage:false});
  // A malformed index payload must not prevent sectors/strategies rendering.
  await page.route('**/api/dashboard',async route=>{
   const r=await route.fetch();const d=await r.json();d.indices={bad:true};
   await route.fulfill({response:r,json:d});
  });
  await page.evaluate(()=>loadDashboard());
  if(!(await page.locator('#dataSourceLabel').textContent()).includes('格式错误'))throw new Error('错误数据未标明');
  if(/undefined|NaN|模拟/.test(await page.locator('#sectorBody').textContent()))throw new Error('单卡错误破坏其他模块');
  if(errors.length)throw new Error(errors.join(';'));
  console.log(JSON.stringify({result:'PASS',statusGroups:9,absentPanels,checks:['dashboard_contract','core_rendering','search_to_kline_chart','kline_period','refresh','blocked_states','US_states','single_card_failure_isolation'],pageErrors:errors}));
 } finally {await browser.close();}
})().catch(e=>{console.error(e.stack);process.exitCode=1;});
