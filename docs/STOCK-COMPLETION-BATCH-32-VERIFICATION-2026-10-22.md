# B32 固定原价版本逐证券对账：部分完成

**2026-10-23 04:44最新状态：专用PG/Redis、3001预览及8002原main已恢复；HTTP9/真实PG38/1070保护重验通过。** 下文02:07不可达为历史故障证据，不再是当前端口状态；恢复过程、失败和边界见文末附录。本人浏览器和完整期货链路/正式发布仍未验收，P4继续部分完成。

实施与实库验证：2026-10-22；续接复验与保存：2026-10-23（上海）。P1–P3已实现并完成本机验证；P4构建/保护验证已做，当前运行及本人浏览器验收阻塞，未完成发布。不宣称全系统、AI或全部历史数据可用。

## 四包及实际文件

1. 后端固定版本核对：修改`backend/app/api/stock_archive_catalog.py`、`backend/app/services/stock_raw_panel.py`；新增`backend/app/schemas/stock_archive_observations.py`、`backend/tests/test_stock_archive_observations.py`。
2. 股票独立入口与严格消费：修改`frontend/src/components/market/stock-archive-catalog.tsx`及其`.test.mjs`；新增`frontend/src/components/market/stock-archive-observation.tsx`及测试、`frontend/src/lib/stock-archive-observation-api.ts`及测试。
3. 真实验证与保护：新增`scripts/verify-stock-archive-observations-local.py`、`scripts/check-stock-b32-preservation.py`；原四文件字节副本/前后SHA记录在`logs/stock-b32-backups.json`和`logs/stock-b32-source-audit.json`。
4. 隔离构建、续接运行检查及本记录/子计划/master/冻结计划检查点。没有新依赖、DDL、迁移、数据回填或研究/模拟读路径切换。

## 数据流、金融口径与错误契约

目录每个版本的“核对本版本原价”只选择固定SHA，不自动查询。用户手填标准标识并提交后，GET `/api/stock/market/archive/versions/{version}/observation?instrument_id=000001.SZ`，股票身份/权限、原archive_ready及严格输入门禁先执行。仅1版本/1证券，原读取器3条SQL读取固定批次、来源快照、全批观察；先核对全部观察与已存来源逐行一致，再返回该证券观察或明确缺口分类。不向用户返回原始source_evidence。

未知SHA404不回退最新；非法输入422；缺来源/内容不一致409；未就绪或DB失败503；8秒超时504；每worker两请求及两实际CPU执行上限，满额立即429、不排队/自动重试。线程内闸门保持到实际CPU退出，HTTP取消不能提前释放CPU名额；Python线程不可强制中止，不把超时当计算已停止。没有恢复每日/累计业务次数限制。

原`selection`、`timestamp`、`decimal_text`、`build_raw_panel`及旧`archive_versions`五函数源码/AST核对未变。原未复权日线、CNY、元每股/手100股/成交额元、供应商除权昨收参考及方法版本保持。Decimal字符串和前端BigInt定点校验不截断0.01手；缺失不补0、不猜停牌，不混qfq。客户端校验版本/证券/日期/覆盖分区/价格区间，身份/token/市场/盘/版本/卸载切换拒绝旧结果。

真实只读样本为10月21日版本`26bfe7eed982d9dc3b6840e036c70df82c28e20d60e62ea848dacdb7e5cee63b`、000001.SZ，5548条影子观察与已存同源快照一致，记录247.57ms。此数不是独立供应商真实性认证，不证明PIT、交易日历连续、全历史/五年覆盖或回测/撮合资格。`point_in_time_verified`、`calendar_continuity_verified`、`eligible_for_backtest`均false。旧300股历史研究资格未提升。

## 实际命令和结果

均在仓库根目录，前端命令除外；Python简称为`backend/.venv/Scripts/python.exe -X utf8`。

- `python -m pytest backend/tests/test_stock_archive_observations.py backend/tests/test_stock_raw_panel.py backend/tests/test_stock_raw_panel_cli.py backend/tests/test_stock_archive_catalog.py -q`：10月22日复查后47通过，10月23日续接再跑47通过（1.65秒），`logs/stock-b32-reviewed-target.log`、`logs/stock-b32-resumed-target.log`。
- `python -m pytest backend/tests -q`：最终2829通过、3个既有真实付费测试skip、757warnings，133.27秒；`logs/stock-b32-backend-final.log`。这是10月22日结果，10月23日未重跑全后端。
- 前端目录：`$files=@(Get-ChildItem src -Recurse -Filter '*.test.mjs' | ForEach-Object FullName); node --experimental-strip-types --import ../logs/node-alias-register.mjs --test @files`：10月22日655通过；10月23日续接655通过、0失败/跳过，`logs/stock-b32-node-final.log`及`logs/stock-b32-resumed-node.log`。
- 前端`node node_modules/typescript/bin/tsc --noEmit`：退出0，包括续接的`logs/stock-b32-resumed-tsc.log`。
- 前端隔离`.next-stock-b32-reviewed`生产构建：退出0，`logs/stock-b32-build-reviewed.log`。使用既有preview开关、同源聊天与本地8002后端，不修改运行配置；Next临时tsconfig变化已恢复，SHA为`05636e5b2998cb7ee216821014240e016a2fb8f4ceca4ef9c9a80b8ed2b42250`。
- `python scripts/verify-stock-archive-observations-local.py`：10月22日真实隔离PG及原ASGI 38检查通过、4私有HTTP响应和1公表只读manifest；私有schema已清理，`logs/stock-b32-private-pg.log/json`。QA价量仅私有schema；夹具认证不等于本人JWT验证。
- 真实PG包括0.01手、三缺口分区、未选证券坏行导致409、原来源丢失409、未知版本404、权限/未就绪、两真实读被PG锁阻塞时第三429且解锁后两200。公表归档/EOD/研究/AI摘要前后相同。
- Node脚本将上述5份实响应交给最终`getStockArchiveObservation`离线fetch解析：5通过、网络0，`logs/stock-b32-real-client.log`；不是浏览器登录证明。
- `python scripts/check-stock-b32-preservation.py verify logs/futures-preservation-b32-start.json`：续接仍1070文件零增删改；四源当前SHA和四备份SHA分别与交付一致，`logs/stock-b32-resumed-state.json`。

## 真实失败及复查

缺路由/类型/线程卸载、缺客户端/入口/组件分别先红后绿，日志`stock-b32-backend-red.log`、`stock-b32-client-entry-red.log`、`stock-b32-ui-red.log`。组件测试初次使用未转义小数正则误匹配证券编码，改为字面小数且增加无表断言后通过，没有放宽金融校验。

独立只读复查发现HTTP 8秒超时会释放异步名额，但to_thread真实CPU仍运行。实际线程反例达到3并发，`stock-b32-cancel-cpu-red.log`；修复为线程内BoundedSemaphore(2)与finally释放。独立复验实际8秒超时两504、接下来两429、CPU最多2；线程退出再200，异常退出不泄漏。最终47目标测试包含真实线程取消场景。

第一次复查后全后端出现无关SQLite临时库CREATE INDEX的disk I/O setup错误，2828通过/3既有skip/1错误，`stock-b32-backend-reviewed-full.log`。C约70GB、F约367GB可用，根因未确定，未假称磁盘满。原`test_paper_custom_funds.py`整模块复验16通过，随后一次同全量命令2829通过；`stock-b32-paper-io-recheck.log`及最终日志。未修改期货代码、删除测试、增加skip或放宽断言。

## 运行、保护与当前阻塞

本包1070冻结保护通过，不代表所有期货功能已经生产验收。原B31受保护库存差异仅声明的stock_raw_panel和7新增，另3个B31原允许文件以B32开工字节副本单独核对，不虚构历史hash保护。五原函数未变的证据见source-audit。历史B29四共享展示兼容变更不因此否认，也未再次修改。

10月22日10:14自有预览重载前：paid AI/主播/研究/归档/因子挖掘活动0，1个AI任务paused/user；`logs/stock-b32-preflight.json`。此前核对自有8002父19136/监听33308，发起原main加载；3001旧26384加载B32产物后当时报告35664。后端长会话结果未取得，不将发起重启认定成功。

10月23日续接：旧exec会话已不可恢复；3001及8002 direct HTTP（trust_env=False）ConnectError，5433和6380 TCP两秒超时；`logs/stock-b32-resumed-state.json`。`python scripts/verify-ux-runtime.py`本次退出1，在8002响应JSON解析失败，`logs/stock-b32-runtime-resumed.log`；没有因此继续读取PG或写新的健康结论。诊断直连确认不可达，未打印网关响应正文或密钥。当前存在其他node/python/PG进程，但不能据进程存在推断目标端口正常，未强停/覆盖/再启动未知服务；3000 TCP可达未认证为本包预览。

当前浏览器控制工具不可用，未执行B32本人浏览器查询、截图或正式发布验收。先前B31截图不能当B32证据。PG38结果是10月22日已完成证据，当前PG不可达，不重复调用同脚本覆盖原通过记录或假称实库现仍健康。不能声称现在项目完整运行。

## 兼容、费用、回退、人工步骤

无数据库迁移/业务写、旧CLI仍兼容ValueError子类、旧manifest和目录接口保持。新增读取API不接交易账户，不开启AI任务。验证器直接源/模型调用均0；原后台新闻/语音等间接费用未量化。最后普通AI请求仍provider_rate_limited（429）、cost_state unknown，本包未再调用，不能保证对话恢复。

回退先比对source-audit当前SHA及后续执行者，再依据四开工副本生成精确反向patch，仅恢复声明股票hunk；保留原档案及独立文件，不整库恢复/删数据。核对无活动任务及自有PID后才可将自有前端切回保留的`.next-stock-b31-reviewed`和加载旧股票后端。当前服务不可达先诊断依赖/归属，不强停他人。没有完成正式回退/容量/安全或生产环境演练。

人工验收待服务恢复：本人登录3001 → A股 → 行情筛选 → 查看数据覆盖资格 → 查看原价归档版本目录 → 21日卡“核对本版本原价” → 输入000001.SZ → “核对固定版本原价”。必须看到固定SHA、21日真实价量/单位、全批5548同源一致与未认证限制；切版本/市场/账号不残留，关闭/只打开不查询。不得下单、恢复收费AI或上传凭据来替代验收。

## 下一四包（先补P4，不重复开发）

1. 核实并恢复既有独立预览依赖/端口/服务归属，保留暂停AI；执行原HTTP检查和本人只读核对，补P4证据。不修改共享配置绕过故障。
2. 授权数据缺口原因与补采预算：复用已有证据，精确区分目录/缺口/未知，未确认费用不批量重导；不追认PIT。
3. 股票只读Agent：版本化Schema/白名单/引用/幂等/预算/固定评测；供应商429未解除不无限探活，不开放未接通入口。
4. 可信历史/PIT/复权锚点/五年/基准和回放扩池；共享消息/音频及正式发布另兼容包，期货回归/回退和人工验收通过才声称完整。续做自动化保留。

## 04:44恢复附录：三个运维子包

### R1 依赖归属与恢复

确认旧PG数据目录`F:/qihuo-pg-data`的postmaster记录5433，但`pg_ctl -D F:/qihuo-pg-data status`返回no server running，目标端口不监听；系统另有默认PG/Redis进程，不碰默认5432/6379。没有删除pid、重建库、修改配置或强停任何进程。执行既有`python backend/scripts/local_service.py spawn logs/full-run-postgres.spawn.json`一次，8秒后pg_ctl确认专用PG PID30420运行。旧stderr包含another server might be running警告，未据此删除锁文件；后续实际连接/实库验证通过。

真实READ ONLY查询确认1个AI任务paused/user，主播任务0，股票研究仅failed9/completed17/cancelled7，没有pending/running；未输出用户信息。执行原`spawn logs/full-wsl-20261021.spawn.json`恢复原WSL keepalive，`python backend/scripts/local_service.py redis-check`退出0，专用6380监听38956。不改Redis运行配置，不切默认6379，不丢弃数据。

### R2 原main与隔离前端

复读原`backend/scripts/full_local_backend.py`，原启动门禁检查数据库schema及活动付费任务，不绕门禁。执行`python backend/scripts/local_service.py spawn logs/full-backend-20261021.spawn.json`，父20956/监听25444；stock research/EOD/archive worker true、GP false、release_verified false，原AI仍paused/user。

首次前端设置了错误环境变量NEXT_DIST_DIR，原配置实际读WENYUAN_BUILD_DIR，因此启动器退出、stderr显示缺少`.next`生产产物。没有伪称启动成功，也没重新构建或改配置。确认该次自有PID22924已退出，再更正进程环境WENYUAN_BUILD_DIR=`.next-stock-b32-reviewed`、WENYUAN_PREVIEW_BUILD=1、NEXT_PUBLIC_CHAT_SAME_ORIGIN=1、BACKEND_URL=`http://127.0.0.1:8002`，重用`spawn logs/ux-production-frontend.spawn.json`；当前3001监听16804。

首次与启动并行的HTTP验证退出1、前端各502，`logs/stock-b32-runtime-restored.log`，并非测试通过。更正后`python scripts/verify-ux-runtime.py`退出0，`logs/stock-b32-runtime-restored-final.log`。待旧验证会话结束，再执行一次定稿命令至`logs/stock-b32-runtime-restored-settled.log`：HTTP9全部通过（login/dashboard与4静态资源200、3未登录私有接口401），最终`ux-runtime-verification.json`与定稿一致。不是本人JWT或浏览器性能验证。

原main日志显示新闻采集和主力合约识别，同时有Sina bridge失败警告；8110未监听且本轮未另启动connector。因此不能保证期货行情/全部功能已经恢复，不以main健康或股票核对通过代替期货链路验收。既有后台新闻/行情请求副作用和费用未全局计量，不能声称整轮0上游调用；本轮没有直接Teajoin补采或付费模型调用。任务仍paused/user，原聊天429保持，未再探活供应商。

### R3 恢复后的金融/隔离复验与剩余门禁

恢复后执行`python scripts/verify-stock-archive-observations-local.py`，退出0、38检查/4原HTTP响应/1公表只读响应通过，私有schema清理成功；`logs/stock-b32-restored-private-pg.log`。新结果保存在原`stock-b32-private-pg.json`，实际样本仍10月21日相同固定SHA/5548条，201.17ms；没有抓22日/23日或造当日收盘，不追认全市场/历史真实性。本验证器0源/模型/public业务写，公表摘要在验证期间保持。是原ASGI+私有PG夹具权限验证，不是线上本人会话。

`python scripts/check-stock-b32-preservation.py verify logs/futures-preservation-b32-start.json`退出0，1070保护无增删改，`logs/stock-b32-restored-preservation.log`。恢复子包只使用原脚本/产物，不改生产源码、迁移、依赖或配置；未重跑已通过的全部单测/构建。无新的数据库迁移需回退；运维回退只能核对当前自有PID/无活动任务后停止本轮自有服务，不能删除数据或强停系统默认/他人服务。

下一轮先补本人B32只读浏览器验收和Sina8110归属/恢复及期货只读冒烟，再推进来源预算、受控只读Agent、可信历史/PIT与基准等原计划。浏览器工具当前不可用，未伪造截图/登录结果，P4仍不勾选；正式发布、回退/安全/容量及全部人工验收未完成，续做保留。
