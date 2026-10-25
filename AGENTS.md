# 本仓库执行约束补充

本文件补充用户提供的完整金融项目AGENTS要求，不替代它；用户最新明确指令优先。

## 当前执行入口

- 课程整改总包：`docs/superpowers/plans/2026-10-23-stock-course-implementation-master.md`。
- 先读总包、对应子包、最新检查点和实际代码，再修改；按依赖每轮3–5个完整工作包，逐包先红后绿及自审，不用页面/代码存在代替验收。
- 用户最新要求：少审查、优先完整内容。按总包第9节做定向读取→实现→目标验证→一次自审，不重复全仓审计、不扩写新总计划；真实金融/权限/失败调查和必要测试不能省略。进展必须是接通功能，不是计划/审查数量。
- 不自动提交、建分支、创建仓库或替换技术栈；当前仓库无Git元数据，使用字节备份、SHA清单和精确patch保护既有修改。
- **2026-10-26更正**：仓库**确有** `.git` 元数据，上文“无Git元数据”不成立。用户当日明确授权“你执行吧……接下来我不会回答你，一直做直到全功能完善再说”（回复“需要我提交推送吗？”），按本文首段“用户最新明确指令优先”取得提交与推送授权。字节备份、SHA清单和精确patch纪律继续有效，不因改用git而放弃；不为消警重置既有基线。
- 默认冻结期货生产代码及共享认证/网关/布局/运行配置。股票独立白名单逐轮登记；共享例外必须先有单独兼容设计、期货回归和回退，不能借股票整改夹带修改。
- 2026-10-23用户最新明确“现在项目完整运行一下”，本次已授权恢复原PG/Redis/前后端/loopback connector及原后台生命周期；此前停机要求保留为历史，不再阻止本次启动。仍保留AI任务paused/user，不自动恢复付费AI；不顺带应用015/016/017或启动尚未接通的v2执行器。不因自动化提示反复拉起用户随后关闭的进程。
- 实库/HTTP/浏览器/迁移应用需要运行的验证在停机期间标记未执行；不因此勾选整包完成，不绕用其他数据库或原端口外临时服务掩盖。
- 用户要求题目二完整核心和机器学习只在A股实现；LightGBM排序是必做，普通AI聊天或期货GP不算机器学习交付。
- 不重写已有9因子/预处理/IC/组合/报告能力；新金融口径用独立版本，旧结果不改写，旧API保留。
- 数据/模型调用默认0预算；已有API Key不代表允许任意补采。不得输出密钥，先核实文档/授权/覆盖/费用，再按显式预算执行。
- 业务使用次数不限，但保留权限、幂等、参数、并发、单任务运行/内存/外部费用限制；不绕供应商TPM/RPM。
- 只做模拟和日线回放，不接真实资金；用户/JWT盘/市场/账本/缓存隔离。模型不能自确认、直接写库或发起交易。
- 单包状态只用未开始/实施中/已实现待验证/受阻/已完成并验证；源码、迁移、上下游、关键异常/权限、真实测试、人工路径未闭合不得报完成。

## 续做检查点

最新执行21（2026-10-26）见`docs/superpowers/plans/2026-10-26-authorized-runbook-pkg1-pkg3.md`：本轮把此前多轮“已实现待验证”但未落库的工作实际提交，共4个commit，全部只碰股票与新增股票模块，期货及共享认证/网关/布局/运行配置未改。

1. `c69f339` 未确认写跨页可见（②-B）：新增`frontend/src/lib/stock-pending-writes.ts`及harness/test，localStorage键`stock-pending-writes:${userId}:${tradingMode}`，`normalize()`只留kind/idempotency_key/expected_revision/signature/created_at五字段，多余字段尤其access token落盘前丢弃；卸载不清空；重试一律带回原幂等键（服务端按`(owner_id,trading_mode,idempotency_key)`去重，016的`uq_stock_v2_job_key`）。回放工作台与研究入口挂`StockPendingWritesBanner`，回放另在挂载时重放未确认项。
2. `bb1b1e8` 数据集资格分级迁移020（⑤）：`ck_stock_dataset_qualification`放宽到observation_only/research_only/backtest_ready三档并登记`stock_research_qualification_schema_versions`；`derive_qualification`对未登记reason直接抛错（不许调用方发明reason名开门），`allows()`是唯一门禁，`qualification_ready()`接进`DatasetCatalog.availability()`，分级能力默认关。
3. `65a75c0` 016任务链兼容演进021（④-1/④-2）：kind放宽六种、新增result_experiment_id/result_report_sha256/result_artifact_id、max_runtime_seconds升到30–900、`ck_stock_v2_job_result`重构成三个互斥分支（分支1仍强制observation_only）；`config.py`新增默认OFF开关；`main.py`的`_start_stock_research_v2`先全部置fail-closed默认值、开关关即返回、只读检查021已登记且有artifact root才起worker；`submit`503门禁，`cancel`故意不门禁（取消不创建任何东西）。v2路由按构造只读。
4. `43d1096` 行情K线日期视窗/六指标与跨页重复读取去重（②-A＋执行19/20前端）：27文件。三页各自断言自己的数据源不再调`getStockBoardsApi|getStockEodStatusApi|getStockMembersApi`，板块目录与EOD状态由overview持有一次，测试以`page.calls.length===1`固定；新增股票只读导航`frontend/src/lib/stock-market-navigation.ts`（行业沿用成员API原32字符/控制符边界，代码限六位数字，不引入任意query键）。

**本轮发现并修掉两个真问题**（不是补文档）：(a)`qualification.grade_allowance`拒绝所有PostgreSQL约束拼写——`pg_get_constraintdef`会加`CHECK`前缀并多包一层括号，`fullmatch`在位置0看到`(`即判“not an IN allowlist”，而裸SQLite拼写能过；不修则`validate_schema`在真实PG上永远回滚，020根本apply不了。已剥离开头`CHECK`与平衡外层括号，并新增测试钉住四种拼写加五个必须仍抛错的负例。(b) 提交`3c4ae95`是坏的：`stock-pending-writes.ts`与测试未跟踪，却有12个已提交文件import它，新检出HEAD无法构建/测试；已打`backup-before-pending-fix`标签、soft reset后带核心模块重提，并全量扫HEAD的434个`@/` import确认为0未解析（4个`.mjs`目标均存在）。

**验证（真跑）**：868全前端通过、TSC `--noEmit`退出0、六文件定向56/56；后端全量3235通过0失败（列式/ML路径需从`backend/`且`PYTHONPATH`指向`logs/stock-columnar-qa`与`logs/stock-ml-qa`绝对路径，否则`columnar_engine_unavailable`是环境不是缺陷）。执行20的12个声明文件与`logs/stock-flow-20-accepted-overlay-sha.json`逐文件SHA-256全对，字节即其验证态。`frontend/tsconfig.json`被next构建改写自身include列表的副作用已按字节还原。

**未闭合**：021/020均未apply到任何真实库，`chain_ready`/`qualification_ready`保持fail-closed；④-3 kind派发/有界子进程/finish_experiment/finish_report在写；④-4六个能力API路由未做。①重启五原端口与③tick显式预算补采仍受阻，runbook见上述文件第3节，授权前`financial_data_calls`与`paid_model_calls`保持0。未发布构建、未连接PG、未跑HTTP契约、未做本人/五年/发布回退验收，不据测试数或commit数称全系统完成。

最新执行20（2026-10-26）见`docs/STOCK-FLOW-EXECUTION-20-2026-10-26.md`：工作台只摘要/行业与业务入口，行情URL行业→成员→个股→返回保留范围，筛选默认多条件并保留两模式实例；统一股票上下文compact/折叠审计与纵滚、六卡自然高度已实现待真实验证。75目标/868全前端/TSC与`.next-stock-flow20`构建通过但未发布，五原端口停、不自动重启/迁移/paid AI。19 K线/公式/store/期货及共享父路由本轮未改。仅本轮12股票文件用`logs/stock-flow-20-accepted-overlay-sha.json`（10变2新），不是整仓接受：开始前相对19又15变5新含共享config/main/worker/021；期间又jobs.py及research_process/task_dispatch/task_execution并发4项，均观测备份不触碰/认证。1205 observed-final是瞬时观测，18完整接受+19/20声明overlay及未知差异分开保留；34补充不变。现有任务kind/worker/021已经在写，下一先核实际执行者/代码并按owner接课程闭环，不按旧dataset-only描述重造任务/021，不循环扩UI。本人/原PG/HTTP/金融权限/发布回退/五年/ML正式验收未闭合，费用0，维持02:10/07:20原续做。最终guard因并行dataset_worker/jobs/task_dispatch继续变化及tsconfig换行字节差失败，自己的12项与34补充仍一致；配置JSON全同但raw SHA已变71d9…，保留他人字节，详20最终并发补记/验证JSON，不伪报整仓保护通过。

最新执行19（2026-10-26）见`docs/STOCK-CHART-EXECUTION-19-2026-10-26.md`：股票统一K线已接已载入日期视窗、全部六指标/逐线预热、真实容器测量与日期/换源复位；日期仅dataZoom不裁指标输入，修复remount旧zoom重播及手动平移后副图错日。53目标/46股票恢复目标、最终832全前端/TSC与`.next-stock-chart19`构建通过，未发布/原PG/HTTP/本人验收，五原端口停、不自动重启/迁移/paid AI。重要：开始前相对18已存在20变化/8新增，含资格020和frontend/Dockerfile，未整体接受；仅12股票文件19声明hunk用`logs/stock-chart-19-accepted-overlay-sha.json`，正式完整基线仍18的1182/34，`...-observed-final-inventory.json`1195只是漂移观测、不能冒充新整仓接受。28未知项中26字节不动，两生产文件只补dashboard JSX闭合/board useEffect且反向单hunk后等于观测备份；旧回放历史测试接真实pending模块/懒初始化。832绿不认证未知金融代码、历史或整个系统。已核两原续做ACTIVE每日02:10/07:20北京时间，不新增重复任务。先按19末四包核未知变更与剩余页面分工，再获准真实验收/016兼容任务，不重复造图/指标/计算核；外源模型预算0。

最新执行18见`docs/STOCK-PAGES-EXECUTION-18-2026-10-25.md`：只读日线来源目录/显式source→股票详情最新日期选源、研究三页角色/实验室真实目录、交易与三账本分工、历史恢复类型已实现待真实验证。778前端/76后端/TSC和当前源码构建通过；主接受`logs/stock-pages-18-accepted-inventory.json`1182、accepted-supplemental34，旧1179仅18声明变化/1161不变，新增3，补充仅日线测试变化、另33不变。两共享路由只新增股票view属性，去属性后文件字节与备份全相同，原统一任务幂等hook及11回放业务函数不变。当前五原端口仍停，构建`.next-stock-pages18`成功但未发布，不自动重启/迁移/恢复paid AI。源选择不改旧接口默认、不绕EOD flag、不混raw/qfq，不补采/导入tick263候选，不认证历史。工作台/行情/筛选余下重复、未确认写跨页恢复、正式ML/自定义任务和真实PG/HTTP/本人/五年/发布仍未完；不重复本轮页面/目录或旧计算核，按18末四包继续。

最新执行17见`docs/STOCK-PREVIEW-EXECUTION-17-2026-10-25.md`：列表/弹窗滚动、详情主副图裁剪与12px柱宽、日线可选coverage及回测多选/bps/恢复交互已实现待真实验证；749全前端/69后端/TSC和当前源码构建通过。唯一共享例外globals.css首行Tailwind source限定src，native旧扫描9970文件含9407历史产物→新544/历史0，所有期货/股票src样式保留。构建`.next-stock-visible-fix17`成功但未发布；当前五原端口全停（原因未知），不借heartbeat自动重启。主接受1179/补充34见`logs/stock-preview-fix-17-accepted-inventory.json`及accepted-supplemental-sha；旧1178仅16声明变化、1162不变，旧33补充全不变，tsconfig构建副作用已字节恢复。605058本项目仅5根，但tick本地找到263根；因日线可能Sina/Teajoin同表upsert且丢row来源/采集/响应hash，不直接认证/混拼导入。外呼/模型/迁移0，本人滚动/原PG新coverage/HTTP/发布未验，不报全系统完成；续做先恢复获准预览验收和候选来源补齐，不重复造图或计算核。

2026-10-25 13:27运行恢复更新：用户已明确允许重启Ubuntu-24.04；首次启动E_FAIL后核实其它发行版均Stopped，正常WSL shutdown后成功启动原Ubuntu与Redis。唯一TTL诊断键读写删除及实际BGSAVE通过，connector/runtime/login均200，五原端口loopback监听，AI仍paused/user、无付费调用/迁移。证据`logs/run-20261025-redis-recovered.json`及`docs/STOCK-RUN-2026-10-25.md`顶部。下段Redis受阻/等待许可是历史，已恢复；当前源码编译内存失败仍未解决，3001仍今日02:03的`.next-stock-ux-round3`，勿称最新前端或全系统验收完成，不自动反复重启。

最新运行见`docs/STOCK-RUN-2026-10-25.md`：用户本轮明确授权完整启动，原PG/Redis/后端/connector和3001前端已拉起，但仅部分启动，后续Redis持久化MISCONF及WSL I/O故障阻塞，不能称全健康。当前源码两次构建及dev编译均Rust内存分配失败，3001暂为今日02:03成功的`.next-stock-ux-round3`，非最新模型历史前端；已明确告知。AI保持paused/user、015–019未应用、v2未放开、1178保护不变、tsconfig自动副作用已字节恢复。Ubuntu发行版重启影响其他进程，已问用户尚未执行；先看最新回复，不自动重启WSL或恢复付费任务，不用早前HTTP通过覆盖后续Redis失败。原执行16为最新代码检查点，勿把之前“全部端口停”当本轮实际状态。

最新执行16见`docs/STOCK-COURSE-EXECUTION-16-2026-10-25.md`。019私有模型历史登记/默认只读迁移runner、GET-only模型API、股票研究入口显式打开模型library（分页/详情/参数/版本/重要性/原始报告）已代码接通待真实验证；默认Flag关闭，未启服务/迁移/paid AI。联合后端122、原生协议修复后21目标、全前端737/TSC通过，真实ASGI四响应已由前端解析。最新`logs/stock-course-execution-16-accepted-inventory.json`（1178）及accepted-supplemental（33），1167旧主仅stock_factors.py及股票entry/测试3项声明变化、1164其它不变，旧27补充全不变，期货/共享配置未改。SHA自洽伪原生模型可登记问题已红修：注册实际parse Booster并核lambdarank/列/树/保存threads2；list/get无native导入，报告8MiB交互限制读前执行，GET不评价。016现实现严格dataset-only，不能沿旧计划假称通用/把ML塞dataset或直接移除约束；下一独立兼容包再打通正式训练/组合任务、结果分区与可视化UI。019原PG/并发、训练API/UI/worker、真实源/经济标签/封存/五年/本人/发布未验收，模型历史不等整个ML产品完成。UI与019包的自审前final清单保留，只用root accepted；不重复新计算核/模型历史CRUD。

最新执行15见`docs/STOCK-COURSE-EXECUTION-15-2026-10-25.md`。用户已授权子agent分域并行，本轮5包：逐页矩阵/筛选恢复、R3原价账本、滚动原生ML、私有Parquet/PIT输入及ML→同成交内核、R4严格基准/两策略比较。联合后端330/最终披露异常修复8目标、全前端714/TSC通过；真实两fold硬资源QA通过。**只用**`logs/stock-course-execution-15-accepted-inventory.json`（1167）及`...-accepted-supplemental-sha.json`（27）；早先final在最后修复前生成、verify报study_inputs变化，保留失败证据，勿当最新。1152旧主项仅声明股票screen组件/测试与ml_train三项变化，1149其它不变；旧补充仅ml_train测试变化，期货/共享配置未改。ML版本map保存排序兼容已修，固定矩阵列顺序仍严检。费用仅2022-07-01..2026-10-25、成交仅00/60主板，不称五年/全市场认证。研究输入/ML成交仍仅QA，未挂正式任务/API/UI、无019模型封存登记；真实PG/数据/本人/发布未验收，服务仍停，不重启/迁移/paid AI。下一包模型/结果登记→016持久业务/能力/API→股票正式UI→真实规则数据/全系统验收，避免重造新计算核或引用旧14声称R3/R4/滚动尚无实现。两条既有续做改为每日12:05/17:10（北京时间），原ID沿用，不重复创建；未完成整体验收不停止。

最新执行14见`docs/STOCK-COURSE-EXECUTION-14-2026-10-25.md`。固定版本内置/自定义因子→同口径IC/衰减/分组→日期purge→真实LightGBM CPU LambdaRank→样本外候选→不可变原生模型/计算报告已实现待验证；176受影响/默认无ML旧链85通过，真实512MiB Job内原生训练通过。六股票新模块，1146旧保护SHA全部不变，新主清单`logs/stock-course-execution-14-final-inventory.json`（1152）及补充15项SHA。锁版ML仅`logs/stock-ml-qa`隔离安装，默认期货venv不能import。当前标签仍未认证公司行动/退市的价格收益，报告明确无可成交回测；真实PG/数据资格/滚动/ML任务登记封存/API/UI/R3/R4/五年/本人未完成，不称ML产品或课程完成。018已属列式，后续ML模型/封存迁移用未占编号（当前019）。服务五端口仍停、不重启/迁移/paid AI；继续业务输入资格、可成交回测基准和ML产品接线，不重复造CRUD/假净值或原生训练核心。

最新执行13见`docs/STOCK-COURSE-EXECUTION-13-2026-10-25.md`。列式可选claim/IPC/父级核验发布与取消释放接线已实现待验证：147受影响后端、默认无Arrow旧JSON28、真实Job内Parquet/协议4通过；原生线程仅列式子进程各1，保留512MiB/4进程，上轮默认launcher漏传模式和原生线程问题已修。最新`logs/stock-course-execution-13-final-inventory.json`（1146）及`logs/stock-course-execution-13-supplemental-sha.json`（8）；5股票旧文件声明变化、其它1140旧保护SHA不变，新增列式入口及2测试，期货/共享配置未改。仅SQLite注入factory业务成功与真实QA短进程，未原PG/迁移/生产生命周期/本人/五年验证，不称完整worker或课程完成；五端口仍停不重启/paid AI/迁移。旧API仍DatasetRef语义，旧JSON租约/任务不能宣称列式就绪；下一包capability/真实PG/Flag与页面、PIT/自定义IC和必做ML，勿重复造JSON/下载或破坏默认JSON兼容。

最新执行12恢复见`docs/STOCK-COURSE-EXECUTION-12-RESUME-2026-10-25.md`。原锁版PyArrow26 wheel已在45秒/0重试预算内下载并核SHA，仅隔离target安装，默认期货venv不能import；真实Parquet写读/精度/缺值/损坏、登记回滚、来源metadata/缺类别显式错误、018默认只读runner/DDL静态检查已实现待验证，最终87后端受影响通过。原PG/迁移/HTTP/本人/worker接线及五年性能仍未执行，勿称D4或ML完成；不启动服务或应用015–018。最新清单`logs/stock-course-execution-12-resume-accepted-inventory.json`（1145）及`logs/stock-course-execution-12-resume-accepted-supplemental-sha.json`，不要用自审前final清单。仅旧columnar.py及两个股票测试变化、新增018 runner/测试；1143其它旧保护SHA不变，期货/共享配置未改。UI第四轮保留，继续列式任务实际接线/自定义IC/回测基准/LightGBM，勿重复下载或把SQLite/QA当真实PG/历史认证。

最新UI第四轮见`docs/STOCK-UX-EXECUTION-04-2026-10-25.md`，最新清单`logs/stock-ux-round4-final-inventory.json`（1144）：股票历史详情失败可手动重试原请求，关闭查看取消传输且晚到结果不能重开，后页空可按原条件回第一页；400/401/403/404/422不提供原请求重试，结果权限丢失清旧详情/动作。只改股票history及测试两文件，其它1142旧SHA不变；23初绿/最终708前端/TSC通过，本轮未重跑构建/后端/PG/HTTP/本人页面。五端口仍停，未重启/付费AI/迁移；外部新增调用0。可见流程已连续四轮补强，下一轮按实际断链推进自定义因子固定版本→IC/报告、Parquet任务、可成交回测基准/复现和必做LightGBM，避免重复审核或只堆UI。真实运行/人工未验收，不标系统完整。

最新UI第三轮见`docs/STOCK-UX-EXECUTION-03-2026-10-25.md`，最终清单`logs/stock-ux-round3-final-inventory.json`（1144）：基础/多条件筛选在本页打开个股并返回原结果实例，基础成员空页可回第一页且阻止offset越10000。32目标/700全前端/TSC/独立构建通过，仍已实现待验证；本人页面/PG/HTTP未执行，原五端口均停，未重启/paid AI/迁移。只改3股票旧文件、新增1股票测试，其它1140旧SHA不变；期货共享配置未改，tsconfig自动副作用已逐字节还原。保持可见页面→课程断链→真实固定数据/回测基准→本地LightGBM队列；本页显式返回不承诺刷新后恢复详情，未把VM实例测试当人工验收。

用户2026-10-25要求全部未完功能持续执行，已将原自动化调整为每日02:00/07:05（北京时间），ACTIVE，未重复创建。执行队列`docs/superpowers/plans/2026-10-25-full-system-delivery-queue.md`，保留原课程/数据/研究/ML子包。最新UI第二轮见`docs/STOCK-UX-EXECUTION-02-2026-10-25.md`，最终清单`logs/stock-ux-round2-final-inventory.json`（1143）：股票图表错误局部展示/手动恢复及无用重复计算删除，696前端/TSC/构建通过。只改2股票文件、新增2股票文件，其它1139旧SHA不变；期货和共享配置不改，tsconfig构建副作用已精确还原。服务/PG/本人页面仍未运行验证，不自动重启/paid AI/迁移；执行12列式源码仍未验证。按队列继续可见流程与课程断链，不把定时触发、测试数量或内部模块当全系统完成。

用户最新优先级为先修可见页面，执行入口调整为`docs/superpowers/plans/2026-10-24-stock-visible-workspace-remediation.md`，金融课程旧总包保留。最新UI记录`docs/STOCK-UX-EXECUTION-01-2026-10-25.md`，最终清单`logs/stock-ux-final-inventory.json`（1141）。登录背景复用正式期货展示骨架，tick ECharts完整图表已股票接线；693前端/11后端隔离及构建通过，真实服务/本人浏览器未验收。唯一期货相关兼容例外为dashboard/page纯展示提取，精确HTML和原市场分发对照通过，交易/认证未改。7声明旧文件变化、1127其它保护SHA不变。原端口仍停，不自动重启/恢复paid AI/迁移。执行12列式源码未验证，见`docs/STOCK-COURSE-EXECUTION-12-HOLD-2026-10-25.md`；不得误报Parquet完成或忽略用户UI优先级继续扩底层。

最新执行11见`docs/STOCK-COURSE-EXECUTION-11-2026-10-24.md`与`logs/stock-course-execution-11-final-inventory.json`（1129，使用final而非中间current）：Windows Job原子绑定/512MiB提交内存及4进程边界、股票launcher接入、异常终止/父进程退出回收三个内部包已实现待验证，117受影响通过。仅既有股票dataset_worker.py声明修改，其他1127旧SHA不变。真实PG/线上/迁移/五年性能/Parquet/ML未验证或未实现，新入口未挂；原五端口本轮均未监听，未重启或恢复付费AI。继续执行11下四包，历史证据不覆盖。

最新执行10见`docs/STOCK-COURSE-EXECUTION-10-2026-10-24.md`及`logs/stock-course-execution-10-current-inventory.json`（1128）：新增可执行观察IPC、父级续租/内容核验/发布与失败释放、显式租约单次消费三个内部包，105受影响通过；1126旧保护SHA不变。仅SQLite与短命CLI拒绝路径，真实PG跨进程成功/并发/硬内存/Parquet/ML未验证或未实现；无常驻生命周期、API/组件生产挂载、015/016/017应用及服务重启/付费AI恢复。按执行10下一四包继续，旧检查点保留。

最新执行09见`docs/STOCK-COURSE-EXECUTION-09-2026-10-24.md`及`logs/stock-course-execution-09-current-inventory.json`（1126）：新增固定claim实际观察manifest构建/幂等登记和双fencing回滚，93受影响通过，1125旧保护SHA不变。结果仅登记观察归档、不标job完成；无IPC可执行子程序/父级worker/Parquet/ML，API/组件未挂生产，015/016/017未应用。未重启服务或恢复付费AI；PG/在线/本人验收未执行。取消/DB失败可能留下未登记内容文件，不授权读取，GC/真实并发待验证；继续执行09下一包，历史清单保留。

最新执行08见`docs/STOCK-COURSE-EXECUTION-08-2026-10-24.md`与`logs/stock-course-execution-08-current-inventory.json`（1125）。新增观察子进程监督前置，结果结构/超时取消/回收/脱敏通过真实QA短命子进程验证，最终88受影响通过；1124旧保护SHA不变。不是完整worker、未启动项目服务/付费AI、未应用015/016/017，API/组件仍未公开。监督仅时间/输出边界，不冒充内存/进程树隔离。后续接真实数据子程序/父级租约及Parquet，不再把执行07当最新清单；执行06停机仍最新运行证据。

最新执行07见`docs/STOCK-COURSE-EXECUTION-07-2026-10-24.md`、`logs/stock-course-execution-07-current-inventory.json`（1124）。内部任务客户端/确认创建及查看取消组件已实现待验证：9目标/687前端/18后端契约回归与TSC通过；1120旧保护SHA不变，仅4新股票前端文件。组件/API仍未挂生产，worker/Parquet/迁移/本人验收未接通，015/016/017未应用；不称D4可用，不重启或恢复付费AI。执行06五端口不可用是最新运行证据，23日启动仅历史。下文执行06/05/04留作历史，继续07下一包，不覆盖旧清单。

最新执行06见`docs/STOCK-COURSE-EXECUTION-06-2026-10-24.md`与`logs/stock-course-execution-06-current-inventory.json`（1120）：新增内部任务API/响应Schema，236受影响测试通过，1118旧保护SHA不变。API未挂生产，worker/Parquet/UI仍未接通，015/016/017未应用。10月24日实际五端口均未监听，见`logs/stock-course-execution-06-runtime.json`；未猜关闭原因或自动重启，不将下文23日启动记录当当前在线。新任务Flag默认关闭，已有任务查询/取消不受接收Flag限制。按执行06下一包继续，执行05/04为历史保留。

最新执行05见`docs/STOCK-COURSE-EXECUTION-05-2026-10-23.md`和`logs/stock-course-execution-05-current-inventory.json`（1118），先读本段再读下文执行04历史。任务契约/016迁移、私有幂等提交取消、租约fencing与固定观察结果内部基础已实现待验证，12目标/230回归通过；1113旧保护SHA不变，仅五新增。016未应用，执行器/Parquet/API/UI未接通，不称任务已可用。运行许可见上文用户最新授权及`docs/STOCK-RUN-2026-10-23.md`，原服务已启动，本轮未重启/迁移/调用模型；AI仍paused/user。后续按总包D4继续，不覆盖执行05在建文件，不把旧“不重启”误认为最新停机状态。

先读`docs/STOCK-RUNTIME-HOLD-2026-10-23.md`和master顶部最新记录。B32/B33历史在线状态不能当当前在线。最新执行04见`docs/STOCK-COURSE-EXECUTION-04-2026-10-23.md`；完整当前清单为`logs/stock-course-execution-04-current-inventory.json`，用原`scripts/check-stock-b33-preservation.py verify`校验。执行03的归档/PIT/历史池保留；执行04新增有界JSON观察读取、所选因子/精确自定义版本依赖预检、未挂载只读目录API和查看组件。218后端受影响/678前端测试及TSC通过，不等于真实PG/供源/人工认证。1104旧保护中仅声明archive.py修改，其他1103不变，新增9个股票保护文件；3后端测试/文档单列，字节备份和精确差异见执行04证据。015/017未应用，D4仅读取前置部分实现、Parquet/016任务/ML未实现；新数据API/组件仍未生产挂载，不重启。R2定义管理保留但固定数据IC/回测未接通。历史清单/备份保留，不覆盖旧baseline或据声明差异回退用户修改。
