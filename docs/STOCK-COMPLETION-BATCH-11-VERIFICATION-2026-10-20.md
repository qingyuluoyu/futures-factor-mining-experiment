# B11 持久GP任务与有界逐代执行真实检查点

日期：2026-10-20，北京时间10:20；续做触发a-09-50。仓库：`F:/111/因子回测/futures-factor-mining-experiment-main`。

**状态：部分完成。** 本轮四包的新版本任务、迁移、可终止执行和受控API已实现，并通过下列本机隔离验证；不是整个产品完成，也不是生产GP已开放。真实`.env`未改，`GP_JOBS_ENABLED=false`；public没有应用008/009，没有新真实用户GP任务。可信期货输入入口、v2前端、操作系统故障/容量演练、发布与人工验收待做。旧不可靠/tasks和旧后台仍关闭，不得用恢复旧推进代替修复。

## 1. 四包实现和文件

### B11-1 任务契约、状态及009扩展

新增：
- `backend/app/models/gp_job.py`
- `backend/app/schemas/gp_jobs.py`
- `backend/app/services/factor_mining/jobs.py`
- `backend/migrations/20261020_009_gp_jobs.sql`
- `backend/scripts/migrate_gp_jobs.py`
- `backend/tests/test_gp_jobs.py`

新增独立metadata的任务/迁移版本表，不更改008的模型签名或旧任务表。009先check008；版本checksum、列/类型/空值/默认值、主键/唯一/CHECK、引用和索引校验；check-only无DDL，无应用启动自动建表。

从真实AuthContext继承本人、futures及JWT盘。新建只接收本人同盘既有冻结input_id、UUID幂等键、可选标题，拒绝客户端/模型指定owner、盘、bars或confirmed。相同键与相同请求离线仍可对账，冲突409；同输入只绑定一个任务；列表limit1—50、offset0—10000，不拉结果JSON。

状态pending/running/paused/completed/failed/cancelled/expired；暂停/取消保留正在计算的租约，停止确认前resume拒绝。job代数、固定输入checkpoint的revision与完成报告同事务提交；过期token、非running、冻结用户的结果拒绝。记录request_id、状态、步骤、尝试、服务器运行计时、起止时间、错误及结果hash。数据库约束代数不超过总数、总数1—200、累计时间0—1800秒，结果只在completed存在。

### B11-2 可杀子进程与租约

新增：
- `backend/app/services/gp_generation_process.py`
- `backend/app/services/factor_mining/process_worker.py`
- `backend/tests/test_gp_process.py`

兼容修改：`backend/app/services/stock_research_worker.py`，supervise_process仅新增可选报文字节预算和超时文案参数；股票默认调用行为保留，实际慢进程/取消/协议回归通过。

每次只认领一代。认领/心跳/结果CAS在短事务内，计算不占数据库事务；全局同时一个GP计算租约，最多8活动任务、同用户同盘2活动任务。单代300秒、任务累计1800秒，队列等待900秒；这些是资源预算，不是每日/月或累计使用次数限制。

父进程限制stdin13MiB、stdout约10MiB、写入等待10秒、定时心跳和停止回收；stdout仅协议，stderr不回传。子进程不接数据库/来源/模型凭据，不访问其客户端；仅固定数据及纯确定性内核。暂停/取消/超时会terminate/kill并wait；停止未证实保留槽，不假称停止。异常固定脱敏错误，无自动重试；CheckpointError保留确定性代码，损坏不可变输入进入failed，不伪装可恢复网络暂停。

child在读取stdin及导入重依赖**之前**由argv中的同一绝对deadline启动watchdog，stdin中的deadline必须一致，不因读取完成而续时。此前固定305秒启动等待可能晚于认领deadline，已以真实stdin保持打开的1秒反例修正。租约失联先暂停，不再自动继续；当前恢复逻辑按deadline+10秒watchdog余量保守释放并计满本代预算，仍需操作系统级挂死/父进程强杀/重启及峰值内存演练，不能只凭单元时钟推演承诺生产孤儿进程已认证。

冠军结果先做有限JSON、Schema及数量/字节校验。公式必须等于tokens的确定性表达式，评分和所有基础指标必须与检查点候选相等；重复/空冠军、篡改/遗漏指标拒绝。额外train/test/WF/regime诊断采用类型白名单，WF折数/样本外标记/稳定性一致，trials和固定配置一致；不把额外诊断当独立外部复核证据。报告始终research_only、source_certified=false，不认定可实盘。

### B11-3 独立v2 API及生命周期

新增：
- `backend/app/api/gp_jobs.py`
- `backend/app/services/factor_mining/startup.py`
- `backend/tests/test_gp_jobs_api.py`
- `backend/tests/test_gp_startup.py`

修改：`backend/app/main.py`、`backend/app/core/config.py`、`backend/.env.example`。

`/api/factor-mining/v2`提供availability、202新建、分页列表/详情、pause/cancel/resume及只读result。认证、期货市场与资源scope仍执行；匿名401、股票409、他人或他盘404、无可用worker新工作503，保留no-store/request_id/固定错误码。availability单独披露source_ingestion_available=false、legacy_available=false，不把内核可运行说成真实来源已接通。

flag默认false时不触碰GP数据库、不启动新worker。true时只读结构校验通过才启动，失败保持关闭；HTTP轮询仅检查版本，不反复锁表做全量反射；关闭应用会停止新worker。未提供公开原始bars上传、迁移008/009或模型自确认入口。本轮没有前端修改，原期货/history新建/恢复保护保留，A股/history仍走原股票报告。

### B11-4 真实验证、复查与记录

新增`scripts/test-stock-completion-batch-11-postgres.py`；`scripts/test-stock-completion-batch-02-postgres.py`只增加b11报告名允许项。真实私有schema应用/回滚迁移、真实JWT/HTTP、真实worker和计算子进程；不是用mock代替上述链路。私有fixture明确synthetic，不是市场数据认证。

## 2. 先红后绿与审查发现

- 新服务/进程/API缺模块的红记录分别为`logs/b11-jobs-red.log`、`logs/b11-process-red.log`、`logs/b11-api-red.log`；实现中7/8/11/13测试通过不是最终验收数。
- `logs/b11-job-safety-red.log`冻结用户及坏冠军反例失败，修复后13通过。
- 独立只读复查两项P2：冠军文本/基础指标可以与真实tokens/score不符；CheckpointError落入通用可恢复暂停。`logs/b11-review-red.log`4失败→`logs/b11-review-green.log`17通过；还补未知诊断、试验数、空结果及数据库代数/时间边界，`logs/b11-progress-red.log`4失败后修复。复查确认两项已修正，并实际检查正常含WF结果可保存。
- **真实PG暴露而SQLite未暴露的迁移问题：** `logs/b11-pg-first.log`及`logs/b11-pg-diagnostic.log`失败于PG为double比较插入的`::double precision`，既有CHECK签名解析不支持。009局部规范化该等价类型标注，未放宽阈值；新增正常/错误边界签名测试，真实事务DDL、重复和漂移验证随后通过。保留失败报告，不删失败证据。
- 自查stdin前绝对deadline反例`logs/b11-watchdog-red.log`1失败/3通过，修复后新增真实进程测试通过；独立复查再次确认读取stdin不会延后deadline。
- 未删除/跳过测试、未关闭类型检查、未放宽金融或权限断言；没有新依赖、提交或分支。

## 3. 实际命令和结果

仓库根目录实际执行：
```powershell
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_gp_jobs.py backend/tests/test_gp_startup.py backend/tests/test_gp_process.py backend/tests/test_gp_jobs_api.py backend/tests/test_gp_checkpoint.py backend/tests/test_gp_checkpoint_store.py backend/tests/test_gp_capability_guard.py backend/tests/test_stock_research_worker_process.py -q
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests -q
backend/.venv/Scripts/python.exe -X utf8 scripts/test-stock-completion-batch-11-postgres.py
backend/.venv/Scripts/python.exe -X utf8 scripts/audit-product-readiness-local.py
```

frontend目录实际执行：
```powershell
node --import ../logs/node-alias-register.mjs --test (Get-ChildItem -Recurse src -Filter *.test.mjs | ForEach-Object FullName)
node node_modules/typescript/bin/tsc --noEmit --incremental false
```

- 最终受影响 **69 passed，35 warnings，exit0**：`logs/b11-targeted-confirmation.log`。
- 最终后端 **2580 passed，3既有skipped，703 warnings，exit0**：`logs/b11-backend-confirmation.log`，119.29秒。早期2566/2579不是最终版本结果；既有utcnow/异步mock等告警没有假称消除。
- Node **449通过、0失败/skip，exit0**：`logs/b11-node.log`；TSC **exit0**：`logs/b11-tsc.log`。
- 最终真实PG **75检查通过，0失败，私有schema清理**：`logs/stock-completion-b11-pg-20261020-021537.json`、`logs/b11-pg-confirmation.log`。覆盖009事务回滚/check-only/重复/结构与checksum漂移、真实worker启动、同键并发HTTP、本人/盘/市场隔离、三代持久运行等于不停机内核、暂停时真实进程回收与晚到拒绝、离线对账、伪造指标不推进checkpoint、坏输入failed且释放槽。
- 公共业务写0，public旧GP任务/GP表计数不变；新合成数据仅在测试私有schema，账户余额和账本不受探针影响。不能把该测试说成生产金融行情已认证。

## 4. 服务、数据、部署阻断和回退

- **没有重启运行中的后端。** `logs/b11-before-reload.log`只读预检发现AI任务1 running（不是B10时的paused），safe_to_reload=false；核实托管backend PID6820/父444及8002监听后仍未停止进程、未修改任务状态。预检Python故意抛RuntimeError阻止重载；PowerShell后续只读进程查询成功不抹掉该失败。新v2路由因此尚未加载到当前常驻进程，不能声称用户当前网页已用上新执行器。
- `logs/b11-runtime-http.log`：原进程health、前端login/history/market均200，匿名supported/paper账户401；OpenAPI确认runtime_gp_v2_loaded=false。只是原服务/页面连通性，不是本人已登录交易E2E。
- 最新READ ONLY快照`logs/full-code-audit-snapshot-20261020-021212.json`：目录5558/110行业；19日有价5545/5558、缺13/未知19；旧研究300证券/585392行到16日，回放仍3证券，影子11093观察/5549证券/2日；五年2302800面板格仍over_limit，缺价8931格没有填0。没有抓取20日未收盘价格，没有混复权锚点或盲重导。
- **008/009只在私有PG验证，public GP表0。** 今后发布需独立受控顺序apply008→check008→apply009→check009，不改已发布版本；先可信数据适配和本人的受限灰度，再开flag。当前008/009脚本供受控迁移编排调用，本轮未执行公共迁移命令。
- 迁移为兼容expand；事务内DDL失败回滚已实测，应用回退保留新表和不可变输入/报告，不盲删已被任务引用的数据。正常回退先关闭新接单，停止worker并确认实际进程退出/核对租约与持久代数，再回退兼容代码。异常退出未确认的计算不得凭恢复旧worker继续；操作系统级孤儿进程、内存/容量及真实重启演练仍是开放前检查项。旧任务读写和后台保护须继续保留，回退不能恢复其已知错误。
- 本轮探针/测试新增来源0、付费业务模型0、公共订单/资金写0，不代表既有运行中AI或常驻行情服务期间没有其正常外呼/成本。没有绕过供应商TPM/RPM；不恢复日/月或累计1000次数门槛。
- 新生产构建/部署、Windows standalone既有EPERM处理、真实用户浏览器/人工交易验收均**未执行**。既有浏览器安全限制不绕过。S04/P01/R02、股票版本/PIT/五年/基准/DSL和真实期货研究仍有未完成部分，不能报告全功能完成。

## 5. 人工验收及下一轮四包

当前本人可验：登录后切期货/history仍看到执行器未开放，不能假新建；切A股/history为股票报告，原账户/数据不串盘。正在运行的AI任务由本人确认后通过正常页面暂停，才安排安全重载；不为显示新路由擅自中断。重载后flag仍false，availability应说明未启用、任务入口503；真正创建/暂停/恢复仅在以后可信输入和受限flag验收时做。公开环境不上传本轮synthetic fixture。

下一轮按依赖推进四包，不重复做已通过迁移或无预算下载：
1. **可信期货固定输入切片：** 核验现有Teajoin真实具体合约、交易日历、收盘时点、单位、使用授权及来源hash；有界预检→不可变输入→本人同盘任务引用。当前本地注册期货合格日线0，不能用错标股票/合成数据补。
2. **v2前端与执行发布门控：** 在新能力实际就绪后接参数/分页任务/暂停停止确认/恢复/研究报告，市场/用户/盘切换清理；补真实父进程强杀/重启、OS终止与峰值内存容量演练，再考虑灰度开flag。旧无盘任务保留隔离审计，不能猜盘迁移。
3. **S04/S05及R02/P01：** 先持久人工确认、写幂等与ExecutionContext；同步完成旧研究消费者迁移和完整版本迁移/构建回退，不宣称B11替代了真正期货因子研究。
4. **A股版本与研究纵切片：** 对账13缺/19未知、原价/复权/历史可得时间桥接；按依赖加载和五年资源验收，再推进基准/DSL/回放扩池/股票AI。没有许可证据或费用预算时保留有效旧数据、先做安全内部接通。

本轮不停止或删除续做自动化；尚未达到全系统/人工发布完成条件。
