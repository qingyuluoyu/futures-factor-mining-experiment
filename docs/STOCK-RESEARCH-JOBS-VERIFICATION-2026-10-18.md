# 07:10续做交付记录：持久研究任务与历史报告

记录时间：2026-10-18 07:43（Asia/Shanghai）。状态：**本轮垂直切片已实现并通过所列本机验证；整个系统仍为部分完成，未通过生产发布验收。** 继续以本记录和任务计划为检查点，不覆盖前轮修改，不停用两个“A股系统续做”自动化。

## 四个工作包与真实数据流

1. 新增独立研究任务表/版本表和版本化迁移；共享旧请求Schema但保留旧API导入/字段兼容。PG事务锁串行控制创建和队列，幂等键按用户与JWT盘隔离；同键同参数复用，同键不同参数409，每用户每盘1个active、全局8个active，最多1000个保存任务。
2. FastAPI现有进程只负责持久队列/状态，不在HTTP内执行新研究；全库计算槽最多1个，启动可终止Python子进程。读取本地真实库只读REPEATABLE READ事务，验证实际事务设置，指纹覆盖实际输入。结果最多8MiB、来源元数据512KiB、执行120秒、排队15分钟、attempts最多1，无自动重算、无上游或模型调用。
3. 研究页改走异步创建→轮询→取消/恢复→读取保存结果，股票/history接通分页列表及共享报告组件，保留失败/取消原因；期货/history仍为原超级因子页面。新客户端严格Schema、捕获会话token、20秒请求上限、最多18分钟/540次自动GET；网络/429/503未知提交保留原键，只有明确创建422才允许修正新建。
4. 完成真实PG迁移、并发、HTTP、原回放/板块回归、浏览器、全量测试、typecheck、构建命令和旗标回退演练。没有新依赖，没有真实资金账户，没有提交或创建分支。

创建链路：前端输入→JWT身份与盘→严格Schema→数据库幂等/配额→pending→行锁领取running→独立只读快照→确定性原金融计算→校验结果→持久completed→有权限GET结果。异常保存failed/expired，迟到结果不得覆盖取消或终态。列表和状态轮询不读取result_json，避免每页50×8MiB读放大。

取消/丢租约时，逻辑终态**不立即释放物理计算槽**；token保留至实际杀停子进程后ACK。孤儿子进程按绝对deadline+5秒硬退出，槽仅在deadline+10秒后兜底回收。DB等待被工作进程5秒、PG锁3秒/语句10秒及总执行deadline约束；HTTP业务处理15秒、就绪检查5秒。worker关闭时即使保存状态的DB故障也保留CancelledError，不吞取消继续循环。

## 金融与数据口径

金融公式仍为`stock-research-2026-10-17.v2`，任务流程`stock-research-job-v1`，数据快照`stock-local-snapshot-v1`。新快照增加独立财报输入选择版本`latest-disclosed-report-frontier-v1`：同证券同公告日取最新报告期，后来披露旧报告期不能倒退已可获得报告期，公告当日仍不可用；精确重复、非有限值和冲突仍拒绝。

调查真实库发现902组同证券同公告日的多报告期，这不是应直接判坏的重复。首次严格快照因此误拒正常研究，随后以报告期前沿选择修复，并验证正常真实子进程计算。指纹包含所有源财务行和选择版本，未选旧期源值变化也改变指纹；最新期NULL不回退旧期值。

快照按limit+1明确拒绝超限：价格/估值各100万行、财务10万行、证券1万、所有计算面板合计200万格，不截断成成功。2026-07-13→09-11的300样本/45观测日可检验和回测；真实浏览器因子检验显示IC均值0.1187、4个IC观测，20股月调仓回测总收益-5.10%。这些短窗值只证明链路，不能证明策略有效。

宽区间仍可能因真实持仓缺价409或面板超限422而失败，保存真实错误，不补前价/删坏成员/编造行情。前复权研究不等于未复权日线撮合，更不等于可实盘收益。来源manifest明确本地逐行供应商/授权/采集时间未留存、`collected_at=null`、`immutable_archive=false`、`point_in_time_certified=false`；指纹不能证明历史时点或使用授权已经认证。

## 实际修改文件

以下为仓库相对路径（根目录`F:/111/因子回测/futures-factor-mining-experiment-main`）；通过前轮源码备份逐文件哈希核对，不依据Git猜测，当前目录无Git元数据。

- 后端新增：`backend/app/models/stock_research_job.py`；`backend/migrations/20261018_002_stock_research_jobs.sql`；`backend/scripts/migrate_stock_research_jobs.py`。
- 后端新增：`backend/app/schemas/stock_research.py`、`stock_research_jobs.py`；`backend/app/api/stock_research_jobs.py`；`backend/app/services/stock_research_jobs.py`、`stock_research_worker.py`、`stock_research_process.py`；`backend/app/services/stock_factors/snapshots.py`。
- 后端兼容接入：`backend/app/api/stock_factors.py`、`backend/app/core/config.py`、`backend/app/main.py`；配置示例`.env.example`。本机忽略文件`.env.runtime`仅增加功能旗标，密钥未放入源码。
- 后端新增测试：`backend/tests/test_stock_research_job_contracts.py`、`test_stock_research_job_model.py`、`test_stock_research_job_service.py`、`test_stock_research_jobs_api.py`、`test_stock_research_jobs_migration.py`、`test_stock_research_snapshots.py`、`test_stock_research_worker_process.py`；未覆盖原市场/API六项测试。
- 前端新增：`frontend/src/lib/stock-research-api.ts`及`.test.mjs`；`frontend/src/components/factor-research/factor-research-workspace.tsx`、`use-research-task.ts`及测试、`research-job-panel.tsx`及测试、`research-report.tsx`及测试、`stock-research-history.tsx`及测试。
- 前端接入：`frontend/src/app/(main)/factor-research/page.tsx`、`frontend/src/app/(main)/history/page.tsx`、`frontend/src/components/factor-research/factor-research-page.test.mjs`、`frontend/src/components/layout/sidebar.tsx`、`frontend/src/lib/market-routes.ts`及测试。原研究结果展示迁至共享组件，期货页面内容未删。
- 报告可视化：`frontend/src/components/factor-research/mini-line-chart.tsx`及新增`mini-line-chart.test.mjs`。局部清除研究图表从K线继承的40根/200px右留白，主题/resize后fitContent，日线显式UTC解析；不改全局K线配置或期货。4项生命周期/日期回归先红后绿，真实浏览器重新加载后9个净值点全部可见，不再仅剩末两点。
- 验证工具：`scripts/test-stock-research-jobs-local.py`、`scripts/test-stock-research-jobs-postgres.py`；计划`docs/superpowers/plans/2026-10-18-stock-research-jobs.md`和本记录。忽略的logs保存备份、QA结果和构建副本，不包含在交付源码。

## 实际命令与结果

| 实际执行 | 结果与证据 |
|---|---|
| `backend/.venv/Scripts/python.exe -m pytest backend/tests -q`（修改前） | 1533 passed、3 skipped、152 warnings；`logs/stock-research-jobs-baseline-20261018.log`。 |
| `backend/.venv/Scripts/python.exe -m pytest backend/tests -q --junitxml=logs/stock-research-jobs-full-suite-20261018.xml`（最终） | **1645 passed、3 skipped、226 warnings，50.60秒**；`logs/stock-research-jobs-full-suite-20261018.log`。3个跳过为既有项，新增项未跳过。新增告警来自真实User夹具既有utcnow默认值；其余既有AsyncMock未await等未扩大修改。 |
| 用ElementTree/Counter比较旧`logs/stock-replay-full-suite.xml`和最终XML的(classname,name)多重集 | 旧1440项、新1648项，旧用例缺失0；没有通过覆盖/删测试制造通过。 |
| 新契约/服务/进程/API专项，及模型/迁移/快照专项 | 曾跑26项与88项通过；后增加慢快照、绝对截止、关停DB故障测试，最终真实子进程专项8通过。所有已包含最终全量。 |
| `backend/.venv/Scripts/python.exe backend/scripts/migrate_stock_research_jobs.py`（首次/重复）及`--check` | 首次applied=true；重复/check为applied=false、checked=true。版本`20261018_002_stock_research_jobs`，checksum `893d47e49179300cc2749c38db297ef4ac9db99d9f574537304c7d32492efb02`。 |
| `backend/.venv/Scripts/python.exe scripts/test-stock-research-jobs-postgres.py` | **8项真实PG检查通过**：独立临时schema，4个并发worker只有1个领取；取消保留槽、错误token不能ACK、正确ACK后下个可领、丢租约过期不重试、过硬截止宽限才释放；finally清理该随机临时schema，不修改真实用户任务。 |
| `backend/.venv/Scripts/python.exe scripts/test-stock-research-jobs-local.py`（受控重启后最终） | **23项真实HTTP/PG/子进程检查通过**；正常检验和回测、同时同键只建1个、409/429、跨用户和盘404、来源/结果持久、缺价失败不伪造/不重算、真实running取消/槽ACK、分页、期货资金不变。`logs/stock-research-jobs-http-20261018.log`及results JSON。 |
| `backend/.venv/Scripts/python.exe scripts/test-stock-expansion-local.py` | **19/19通过**；直接注册、并发唯一/bcrypt、5558目录/110板块/299最新报价、原研究/缺价、期货资金不变。没有本轮上游导入；目录upsert测试事务回滚。 |
| `backend/.venv/Scripts/python.exe scripts/test-stock-replay-local.py` | **18/18通过**；自填资金、并发幂等、T+1/整手/原始开盘/费用/权益、权限和两盘隔离、原期货资金不变。仅新QA回放账户。 |
| `backend/.venv/Scripts/python.exe backend/scripts/migrate_stock_replay.py --check` | 旧版本`20261017_001_stock_replay`、原checksum不变，checked=true、applied=false。 |
| frontend：收集全部`*.test.mjs`，`node --import ../logs/node-alias-register.mjs --test $tests` | **237通过、0失败/跳过**；`logs/stock-research-jobs-node-20261018.log`。loader仅解析原@路径，不替换金融逻辑。 |
| frontend：`node node_modules/typescript/bin/tsc --noEmit --incremental false` | 最终退出0。 |
| frontend源码复制到`logs/stock-research-jobs-build-20261018-0710`，排除.env*/node_modules/.next并复用依赖junction；`node node_modules/next/dist/bin/next build` | **命令退出0、编译/typecheck/40页生成通过**，但Windows standalone追踪复制出现symlink EPERM警告；不能据此称可部署standalone产物已验证。最终日志`logs/stock-research-jobs-build-20261018.log`。运行dev目录未覆盖。 |
| 旗标false→受控重启→HTTP检查→true→受控重启 | 关闭时availability关闭、新列表503、原期货账户200；恢复后worker接收、既有QA报告ID仍全部可读、期货200；`logs/stock-research-jobs-rollout-20261018.jsonl`。其中关闭记录的reports_preserved=false表示未读取报告，**不是删除数据**；重新开启已验证保存。 |
| dotenv动态读取密钥，仅扫描源码内容是否匹配，不输出值 | 最终803个源/测试文件，实际runtime敏感值匹配0。无模型/上游请求。 |

前期失败及修复不隐藏：Schema/服务/进程专项先6失败，涉及标题空白、嵌套未知字段、取消释放计算槽、列表结果读放大、跨午夜测试/幂等和DB等待越界；全部通过强回归闭环。最后就绪/worker不可用503缺追踪头的2项新增断言先红后绿。浏览器发现StrictMode旧abort污染新effect、取消遇已结束409保留旧running等，已修复并增加hook回归，不以屏蔽错误字符串代替修复。

## 浏览器与人工路径

仅使用`localhost:3000`、原已有隔离`qa_browser_38d12fe4`，未操作用户原`127.0.0.1:3000/market`标签或原资金。已实际提交正常检验/回测、刷新恢复同一个任务和结果、进入历史查看成功与失败、即时取消实际attempts=1任务显示cancelled、切期货history看到原超级因子、再切股票返回行情。首次日期fill未提交React变化造成两次默认2018超面板失败，随后用原生日期键提交并确认真实参数，不删除这些QA失败记录。

- 成功检验任务`69c3fc98-a7fc-4f22-b633-e39152f3d904`，成功回测`fd461a69-e959-48f7-a80f-446d2af0f97f`；数据版本均`a07c62ab2f41819f8e5538a7d918fd66f97481267fef38056ad376139b05ee82`。
- 取消任务`2f8169b4-c5ef-4f88-90b5-0df090c10a7f`已显示cancelled，未保存结果；不是只中止前端future。
- 证据：`logs/stock-research-history-20261018.png`、修复后`logs/stock-research-backtest-readable-20261018.png`。原留白截图`logs/stock-research-backtest-20261018.png`保留作对照；没有把错误截图当最终验收。数值/真实参数/财报raw10322→selected9227的PG核验在`logs/stock-research-jobs-browser-evidence-20261018.json`。

人工复核：A股→因子研究→20日收益率→开始2026-07-13、结束2026-09-11→开始检验→刷新→历史研究报告查看同ID/参数/来源/风险；再组合回测20股月调仓；新建后立刻取消；广区间检查明确缺价/超限而非旧曲线。股票/期货切换history不混页；交易继续仅独立模拟和日线回放、自填资金。

## 迁移、兼容与回退

备份：`logs/stock-research-jobs-backup-20261018-0710/backend-app`及`frontend-src`；迁移前PG备份`logs/before-stock-research-jobs-20261018-0710.dump`，pg_dump退出0、17602452字节。仅PG5433专用本机库操作，系统5432未动。

迁移顺序：备份→独立SQL迁移/检查→兼容代码→STOCK_RESEARCH_JOBS_ENABLED=true→重启→健康与真实链路。新表未加入旧Base.create_all，不要求启动同步建大表；本次仅新空表，无旧表锁定或历史重导。默认示例旗标false，生产未开启也不读不存在任务表。

回退先关闭旗标并受控重启，等待/终止现有研究子进程，保留新增表/报告；必要时逐文件对照备份恢复**本轮**接入，不能覆盖后续修改。旧期货/回放表及账户兼容；旧同步研究API保留。不要整库恢复覆盖新注册或后来交易，也不要为了回滚删研究报告。无自动重试付费副作用、无真实交易补偿。迁移回退采用保留增量表的兼容退回，不宣称破坏性drop可逆。

本机最终：3000 dev、8002 backend、5433 PG保留运行；旗标true；没有残留研究计算子进程，3000/login、8002/health、8110/health均200。QA临时浏览器标签已关闭，原用户127.0.0.1行情页仍保留。未安装依赖、未调用付费模型或供应商，本轮外部调用数0；本地计算、17.6MB数据库备份、QA记录和构建副本有磁盘/CPU成本，不能称总运行成本为0。

## 剩余风险与下一轮四包

1. **旧同步研究财报口径**：旧`load_market_data/_build_market_data`仍绕过新报告期前沿，同日多期/晚发旧期可能择期不确定。新jobs已修，但旧API不能因此宣称全部财务研究正确。下一轮提取共享选择器、明确方法/输入版本兼容并做旧/新真实财务因子回归，防止双轨不同口径。
2. **数据覆盖与默认研究区间**：5558目录不等于5558完整历史；最新有价仍299，三证券28交易日原始回放不变。默认2018长窗可能超200万面板上限，应补有界覆盖可视选择/参数恢复、增量导入审计/采集和授权凭据、停牌/公司行动辨别；不盲目全市场重导、不填缺价。财报历史修订/行业成分、PIT仍未认证。
3. **股票AI只读研究**：预算/最大步骤、工具白名单、结构化输出、来源与报告资源权限、取消/幂等付费调用尚未全接通；原模型429 TPM未重新请求，不能称AI全部可用。先只读、再接独立日线模拟工具，绝不照搬期货保证金或真实交易工具。
4. **发布与容量**：Windows构建退出0但standalone symlink EPERM未解决；Linux/Docker实际部署产物、性能/背压/监控/备份恢复、移动端等未验收。既有html主题dark/light hydration警告仍存在，未改主题架构。当前单槽无公平调度、每盘1000报告硬上限无用户归档入口，后续按真实容量设计；无生产部署/线上SLO承诺。

以上风险为明确未完成，不以本轮通过取代全系统验收。两个续做自动化保持活动；下一次先读本记录，再做上述四包的安全垂直切片。
