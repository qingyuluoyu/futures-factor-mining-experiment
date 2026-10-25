# 股票影子归档后台链路 Implementation Plan

> 使用executing-plans / test-driven-development / dispatching-parallel-agents / verification-before-completion；无Git，不建分支/提交；只隔离文件所有权，不覆盖已有修改。

**Goal:** 将已存EOD→Decimal影子归档接入可追踪后台任务与两页共享状态，不依赖一次人工CLI；不切换用户K线/研究读路径，不抓05:15未收盘daily。

**Architecture:** 保留原EOD worker。独立开关默认false，启动仅check既有003/004与新005，不自动迁移；独立worker每300秒最多一批，仅本地DB已成功EOD。任务唯一来源+方法，领取running原子提交；影子写入和任务completed同事务。失败/取消/过期保存终态且不自动重试，原采集成功不回滚。只读状态加兼容archive字段；不开放交易/付费模型/手动重试HTTP。

**Tech Stack:** 原FastAPI/SQLAlchemy/PG/asyncio/Next/Zod/pytest/Node，无依赖和外部调用。

## 包1：持久任务模型与005扩展迁移（独立agent所有权）

新增`backend/app/models/stock_archive_job.py`、`backend/migrations/20261019_005_stock_archive_jobs.sql`、`backend/scripts/migrate_stock_archive_jobs.py`、`backend/tests/test_stock_archive_jobs_migration.py`。

契约：ArchiveJobBase；StockArchiveJobVersion表stock_archive_job_schema_versions(version/checksum)；StockArchiveJob表stock_archive_jobs。字段：id UUID PK，source_batch_id UUID，source_data_version CHAR64，method_version VARCHAR64，trade_date DATE，request_id VARCHAR32，status VARCHAR16 running/completed/failed/cancelled，step VARCHAR32 archiving/completed/failed/cancelled，attempts INTEGER=1，started_at/updated_at/deadline_at TIMESTAMPTZ，finished_at nullable TIMESTAMPTZ，archive_batch_id nullable UUID，error_code nullable VARCHAR64，error_message nullable VARCHAR300。其他字段nonnull。唯一source_batch_id+method_version；索引status+deadline_at、started_at；CHECK status枚举、step=status对应(running→archiving)、attempts1、deadline>started、updated>=started、running时finished null且archive null/error null、completed时finished非空/archive非空/error null、failed/cancelled时finished非空/archive null/error_code非空，finished>=started。与004隔离metadata、无跨外表FK，不能改已应用004/共享Base。

- [x] 模型/迁移契约先红后绿；严格check结构/SHA/索引/约束/默认值，事务锁/超时；check缺表不建，不改旧版本。003/004结构校验不受新增模型影响。

## 包2：受控后台任务/生命周期/只读API（root）

新增`backend/app/services/stock_archive_worker.py`、`backend/tests/test_stock_archive_worker.py`；修改config/main、stock_market API；新增`backend/app/services/stock_archive_status.py`及测试。

stock_daily_archive_enabled默认false。`_start_stock_archive`只公开enabled/ready/error_code/worker，不保存完整settings；就绪检查003/004/005在10秒内。失败不阻止旧EOD启动；shutdown独立stop。

`process_archive_once(factory, *, now: datetime | None=None) -> dict`：在短事务将最多10条过期running改failed/archive_expired，不重复归档；按最早trade_date/id选最多1个已完成/部分完成、非未来/未收盘同日且无当前method任务的EOD，唯一插入running后commit。多worker靠DB唯一约束只有1执行。90秒deadline，60秒执行总界、SQL5s/lock2s；锁任务并核实状态/deadline/源版本，调用archive_eod；completed/返回archive id和观察写同事务；失败脱敏并写failed，取消写cancelled后继续抛CancelledError。不吞错误为idle，不改源EOD、目录或账户。不自动重试终态（显式安全重试入口留后续）。worker启动即检查旧批次，随后sleep300；0来源/模型。

兼容GET `/api/stock/market/sync-status`新增`archive`字段，旧字段不变：
```
archive: {status_version:"stock-archive-status-v1", enabled:boolean, ready:boolean,
 worker_running:boolean, state:"disabled"|"unavailable"|"stopped"|"idle"|"running"|"completed"|"failed"|"cancelled"|"expired",
 message:string, latest_job:null|{id,request_id,source_batch_id,source_data_version,trade_date,status,step,attempts,
 started_at,updated_at,deadline_at,finished_at,archive_batch_id,error_code},
 scope:"shadow_only_user_read_path_unchanged", source_calls:0, model_calls:0}
```
只读新增1个LIMIT1任务字段投影，不读quotes/error_message；disabled/notready不读新表。异常仍503/no-store/requestID，不返回假idle。

- [x] 状态机/开关/启动缺迁移/超时/取消/失败/幂等/追踪先红后绿，补领取提交窗口及持久化失败取消语义。
- [x] 本地应用005前先真实私有schema并发/事务/过期/失败验证；按PID核实后受控重启自有后端，本地仅启新开关，其他服务和原用户会话保留。

## 包3：共享状态前端（独立agent）

只修改`frontend/src/lib/stock-eod-status-api.ts`及测试、`frontend/src/components/market/stock-eod-status.tsx`及测试。Zod的archive字段optional兼容旧服务器；有字段按上面严格Schema，不是optional容忍坏对象。组件显示归档开关/worker/状态/原交易日/错误码/尝试/任务ID，可details收起。明确只影子归档、不代表历史迁移或认证、不改变用户行情读路径；无字段提示旧服务未提供状态。沿用刷新和取消、不新增轮询/写请求，不能改变原EOD状态文案或两页布局其他部分。

- [x] 正常/缺字段/错误/过期/失败/无任务/Schema腐坏与共享刷新先红后绿；正确原loader全Node304及tsc通过，状态/结果跨字段refine。

## 包4：集成/交付（root）

- [x] 新真实PG脚本私有schema执行005/004/003和多worker一次归档、成功事务/失效源/过期/取消、原数据聚合不变及清理；在本地005扩展应用、后台对Oct16只做已有归档核对并记录completed，不能重复下载。
- [x] 专项103/完整后端2138、Node304/tsc、真实PG21/HTTP13/独立QA浏览器、独立审查通过；保存实际成功/失败/未执行与成本、回退、下一包。回退关闭新flag/停worker、保留005和任务/004影子数据；不删表/清库/改旧字段。
- [x] 已通过工具删除已履行的单次a-05-15，02:05/07:10实际仍ACTIVE。只有整个系统生产验收完成才停止其他续做。本轮仍部分完成，未做factor/锚点迁移，不声称全市场历史真实无误。

## 05:38实际检查点

交付`docs/STOCK-ARCHIVE-WORKER-VERIFICATION-2026-10-19.md`。005已本地应用，SHA a3f59406504f788a67bfed1e5109b26a1305092e64c0974bf50898878f84cdb0；新flag本地启用、默认配置仍false，后台已将前轮Oct16归档核实为一条completed任务30a7637b-78e7-4df4-ad8a-7da6764adc34，原影子5548行不重复。用户读路径/原EOD/旧股期货历史不改，0上游0模型。后端2138通过（原3skip/502warnings）、103专项-W error、Node304/tsc、私有PG21/真实HTTP13及两页QA浏览器通过。两个取消窗口Important和前端Schema Minor均修复。backend新listener10784，其他服务保留，PID下轮重核实。单次05:15已删除，原02/07仍ACTIVE。后续显式受控重试、因子/锚点、新收盘/缺口、AI/发布四包仍未完成；07:10不抓未收盘daily。
