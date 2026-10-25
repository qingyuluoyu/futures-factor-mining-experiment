# 第二批：数据查询与共享执行安全 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复已证实的无界股票查询、陈旧认证响应和聊天交易权限旁路；不将安全止血宣称完整股票Agent交付。

**Architecture:** 保留 FastAPI/SQLAlchemy/Zustand 和既有HTTP契约。四个独立工作包采用兼容参数、明确错误和服务端关闭未完成能力；不增加数据库表或执行公共库迁移。S04完整持久化确认和S05完整行情上下文仍需后续批次，不能用模型输出或内存状态代替。

**Tech Stack:** Python3.12、FastAPI、SQLAlchemy、pytest、TypeScript、Node test、现有隔离PostgreSQL验证脚本。

**执行状态（2026-10-19 15:50）:** 四个安全子切片已实现并完成本机目标/完整测试、隔离PG/HTTP验证；独立复查发现两项重要问题后已加反例修复，二次复核无新增Important/Critical。最终后端2333 passed/3既有skip，前端358 passed，TSC退出0，实际PG/HTTP42通过且私有schema清理。未发布、未浏览器人工验收，父包仍部分完成。真实交接见 `docs/STOCK-COMPLETION-BATCH-02-VERIFICATION-2026-10-19.md`。中途两个分工额度中断，主执行者已接管，不能把分工失败当交付。

## 全局约束和当前证据

- 2026-10-19 12:10读取第一批交付及主计划，其他审查者均已完成；没有活动文件写入冲突。
- 只模拟、不接真实资金。保留期货手工交易与原AI任务入口；聊天写工具尚无服务器确认凭据，禁止继续依据模型 `confirmed` 放行。
- 不读取或输出凭据；不调用付费模型/行情；不重导行情；午间不采集当日收盘。
- 不提交、不建分支、不重启包含遗留DDL的主后端。仅隔离数据库允许建测试表，结束清理。
- 范围与主计划映射：D05子切片、S06客户端子切片、S04/S05安全子切片。父包只有完整验收才勾选。

## 包B02-1：股票估值/财报查询边界（独立委派）

**Files:** `backend/app/api/stock.py`、新增 `backend/tests/test_stock_query_boundaries.py`；如调用方确需同步，仅修改 `frontend/src/lib/stock-api.ts`及对应测试。

**Interfaces:** 保留 `/valuation`、`/financials` 的数组响应；股票代码为六位ASCII，日期必须ISO日历日（兼容空字符串表示不设边界）；非法或倒序422。估值新增有界limit/offset，按日期稳定排序，默认覆盖当前已有调用方区间。财报保留默认40，上限明确。响应头公布分页和单位/市场/币种/时区/频率/来源证据边界，不把旧数据认证为可信来源。

- [x] 搜索全部调用方和现有schema；确定默认值不会把现有图表静默截成不完整系列。
- [x] 写ASGI测试：无效日期/倒序/Unicode代码/越界分页422，合法分页并集无重复；鉴权在入口生效。
- [x] 运行 `backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_stock_query_boundaries.py -q`，保存业务red。
- [x] 实施校验和有界查询，保持原字段及空值不变；执行green和股票API相关回归。
- [x] 复查返回数量、价格缺失、financial日期含义；交主执行者真实PG/HTTP验证。

## 包B02-2：HTTP刷新绑定当前会话（独立委派）

**Files:** `frontend/src/lib/api.ts`、`frontend/src/lib/api.test.mjs`及新增会话竞态测试；必要时独立小模块和测试。不得覆盖上一批绑定资料API或auth市场队列。

**Interfaces:** 保留 `request<T>` 和 `tryRefreshToken():Promise<boolean>`。捕获发起身份/访问和刷新凭据；响应只允许当前同一会话消费。旧401不得刷新新用户、重放写请求或删除新登录；同一会话并发401只做一次刷新，使用该轮返回token；迟到刷新不能先写localStorage再核对身份。

- [x] 读HTTP、auth store、WS刷新调用方、现有测试加载器；不读取存储中的真实凭据。
- [x] 测试可控Promise：A请求→登录B→A401；A刷新→B登录→旧成功/失败；并发401；重复登录同token代次变化；204和登录错误保持兼容。
- [x] Node目标测试先red再实施；类型检查和全部Node测试在整批汇总。
- [x] 身份变化返回明确错误，不清空当前用户；刷新失败仅对原会话执行过期处理。
- [x] 记录边界：本包不实现服务端refresh轮换/改密撤销，不可标S06完成。

## 包B02-3：聊天写权限止血与模拟账户读隔离（主执行者）

**Files:** 新增 `backend/app/services/ai/execution_context.py`、`tools/safety.py`；修改 `api/ai_chat.py`、`services/ai/gateway.py`、`tools/registry.py`、`tools/paper/dispatch.py`和`exec.py`的三个读取函数；新增 `backend/tests/test_ai_chat_execution_safety.py`。

**Interfaces:** 冻结类型化 `ExecutionContext(user_id, market_type, trading_mode, request_id)` 只由服务端AuthContext创建；通过流和工具链显式传递。纸面账户读取必须匹配context.user，传递JWT盘，缺失/股票scope拒绝。原聊天API返回股票未开放409；不得通过隐藏入口认为已经隔离。

**安全选择:** 当前聊天写工具自身commit、缺幂等账本，单纯加确认字段仍会漏权。此批临时关闭聊天paper写和代码沙箱执行（包括自由交易），返回 `server_confirmation_required` 和手工交易路径；不产生假“待确认成功”按钮。模型 `true`、字符串`false`、伪造action文本均不授权。原手工期货交易/任务创建不改。

- [x] 写测试覆盖五个paper写、沙箱、不同confirmed值均无业务调用；纸面读取live/virtual显式透传、缺context/跨用户/股票拒绝。
- [x] 用替身模型发出未下发的写工具，确认实际gateway仍拒绝且没有confirmation_request事件，不消耗真实额度。
- [x] 运行目标red；实施最小代码；真实ASGI和隔离PG账户分盘验证，无公共账户写入。
- [x] SSE返回明确未执行原因，并写脱敏request_id/tool/error日志；不改变确定性撮合公式。
- [x] S04仍未完成：下一包007版本迁移、确认记录/hash/到期/幂等状态及同事务账本、前端结构化确认，真PG并发验收后恢复聊天交易。

## 包B02-4：工具重试与共享数据库会话互斥（主执行者）

**Files:** `backend/app/services/ai/tools/loop.py`、新 `tools/safety.py`、新增 `backend/tests/test_ai_tool_execution_policy.py`。

**Interfaces:** 不修改工具结果消息协议。只有明确白名单的幂等只读工具允许超时重试一次，未知/写工具不重试；有同一AsyncSession的工具顺序执行，session=None的无状态读取可并发。类型非法参数给结构化 `invalid_tool_arguments`，不把数组/损坏JSON悄悄替换成合法空对象。

- [x] 写计数测试：只读超时调用两次、写/未知一次、权限/普通异常一次；共享session检测最大并发为1、无session只读并发保留；非法参数不进入executor。
- [x] 目标red→实现→green，检查gateway所有调用方同步传context；独立复查找出遗漏memory别名调用，改为无工具纯文本模式，不补默认live。
- [x] 运行整批后端及前端回归，失败记录具体证据，不降低既有关键断言。
- [x] 复查异常日志不含模型自由文本或秘密；写工具无幂等时保持关闭，不宣称S05完成；流式研究新增执行前工具开关复核。

## 整批验证、发布和回退

- [x] 新增专用 `scripts/test-stock-completion-batch-02-postgres.py`，沿用已有安全连接helper，创建随机私有schema并验证namespace，实际表查询+ASGI验证两个用户/双盘、数据分页，finally清理。只用确定性测试数据且明确不是行情认证。
- [x] 运行目标测试、完整pytest、全部Node、TSC。原服务只读HTTP健康可选，不把200当功能认证。浏览器/生产构建若未执行明确记录。
- [x] 独立审查实现和测试；修复重要问题后复跑受影响项。
- [x] 更新主计划子切片状态与真实交付记录：文件、实际命令/结果、迁移无变更、金融公式无变更、0上游调用、未发布风险和人工验收步骤。
- [ ] 发布须先解决P01启动DDL或采用已验收安全部署路径；本批不重启公共服务。代码回退可恢复旧API兼容，但不得把关闭聊天不安全写的保护回退到模型confirmed授权，应前向修复。

## 下一轮工作包（本批不冒充完成）

1. S04持久化动作确认与写幂等完整垂直切片。
2. S05所有行情/研究/会话/工具的market/mode执行上下文和预算。
3. R02旧同步研究入口归一、确定性队列测试竞态复现。
4. D01盘后缺口/失败恢复前台状态，与下一真实收盘检查分开。
