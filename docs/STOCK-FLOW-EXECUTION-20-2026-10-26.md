# 执行20：股票工作台、行情与多条件筛选分工

**整体状态：部分完成。** 余下三页职责与滚动链路已实现待真实验证，四包完成离线目标/联合回归、TSC与当前源码构建；未发布、未启服务、未完成本人/原PG/HTTP验收。19统一K线和原计算核保持，不再重复移植或增加假指标。用户看到的旧预览不会因本次静态构建自动更新。

沿`2026-10-25-stock-chart-completion.md`执行20段：根独占工作台/行情路由和导航工具；visible独占筛选；daily独占股票总览/板块及直接测试。默认冻结期货/共享认证/网关/布局/运行配置；未知其他执行者变更只观测、不认领或回退。

## 四个实际工作包

1. **工作台只摘要，真实跳转到行情。** 修改`frontend/src/components/dashboard/stock-dashboard.tsx`/测试、`market/stock-market-view.tsx`/测试，新增`frontend/src/lib/stock-market-navigation.ts`/测试。工作台移除完整StockMembers列表和StockQuoteDetail实例，仅保留真实覆盖上下文、样本涨跌/行业前六、因子库/检验/回测/模拟/历史入口。行业排行变成真实`/market?industry=…`链接；行情URL行业由原overview受控传给板块浏览，直接显示对应成分，选股进入同一industry+symbol深链接，详情返回保留行业；板块工作区继续挂载hidden/inert，不因看详情重建条件。原无industry的symbol深链接和六位代码兼容。行业按成员API的32字符上限校验并URL编码，非法范围显示错误与显式恢复，不静默扩大成全部股票或误选证券。未知超限分类不能伪装为该行业链接。
2. **筛选页默认多条件。** 修改`market/stock-screener-workspace.tsx`/直接测试；复用既有真实StockScreenResults/成员分页/API。默认多条件输入和结果，基础股票目录显式选择，第一次选择前不挂基础目录；访问后两模式hidden/inert保留已挂载实例，切换/详情返回不丢应用条件、页码及数据资格展开状态。行业变化按原key重建，刷新只传revision不改key；不把行情页再次作为默认筛选页。筛选只代表当前目录/本地行情条件，原“不是回测历史股票池”的提示保留。
3. **统一股票上下文和纵滚。** 修改`market/stock-market-overview.tsx`/测试与`stock-board-browser.tsx`/测试。增加可选presentation='full'|'compact'、suppressContext，默认full兼容。三个页面明确选compact，首屏只显示真实来源/行情日/覆盖短上下文，完整覆盖、来源追溯和唯一EodStatus保留在同一默认关闭详情里，避免三个页面首屏都是大覆盖面板。打开筛选详情时元数据hidden/inert但不卸载、不重新读取；内容区域给详情有界空间。overview独占有界纵滚，工作台/板块/筛选自然内容高，板块原桌面一行六卡、成员分页/搜索/取消保留；URL行业非空直接显示成员，返回板块清父行业，空行业仍可显式全部股票。StockMembers函数及其前部imports字节与本轮备份完全相同，没有借布局修改金融或读取逻辑。
4. **整合与真实保护记录。** 原19的日期视窗、全指标、EMA/MA前缀、主副图同日测试继续回归；四页层级/隐藏/刷新与旧市场分发一起验证。无需新API/表/依赖/模型调用；当前源码构建`.next-stock-flow20`成功但没有部署。本轮不调整已核实02:10/07:20续做时刻，不删除未完续做。

## 金融、权限和成本边界

样本涨跌/行业均值仍用原真实boards响应，不冒充指数/全市场；缺价不补0，目录不等历史完整，行业仍当前分类。源选择、raw/qfq、股/手、小数成交量、19全部指标公式/方法版本/回放游标、撮合和账本均未修改。工作台仅只读导航，不创建研究/交易或恢复paid AI；股票身份和实际资源权限继续原API/父路由执行，期货分支/共享会话不改。

目录/EOD每工作区仍原唯一读取，展开详情不新建第二份请求；基础目录只有主动访问才挂载。访问过两筛选模式后同时保留实例，显式刷新可能分别更新两种有界本地结果，不新增上游请求或无限读取。新增金融源/模型预算与实际新增调用均0；没有安装包、应用迁移或补采。

## 实际验证

frontend实际命令：

```powershell
node --experimental-strip-types --import ../logs/node-alias-register.mjs --test --test-concurrency=1 src/components/dashboard/stock-dashboard.test.mjs src/components/market/stock-market-view.test.mjs src/lib/stock-market-navigation.test.mjs
node --experimental-strip-types --import ../logs/node-alias-register.mjs --test --test-concurrency=1 src/components/dashboard/stock-dashboard.test.mjs src/components/market/stock-market-view.test.mjs src/components/market/stock-market-overview.test.mjs src/components/market/stock-board-browser.test.mjs src/components/market/stock-screener-workspace.test.mjs src/components/market/stock-members-workflow.test.mjs src/components/market/stock-screen-results.test.mjs src/lib/stock-market-navigation.test.mjs src/components/market/tick/date-window.test.mjs
$allTests = @(rg --files src -g '*.test.mjs')
node --experimental-strip-types --import ../logs/node-alias-register.mjs --test --test-concurrency=1 @allTests
node node_modules/typescript/bin/tsc --noEmit --incremental false
```

根21目标、合并75目标通过（`logs/stock-flow-20-root-ui-final.log`/`...-target.log`）；最终**868/868全前端通过，0失败/跳过，47.52秒**（`...-frontend.log`，实际文件列表`...-frontend-test-files.log`）。子包49筛选/28上下文、根目标与75均有重叠，不能相加当独立总量。包含原期货分发/身份隔离、成员搜索分页/取消、筛选结果恢复、真实ECharts SVG与日期完整输入回归；受控React/DOM节点不是真实本人页面验收。

完整TSC前后exit0（`logs/stock-flow-20-tsc.log`/`...-tsc-final.log`）。构建进程局部BACKEND_URL=http://127.0.0.1:8002、WENYUAN_BUILD_DIR=.next-stock-flow20、WENYUAN_PREVIEW_BUILD=1、RAYON_NUM_THREADS=2、NODE_OPTIONS=--max-old-space-size=2048，运行`node node_modules/next/dist/bin/next build`，**exit0、编译29秒、40页生成成功**，`...-build.log`；ID `8PxA-lkzgfqmPK_oR0QuL`。只撤Next新增/重排的生成类型include，其它配置比对相同，按`...-tsconfig-before.bin`逐字节还原并再次TSC通过；原SHA05636e5b2998cb7ee216821014240e016a2fb8f4ceca4ef9c9a80b8ed2b42250，生成patch保留。

红例/中间失败均留证：导航模块不存在2红（`...-nav-red.log`），工作台仍挂列表/行业跳转丢上下文/非法行业扩大范围先红（`...-root-ui-red.log`），首屏仍重复大面板2红（`...-compact-red.log`）；既有宽泛“版本”断言误匹配新增“自定义版本”入口，保留first-green失败，将同一原来源禁止伪造断言精确落在市场覆盖/来源块上，未移除或放宽缺失元数据断言。筛选首红6项/上下文首红5项及一次测试VM重渲染接线修正见两个子包文档/日志。没有删除/skip测试或关闭typecheck。

一次定向自审复核：工作台不再挂第二套股票浏览；行业scope长度/URL编码/返回链真实可走；来源与数量不丢、无缺价补0；两筛选模式和详情层级隐藏但条件实例不卸载；唯一滚动容器不再被h-full子页重复挤占；旧期货父路由不改。本轮结束可见页面职责代码切片，后续除真实验收反馈外回课程/可信数据闭环，不循环增加UI或重造已有计算核。

## 并发差异：本轮不是新整仓接受

开始前以19观测快照核guard失败：相对19又有**15既有变化/5新增**，包括共享`backend/app/core/config.py`/`app/main.py`、任务Schema/模型、dataset前端、pending banner、worker/021迁移/runner。观测1200在`logs/stock-flow-20-observed-before-inventory.json`，20项字节备份与差异`...-preexisting-delta.json`/`...-preexisting-backup/`。本轮未触碰它们，截至整合仍与备份完全相同；没有把共享改动当股票白名单放行。

实施期间再次出现非本轮持有的`stock_research_v2/jobs.py`变化和`research_process.py`、`task_dispatch.py`、`task_execution.py`新增，属于并行未关单修改；已保存`...-concurrent-delta.json`与字节备份，不回退、不覆盖、不纳入本轮验收。首次整合保护断言因发现这4项而失败，未放宽成全仓接受，而是明确分离本轮12项与并发4项。当前类型已出现research_ic/backtest/benchmark/ml_train/report，worker/main/021存在且配置默认OFF；下一轮必须先读实际代码和对应执行者结果，不能沿旧记录再称“全无任务代码”或重复造021。存在不等正式任务接通/金融验收通过；本轮没有验证或运行这些新代码，也不把前端868绿当其安全证据。

本轮独立声明**12股票文件（10变化/2新增）**在`logs/stock-flow-20-accepted-overlay-sha.json`，仅接受本轮hunk且仍待真实验收。两个子包冻结SHA复核通过；root备份/精确patch`...-root-backups.json`/`...-root-before-*`/`...-root-changes.patch`，子包见`stock-screener-pages-20-*`和`stock-market-context-20-*`。34补充SHA仍与18相同。

最新1205快照`...-observed-final-inventory.json`是瞬时漂移观测，**不是新正式整仓基线**，并行执行者未关单时可能继续变化。18完整接受+19/20逐轮声明overlay仍分开保留；同路径20的声明增量优先，但旧未知差异/并发写入都不能被snapshot覆盖成“已接受”。19的Dockerfile/资格020等未知变更也没有在本轮被认证。期货/共享冻结保证仅指本轮没有修改它们，不能保证其它既有执行者的共享变更已过期货回归。

### 最后保护核验的并发补记

最终整仓guard **失败**（`logs/stock-flow-20-observed-final-verify.log`）：并行的dataset_worker.py/jobs.py/task_dispatch.py继续变化，另有frontend/tsconfig.json字节被后续执行者改写。本轮自己的12股票文件最终SHA与overlay全相同、34补充也全相同；不能把这些局部通过写成整仓保护成功。晚到4项已逐字节备份`...-concurrent-late-backup/`与`...-concurrent-late-delta.json`，不覆盖它们，不循环追逐正在写的源码。

本轮Next副作用先已按原bytes还原并TSC exit0；之后的tsconfig当前SHA为71d9c7f89a19cb926d5386d52b962de49476fc9a72dfcea7ffa1b5477afc3a0b，原为05636e5b2998cb7ee216821014240e016a2fb8f4ceca4ef9c9a80b8ed2b42250。已严格证明仅CRLF/LF换行字节差、完整JSON（含include）相同；保留后续写者的原字节，不再次还原以覆盖其修改。最终12项/34项/配置语义/调度核对见`logs/stock-flow-20-final-verification.json`；`original_tsconfig_bytes_at_final=false`且`full_repository_accepted=false`，如实保留，不把比较方式变化冒充原SHA一致。当前源码构建/测试的通过只对应已冻结股票前端和当时配置，不认证仍在修改的后台任务链。

## 未执行、回退和人工路径

2026-10-26 04:55只读核3001/8002/8110/5433/6380均无监听，`logs/stock-flow-20-runtime.json`。未启动PG/Redis/前后端/connector/worker、应用015–021或恢复paused/user付费AI。原PG、运行HTTP、本人可见滚动/小屏/原期货完整功能、当前发布及运行回退、五年/全市场/正式ML业务任务均未执行；没有以SQLite/另端口/测试夹具冒充。本轮后端没有代码改动/新增验收结果。

静态兼容：overview新props可选且默认full；原stock API/计算/stock store和共享父路由保留。回退先核当前SHA再按本轮独占hunk撤，不整文件覆盖并发修改；工作台行业链接与行情行业解析应一起撤，恢复旧页面时同步撤可选展示props及导航工具引用。原数据/模型/账户/报告/未确认请求保留，无DB回退或删除数据；保留19和20成功构建。真实发布回退未执行。

人工路径（待明确运行许可）：工作台应是摘要和研究入口，点击行业进入对应成分列表→打开股票统一19日/周/月K及指标→返回仍同一行业/原板块条件；行情首屏是板块六卡，详细覆盖在折叠区，50行成员可滚到底；筛选默认多条件，基础目录第一次主动打开，设置两种条件分别切换/看个股/返回/刷新，条件页码不丢，行业改变才重置；进入个股时上方覆盖隐藏且KDJ/页尾可滚到；核非法行业不发扩大查询；最后原期货入口/图表/交易/历史独立回归。

下一四包：①与实际并行执行者对齐现有worker/021/任务分派与资格020，按owner分域验收、不重复新核或覆盖未关单源码，核共享启动门禁/权限/成本/回退；②在已存在课程任务上接固定数据→精确自定义版本→IC/可成交回测/基准→报告及本地LightGBM正式产品入口，未接通保持Flag关；③既有真实数据/PIT/经济标签/历史股票池与tick候选来源对账，预算仍0，不补采或混锚点；④获新运行许可后原PG/HTTP/本人、期货回归与发布回退验收。可见三页本轮不再扩计划；未达全部核心验收不称系统完整、不删除未完续做。
