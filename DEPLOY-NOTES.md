# 拾米本地部署说明（DEPLOY-NOTES）

本文件记录本地隔离部署的数据模式、启动方法、出站白名单与禁用清单。
基线：仓库 `StealTyTowerLose2Star/shimi-trading-studio`（main）；2026-10-26 起本地改动经脱敏扫描后推送远端（`.local_token` 已停止追踪，历史遗留见回传文档）。

## 数据模式：仅 real（真实只读）

本人 2026-10-23 起要求产品只展示真实数据：`SHIMI_DATA_MODE` 仅允许 `real`（默认），
任何其他值（含 `mock`）在启动时直接拒绝（`real_data_only`），不回退模拟、不以合成数据补位；
历史 mock 函数仅作离线 fixture 保留在 `local_run.py` 中，不构成可启动模式。

| 项 | 说明 |
|:--|:--|
| 数据源 | Tushare Pro HTTP API（日线 EOD：盘中显示最近一个有数据交易日的收盘值） |
| 传输 | **HTTPS**（`https://api.tushare.pro`，2026-10-24 本机实测验收；明文 HTTP 在读令牌前被闸门拒绝） |
| 凭据 | 从本机用户环境变量 `TUSHARE_TOKEN` 继承；缺失时数据端点返回明确 502 错误（不 500、不伪造） |
| 用途闸门 | 需操作者环境变量 `SHIMI_MARKET_USE=personal_view`（个人非商业本地只读声明）；`commercial`/未声明一律拒绝 |
| 响应头 | `X-Shimi-Data-Mode: real-readonly`，`X-Shimi-Real-Data-Only: true`；含 `_mock` 标记的响应被统一拦截为 502 |
| 出站 | 白名单制：仅 loopback + `api.tushare.pro` + 事件免key源（`www.cninfo.com.cn` 巨潮公告、`np-anotice-stock.eastmoney.com` 东财公告、`www.cls.cn` 财联社电报、`np-weblist.eastmoney.com` 东财7×24快讯）（+ 东财 push2/push2his 备用节点保留，默认不走），其余一律拦截留痕 `[EGRESS-BLOCK]` |
| 美股 | `/api/us/quote?symbol=AAPL`（us_daily 日线收盘，**官方限 1 次/小时**，成功缓存 65min） |
| 策略扫描 | 趋势/混合/龙头已启用（Tushare 全市场快照+面板K线真实评分；按数据日 6h 重计算缓存，refresh 同日不重扫；2026-10-25 实测）；**候选池 2026-10-26 起剔除名称含 ST 证券**（北交所此前已排除）；**2026-10-27 起附回测视图** `GET /api/strategy/<trend\|hybrid\|dragon>/backtest?hold=5&top_n=5`：与线上扫描同一评分路径（realtime_scorer 纯数据函数按日切片），防未来函数（信号日 T 仅用 ≤T 数据，T+1 开盘价买入、T+1+H 开盘价卖出，停牌顺延/信号作废），等权定额+费用口径（买万2.5/卖万2.5+印花税万5），指标移植自 HKUDS/Vibe-Trading（MIT）`agent/backtest/metrics.py`（累计收益/年化/最大回撤/Sharpe/Calmar/Sortino/胜率/盈亏比/换手率，年化 252）；窗口受面板 150 日限制（暖机 60 日），结果按数据日 6h 缓存 |
| 新增模块 | 个股雷达（5年分位+fina_indicator 基本面+margin 融资余额+**估值六子项复核** financial_rigor，2026-10-26 移植自 ai-berkshire）、操作建议（三策略共识+K线形态共振）、翻倍评分/月扫/10k 方案（D9 龙虎榜+资金流真实接入；D7/D8 公告催化剂 2026-10-26 起走巨潮沪深北全量）、两融 `margin_detail`、复盘引擎（依赖建议推荐记录 T+3）、缓存摘要/运行监测（2026-10-25 实测） |
| 事件总览 | **2026-10-26 启用**：巨潮催化剂公告（沪深北全量 295 条实测）+东财公告+财联社电报+东财7×24快讯（全免 key 真实源，313 事件/288 标的实测）；finnhub/yfinance 无凭据源保持禁用并在 `sources_status` 显式标注；先知（合成训练数据）仍不启用 |
| 交易日志 | **2026-10-26 启用（仅手动记账，操作者授权）**：`/api/trades` 流水 CRUD（买/卖：代码、日期、价格、数量、费用可选）+ `/api/trades/portfolio` 持仓视图；移动加权平均成本（买入费用摊入成本、卖出费用抵减已实现）；现价为 tushare EOD 真实收盘（查不到显式 `quote_status=unavailable`，浮盈为 null 不用 0 冒充）；写路径仅本机+登录；**券商API/自动下单/买卖建议/webhook 永禁**。**2026-10-27 前端已切到后端**：流水表（买入/卖出）+持仓表（成本/现价/市值/浮动/已实现）+统计卡全部走后端返回值，录入/修改/删除走 CRUD，401 自动回登录提示；localStorage 仅余登录态，不再存交易数据；上游开仓语义按钮（实时价/盈亏日历/持仓分析/批量平仓/批量止损）已移除，CSV 导出改客户端生成 |
| 未开通 | 先知（训练数据为合成样本违反 real-only）、美股看板/扫描（yfinance/finnhub 永禁）、券商交易/推送（永禁） |

## 启动方法

```powershell
powershell -File restart-local.ps1        # 唯一入口（仅 real）
```

- `restart-local.ps1` 从注册表 User 作用域读取 `TUSHARE_TOKEN` / `SHIMI_MARKET_USE` 到进程环境（不回显、不落盘、不改系统配置），再经 `local_safe_start.py start` 白名单继承给服务进程
- 监听：仅 `http://127.0.0.1:17890`（不绑 0.0.0.0，无防火墙变更，窗口隐藏）
- 停止：先 `Get-NetTCPConnection -LocalPort 17890 -State Listen` 取 PID，`Get-CimInstance Win32_Process -Filter 'ProcessId=<PID>'` 校验命令行含 `local_run.py` 后 `Stop-Process -Id <PID>`
- 日志：`runtime/server-local.log`；数据库 `runtime/shimi.db` / `runtime/users.db`（本地，不加载仓库内已提交库）
- 预检（无凭据显式失败路径验收）：`venv/Scripts/python local_safe_start.py preflight`（临时端口 17891，17 项检查）

## 出站白名单清单（仅 real 模式放行）

| 主机 | 用途 | 代码依据 |
|:--|:--|:--|
| `api.tushare.pro` | 全部 A股/美股 日线数据（daily/daily_basic/index_daily/stock_basic/trade_cal/us_daily/us_basic/fina_indicator/top_list/moneyflow/margin_detail） | `local_tushare_data.py` `API_URL`（HTTPS） |
| `push2.eastmoney.com` 等编号节点（`N.push2(his)?.eastmoney.com`） | 东财盘中补充**备用**（默认不走） | `local_real_data.py`（保留未接线） |
| `www.cninfo.com.cn` | 巨潮公告检索（催化剂关键词 34 个，沪深北全量 `column=""`） | `data/cninfo_fetcher.py` |
| `np-anotice-stock.eastmoney.com` | 东财公告列表（事件总览 A股源） | `data/market_events.py` `fetch_eastmoney_news` |
| `www.cls.cn` | 财联社电报（v1 API，`sign=md5(sha1(排序query))` 纯本地零 key） | `data/news_fetcher.py`（移植自 a-stock-data §5.2，Apache-2.0） |
| `np-weblist.eastmoney.com` | 东财 7×24 全球快讯 | `data/news_fetcher.py`（移植自 a-stock-data §5.3，Apache-2.0） |

白名单 = 精确主机 + 东财备用正则 `^(\d+\.)?push2(his)?\.eastmoney\.com$`；白名单域名解析出的 IP 才允许 connect；直接 IP 字面量外连一律不放行。请求侧显式空代理（`ProxyHandler({})`）+ 全局 1s 节流（线程锁串行）+ 网络级重试 1 次（代理路径瞬断/慢读实测所需，us_daily 除外）+ 内存缓存（全市场快照/名录 6h、指数行情 15min、指数历史 1h、K线 5min（面板批量路径写入 30min；同代码更长序列/共享面板未过期时切片应答）、K线面板（≥30只且≤200日触发，深度规范化 150 日）10min、扫描/建议/翻倍等重计算按数据日 6h、美股 65min、空结果 10min）。

## 永久禁用清单（任何模式都不放开）

- 券商交易写库 / 券商 API：任何模式永禁（2026-10-26 起「手动记账」流水写库除外——本人授权的纯记录，非交易执行；相关路由见上表）
- 消息推送：`SHIMI_WEBHOOK_URL=""`（serverchan/微信 webhook 不触发，域名不放行）
- 付费数据源 / 模型 API：finnhub、yfinance 等不放行、不装密钥
- `.local_token` 自动登录后门：已移除（`shared/auth.py` 补丁）
- 商业用途数据分发：tushare 许可仅限个人非商业查看；不购买权限、不升级套餐

## 合规边界

- Tushare Pro 数据仅用于**本人本地非商业只读查看**（已读官方数据服务协议（二）5，2026-10-23；用途经 `SHIMI_MARKET_USE` 显式声明）；不缓存转售、不对外提供服务、不做收益承诺/荐股/自动交易
- 数据为日线 EOD：盘中页面显示最近收盘，属预期口径并在响应 `note`/`trade_date` 字段与首页横幅注明
- 东财备用后端（`local_real_data.py`）保留但未接线；其公共接口存在 WAF 频率封禁风险（2026-10-22 实测），默认不使用

## 测试基线（全部退出码 0）

- `local_test_runner.py`：test_smoke + test_local_accounts 17/17（出站封锁下）
- `test_tushare_data_quality.py` 19 项（tushare 后端合成响应契约：传输/用途闸门、数值/日期校验、缓存时点）
- `test_market_error_contract.py`（六端点 502 契约、dashboard 降级、health/lookup/K线失败语义）
- `test_real_only_policy.py` 8 项（非 real 模式拒绝启动、合成响应守卫）
- `test_local_failure_closed.py` / `test_local_numeric_contract.py`（东财备用后端失败闭合与数值契约）
- `test_local_data_quality.py` 9 项 / `test_local_modules.py` 4 项（real-only 无凭据显式失败契约；新启用端点无凭据 502、禁用面 403；事件总览 200 cache/scanning 契约且后台扫描打桩不触网）
- `local_safe_start.py preflight` 17 项（无凭据整机显式失败验收）
- `verify-real-mode.sh` 13 节（服务端实测：行情/策略/雷达/翻倍/两融/复盘/缓存/监测 + 404/403 安全面）
- `test_financial_rigor.py` 9 项（估值复核纯函数+模拟端到端+确定性，2026-10-26）
- `test_st_filter.py` 7 项（三策略+月扫候选池 ST 剔除、主扫描名称级过滤源码契约，2026-10-26）
- `test_news_fetcher.py` 7 项（财联社签名确定性、双源解析、失败返回空、快讯分类去噪，2026-10-26）
- `test_trade_journal.py` 11 项（流水 CRUD、移动加权成本含部分卖出/费用摊入/清仓重置、超卖与破坏一致性改删拒绝、无行情显式 null、文案红线，2026-10-26）
- `test_backtest.py` 15 项（回测指标手算核对、非正前价符号安全、T+1 成交价/费用口径、停牌顺延、ST/北证剔除、突变未来数据信号与净值前缀不变——防未来函数，2026-10-27）

## 运维提示（2026-10-25 实测）

- 冷启动：全局 1s 节流下构建 150 日全市场面板需数分钟（代理路径 5-15 分钟）；预热线程依次跑 trend/hybrid/dragon/advice，完成后各端点秒回。浏览器在冷启动期间打开页面会并发触发翻倍/建议重计算，进一步拉长车队，属预期。**回测端点同理**：冷缓存首次调用需等面板构建+逐日截面评分（数分钟），完成后按数据日 6h 缓存秒回；回测不新增出站域名（复用同一面板与快照缓存）。
- 历史日快照目前仅进程内存缓存（6h），重启重建；如需秒级重启，可将历史日快照持久化到 daily_store（历史日不可变，安全）。
