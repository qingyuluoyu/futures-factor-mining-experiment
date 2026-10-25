# 第五批：取消业务次数配额与因子双问题调查 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans task-by-task. Independent read-only investigations may run in parallel under dispatching-parallel-agents.

**Goal:** 取消因子实验室/历史回测的每日使用次数限制，同时用代码和真实只读状态解释超级因子不产出、期货无研究页的原因。

**Architecture:** 统一业务配额策略替换零散每日限制；保留旧字段和API签名用于兼容，不删除数据库列/计数、不批改用户。管理端不再提供已无效的日次数开关。并发、任务生命周期、数据质量、参数上限、模型调用预算/超时仍保留，不能把“次数不限”实现为无预算重试或无限计算。

**Tech Stack:** 现有FastAPI/SQLAlchemy/Redis、Next/Zod/Node/pytest；本地隔离PG/HTTP。沿用用户正式金融产品与权限、成本、迁移和回退约束；不提交/建分支、不读密钥、不调用付费模型或外部行情。

## B05-1 业务次数不限垂直切片

已读调用方：factor_lab search/llm/backtest/ab、backtest run/generate-kline均经quota_svc。max_tasks是同时活跃任务保护，不是累计使用次数；归档恢复2次等是受控上游预算，不在取消范围。

**Files:** quota_svc.py、system_settings_svc.py、admin.py配额响应、admin settings/user-quota-card、对应tests；必要兼容schema字段不删除。

- [x] 先红：旧全局3/2、用户覆盖1/1、Redis已超次数、不同角色都不再403；旧调用直接传limit=1也不恢复限额。任务并发仍生效。
- [x] 返回factor_daily/backtest_daily=None、兼容全局daily字段回显0；旧覆盖字段保持存量只作历史配置，不影响有效值。管理端取消日次数输入/提交，明确不限和保留资源保护。
- [x] 所有相关API调用方搜索确认；保持登录/权限、输入校验和错误返回。实际隔离PG/HTTP验证高使用计数不阻塞，匿名仍401，不用mock代替该链路的数据库和权限。
- [x] 不修改公共数据库存量限额，不删除Redis旧keys。当前正式行为固定不限；前向回退和旧版本恢复限制的影响明确记录。
- [x] 独立复查发现A股研究累计1000份限制，补充反例后移除；真实PG第1001份可建，下一活动任务仍因并发限制429。单次token/调用轮次/运行时间保护保留，不声称存在按金额的费用预算。

## B05-2 超级因子不产出根因

**Files to inspect:** factor_mining loader/lifecycle/loop、API/model、factor_lab search/scoring、super-factor页面/hook/API、现有测试。

- [x] 沿页面→创建→数据→worker→搜索→保存→展示逐段查，真实只读PG聚合/样本错误验证，不新建公共任务或拉数据。
- [x] 区分休市被暂停、数据不足/混市场、参数/质量门、异常吞掉、运行锁/停worker、空冠军与真正失败。不可降低金融质量门或造冠军冒充修复。
- [x] 安全独立修复：候选同时检查市场和期货产品注册域，先红后绿，错标签原数据保留。未修改GP、任务权限或质量门。
- [ ] 另包实现固定数据快照、版本化种群/RNG检查点与逐代原子推进；已复现每tick重复初始种群，并未修复该算法控制流。当前本地期货日线路径只有17008根错标股票、可用期货产品0，不追认其他来源也无数据。

## B05-3 期货因子研究入口调查

已核实factor-research/page.tsx恒用StockResearchEntry，后者明确拒绝futures；不是期货数据没导入就能解决。

- [x] 清点旧期货实验室/超级挖掘/回测组件、菜单和确定性IC/分组/组合API，确认没有可直接恢复的完整期货研究页。
- [x] 调查报告说明原有真实能力入口及原因：研究路由写死股票；单品种时间序列IC不能冒充跨期货截面研究。未添加占位页或伪接股票API。
- [ ] 真正期货研究分市场实现、入口测试、会话切换与错误展示仍待新切片。本轮只完成原因与后续API/数据/方法边界调查，没有宣称此项功能修复。

## 验证、发布和回顾

- [x] 受影响红绿测试、后端2464通过/3既有skip、Node408通过、TSC退出0，隔离PG/Redis/HTTP64检查通过并清理。独立复核后补累计上限反例。启动安全门保持只读，安全重载本地后端并核对新OpenAPI；未执行全站浏览器验收与新发布构建。
- [x] 记录真实文件/命令/通过失败未执行/金融口径/兼容与回退/成本/风险/人工路径/下一轮五包，详见 `docs/USAGE-POLICY-AND-FACTOR-INVESTIGATION-2026-10-19.md`。系统整体部分完成；后两项根因调查不等于功能已修好。
