# 本仓库执行约束补充

本文件补充用户提供的完整金融项目AGENTS要求，不替代它；用户最新明确指令优先。

## 当前执行入口

- 课程整改总包：`docs/superpowers/plans/2026-10-23-stock-course-implementation-master.md`。
- 先读总包、对应子包、最新检查点和实际代码，再修改；按依赖每轮3–5个完整工作包，逐包先红后绿及自审，不用页面/代码存在代替验收。
- 用户最新要求：少审查、优先完整内容。按总包第9节做定向读取→实现→目标验证→一次自审，不重复全仓审计、不扩写新总计划；真实金融/权限/失败调查和必要测试不能省略。进展必须是接通功能，不是计划/审查数量。
- 不自动提交、建分支、创建仓库或替换技术栈；当前仓库无Git元数据，使用字节备份、SHA清单和精确patch保护既有修改。
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
