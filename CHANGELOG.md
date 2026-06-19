# 拾米交易工作室 · 变更日志

> 建筑师每次审计后记录 | 格式: [日期] [角色] 变更摘要

## 2026-06-19

- [安全] tryLocalToken 漏洞彻底修复 (前端函数+4调用删除, 后端已返回401)
- [安全] 密码重置密保验证加固 (密保问题→验证码→重置)
- [前端] 登录弹窗关闭按钮+点击遮罩关闭
- [基础设施] WebSocket→SSE实时推送 (api/sse.py + 前端EventSource)
- [哨兵] doubler_daily_scan cron修复 (Hermes脚本副本过期同步)
- [哨兵] 微信推送限频修复 (18:00/18:05/18:10错峰)
- [数据] 用户数据库双库统一 (shimi.db + data/users.db ID同步)
- [交易] ericliu交易记录恢复 (5笔)
- [技能] 7个角色技能+1个自动学习引擎全部就绪 (79技能curator巡检)

## 2026-06-09

- [建筑师] pre-commit hook (冒烟测试自动拦截)
- [建筑师] CHANGELOG.md 创建
- [建筑师] 模型锁定 (deepseek-v4-flash)

## 2026-06-08

- [全角色] 变更审计协议生效 (6技能+灵魂.md)
- [HiTao] 美股投资建议 (us_advice) + 复盘系统
- [魔法师] predict_monthly_doublers 启动过滤修复
- [Magician] 独立 magician/ 目录 (消除 haitao/ 重复)
- [哨兵] API测试(19端点) + 安全扫描路径修复 + 存储监控
- [通讯员] 消息状态机+重试+每日摘要
- [拾米] Dashboard容错降级 + tushare Broken pipe重试
- [仓库] 清理(删8脚本+8JSON+空目录) + README重写
- [技能] self-improve自动学习引擎全角色集成
- [新模块] 预测模型研究 + 市场事件监控 + 观星台+先知规划

## 2026-06-07

- [建筑师] 基础设施 (logger/middleware/monitor/deps)
- [建筑师] 蓝图解耦 (backend.py 485→84行)
- [拾米] 条件预警系统 (services/alert.py)
- [魔法师] 启动前期过滤器 (_early_stage_score_batch)
- [HiTao] 美股盈亏追踪 (us_pnl) + 复盘 (us_review)
- [哨兵] 数据库备份脚本 + 告警检查cron
- [通讯员] 消息队列 + 告警投递

---

> 审计协议: 任何角色变更后必须呼叫建筑师全量审计。
> 审计清单: 导入完整性 / 跨市场耦合 / 端点可达 / 前后端匹配
