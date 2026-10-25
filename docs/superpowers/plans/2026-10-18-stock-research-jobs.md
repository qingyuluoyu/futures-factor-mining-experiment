# A股研究任务与历史报告垂直切片（07:10续做）

基于上轮真实检查点继续，不重做已验证的注册/板块/图表，不接真实资金，不调用模型或上游。源码已备份至logs/stock-research-jobs-backup-20261018-0710。用户要求直接实施；不提交、不建分支。

## 现状及最小架构调整

当前研究API在HTTP内读取数据并在线程中计算，取消HTTP不能停止计算，关闭页面无持久结果，股票/history仍被隔离。简单BackgroundTasks仍会在重启时丢任务，也不能可靠终止线程。保留FastAPI/PG单体、原研究方法及旧API兼容，新增PG持久队列和可终止的单计算子进程，不新增队列服务/框架/依赖。不修改期货scheduler。新功能由STOCK_RESEARCH_JOBS_ENABLED控制，默认false，迁移/自测后本机启用。关闭旗标可回退，保留新表和报告，不能整库恢复覆盖新用户数据。

## 四个工作包

1. 独立ResearchJobBase模型、版本迁移/校验和状态约束；不注册到旧Base.create_all，迁移前不读取不存在的表。所有写操作事务与行锁，创建的幂等键/摘要按用户和JWT盘隔离。原API参数Schema提取共享而保留旧导入名。
2. 单计算槽位持久队列、受限数据快照和指纹、执行/取消/超时/崩溃恢复。PG事务advisory锁串行队列状态变更，running行lease+worker_token防旧worker覆写；无自动重试（attempts最多1）。每进程只能有一个计算子进程，DB全局最多一个running；旧worker失败的迟到结果不写入。子进程持有只读repeatable-read快照；无上游、模型或交易工具。参数、输出和错误经过校验，JSON结果最多8MiB。实际运行上限120秒、队列等待上限15分钟；worker关停终止子进程并将运行任务安全结束，崩溃遗留lease到期标expired，不擅自重复计算。
3. 研究页面创建/轮询/取消/刷新恢复；股票/history提供有权限的分页报告列表与详情，复用同一结果/风险组件。股票和期货history分流，原期货页面保留。网络失败保留任务ID和原幂等键供重试，不盲目新建任务；切用户/JWT盘清空旧轮询和结果。旗标未启用时明确显示不可用，不显示假报告。
4. 先红后绿，专项、PG迁移首次/重复/check、真实HTTP/并发/权限/盘/取消/缺价/超时、浏览器、全量回归、tsc/build、记录检查点与回退；不删除或跳过旧测试，不以mock替代实际计算正常链路。

## 固定模型契约（后端名称snake_case）

表stock_research_jobs：id UUID；user_id UUID FK users(RESTRICT)；trading_mode live/virtual；kind analysis/backtest；title varchar80；status pending/running/completed/failed/cancelled/expired；step varchar32；progress int0..100；request_id varchar32；idempotency_key UUID；request_hash char64；params_json JSONB；method_version varchar64；workflow_version varchar40；data_version varchar64 nullable；data_manifest JSONB默认{}；result_json JSONB nullable；result_bytes int默认0；attempts int0..1；worker_token UUID nullable；created_at/updated_at timestamptz非空；started_at/finished_at/deadline_at/lease_expires_at timestamptz nullable；error_code varchar64 nullable；error_message varchar500 nullable；error_status int nullable。唯一(user_id,trading_mode,idempotency_key)；索引(user_id,trading_mode,created_at,id)、(status,created_at)、(status,lease_expires_at)。完成状态须有result_json/data_version/finished_at，其他状态不得有result_json；running需要worker_token、started_at、deadline/lease；所有终态finished_at非空。独立版本表stock_research_schema_versions，版本20261018_002_stock_research_jobs；advisory迁移锁20261018002，队列状态锁20261018003。

合法状态：pending→running/cancelled/expired；running→completed/failed/cancelled/expired；终态不再迁移。终态取消重复请求保持终态（已completed/failed/expired取消409，cancelled幂等200）。取消会清结果；不删除报告。每用户+JWT盘最多1个active（pending/running），全局最多8个active；重复同键同payload先返回既有任务，不因队列限额产生重复；同键不同payload409；满载429且Retry-After，不以禁用前端按钮代替。

## 新API契约

前缀/api/stock/research；使用AuthContext，只认JWT的user_id/mode，不接受客户端身份覆盖；新列表limit1..50默认20、offset0..10000。响应带X-Request-ID，POST返回202，GET/取消200。未开启或未迁移503；跨用户/跨JWT盘404；result未完成409；已失败仍可通过详情查看错误。

- GET /availability → {enabled:boolean,accepting:boolean,max_active:8,max_user_active:1,max_runtime_seconds:120,reason:string|null}。worker未运行时accepting=false，创建不得悄悄排入无人消费的队列。
- POST /jobs → {idempotency_key:UUID,title?:string(1..80),kind:'analysis'|'backtest',parameters:对应旧AnalysisBody/BacktestBody}；严格额外字段校验，不接受未知参数。开始/结束日期兼容旧三格式；结束空在首次创建时固定上海当天，已存在幂等重试不随当天变化。最大研究区间3660天，结束不能在未来。返回JobView。
- GET /jobs?limit&offset&kind?&status? → {items:JobView[],total:int,has_more:boolean}，列表不包含结果大对象。
- GET /jobs/{id} → JobView（包含params和data_manifest）。
- POST /jobs/{id}/cancel → JobView（无body，状态幂等）。
- GET /jobs/{id}/result → 保留原分析/回测结果所有字段，附research_job_id、data_version、data_manifest；只完成可读，带方法/工作流版本，私有结果不共用缓存。

JobView：id、kind、title、status、step、progress、request_id、attempts、created_at、updated_at、started_at|null、finished_at|null、method_version、workflow_version、data_version|null、has_result、error:null|{code,message,status}；详情额外params:Record和data_manifest:Record（客户端可作可选）。mode由上下文隔离，所有结果仍标stock/research_only，不能混用期货。

## 数据快照公开接口与限制

app.services.stock_factors.snapshots.load_versioned_market_data(session_factory,start_date:date,end_date:date) → (MarketData, manifest:dict)。在同一只读repeatable-read事务中取得本地真实数据，固定排序、有界查询。最多1000000价格行、1000000估值行、100000财务行、10000证券、2000000面板格；读取limit+1并明确拒绝超界，非静默截断。哈希覆盖实际计算输入及证券当前分类/名称；manifest.data_version为sha256、source本地授权导入库、市场/单位/时区/频率/复权/日期/行数/覆盖/采集时间、当前分类和历史修订未认证限制。指纹不是不可变原始数据归档，报告须明确此边界。缺价仍由原金融引擎拒绝，不能填平。未知/非有限/冲突数据不能伪造成0。

## 所有权与校验顺序

模型/迁移包独占models/stock_research_job.py、migrations/20261018_002_stock_research_jobs.sql、scripts/migrate_stock_research_jobs.py和相关新tests。数据包独占services/stock_factors/snapshots.py及其新tests。前端包独占新stock-research-api客户端/测试、研究页与共享报告/历史组件、history/page、market-routes及侧栏股票标签。主流程独占共享Schema提取、jobs服务/API、worker/子进程、main/config/.env.example、HTTP验证脚本与交付记录。禁止写其他包文件、启动/停止服务、真实迁移/导入或付费调用，由主流程统一操作。互相只依赖上述明确契约，不传完整数据库或密钥给模型。

先验证默认关闭旧业务可启动→PG备份→增量迁移→重复/check→本机开启→受控重启→真实链路/浏览器→全量/构建。若未验证完整链路则保持关闭，记录部分完成。数据补全、股票AI与生产容量不在本轮伪称完成，后续按安全包继续。

## 07:10轮最终检查点（2026-10-18 07:39）

四包实现及本机链路验证已进行，完整证据见`docs/STOCK-RESEARCH-JOBS-VERIFICATION-2026-10-18.md`。最终后端1645 passed/3既有skipped、前端237 passed、tsc退出0；真实HTTP23、PG并发8、原扩展19、原回放18项通过。迁移已应用并重复/check，旗标关闭/开启回退实测通过，本机最终开启。构建命令退出0但Windows standalone追踪symlink EPERM仍需部署环境验证，不能报完整生产验收。

执行中细化：取消/过期保留worker_token物理计算槽，实际杀停后才ACK释放；孤儿子进程绝对deadline+5硬退出，槽deadline+10后回收，防逻辑取消与实际停算之间并发。正常财报同公告日多报告期不是冲突，新增latest-disclosed-report-frontier-v1择期并纳入快照指纹。旧同步研究未共用此规则，明确留下一轮，不能误称金融双轨一致。完整原始数据归档/PIT/逐行授权仍未认证。

07:43补充：真实报告曲线继承全局K线留白，主题effect会覆盖fitContent，已仅在MiniLineChart局部修复并补4项先红后绿回归；真实浏览器确认9个净值点完整可读。保留原/修复后截图，未改期货K线配置。

下一轮四包：共享旧新财报选择与版本回归；有界覆盖/默认日期与增量审计；股票AI只读预算/工具/持久报告链路；部署产物/容量/监控验收（随后才接AI日线模拟）。不停止两个续做自动化，不覆盖已有修改。
