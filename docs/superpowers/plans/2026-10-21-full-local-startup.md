# 正常本机服务启动 Implementation Plan

> 使用writing-plans、executing-plans、TDD顺序三包；不提交、不建分支。用户已明确选择保持既有AI任务暂停并打开完整系统。

**Goal:** 将唯一既有休市自动暂停任务改为用户手动暂停，然后启动原main正常生命周期，不使用预览入口。

**Architecture:** 不修改既有期货、认证、网关、布局、模型或配置文件。新增本地操作工具只修改被明确授权且仍处paused/market_closed、身份/盘/updated_at相符的一条任务的自动恢复意图，状态仍paused；同事务写强制审计，禁止恢复/交易/资金变更。新增薄启动包装复用原main生命周期，只读核查无待自动恢复AI任务、复用凭据日志过滤，原研究/EOD/归档worker按已有配置启动。

**Tech Stack:** 现有asyncpg/SQLAlchemy/FastAPI/uvicorn、pytest/专用PG；无依赖/生产DDL。

## 1. 有授权的离线暂停操作
- [x] 真实私有PG12检查通过：CAS、重复幂等、他人/盘/时间/运行状态拒绝、审计插入失败整事务回滚；schema已清理，public任务摘要不变。
- [x] 新增backend/scripts/offline_user_pause.py，固定SQL仅改被授权任务pause_reason=user和更新时间/说明，不写status、资金、信号、持仓、模型或策略参数。实际成功一次、重复changed=false且同一审计ID，原任务其他字段聚合摘要一致。
- [x] 未先恢复再暂停，未伪造JWT或新增网络写接口；本地DB所有者执行用户明确授权的精确维护，状态/身份/盘/时间不符拒绝，8002在线拒绝离线操作。

## 2. 启动原生命周期
- [x] full_local_backend.py预检通过，原app.main:app/正常lifespan启动；仅本进程APP_ENV关闭SQL调试、凭据日志过滤，无预览请求白名单或worker替换。
- [x] 复用原3000/6380/5433/8110，8002隐藏启动；正常scheduler主锁、研究/EOD/归档worker运行，GP维持原关闭。新增只读/local-runtime/status说明实际状态与release_verified=false。
- [x] 原Teajoin盘后调度完成21日一次批次：5548/5558、缺10/目录未匹配20，partially_completed、attempts1。不是全市场/全部历史认证；既有快照保留，没有无限重试或盲重导。

## 3. 验证和交接
- [x] startup/原AI安全/新增单测67通过，真实私有PG12检查通过；真实前后端200、/preview/status404、实际worker运行。既有1014文件无改删，仅新增两个运行脚本；扩展1016保护通过。
- [x] CUA实际打开正常首页及标准登录页，未提交密码/读取浏览器存储/追加付费模型或交易；保留登录页，截图logs/full-startup-login-20261021.png。本人登录后业务验收本轮未执行。
- [x] 真实记录docs/PROJECT-FULL-RUN-VERIFICATION-2026-10-21.md。无生产DDL/依赖/期货生产代码变更，正常服务启动完成不等于系统整改/上线完成。停止本轮进程不能恢复收费任务，暂停意图保留。
