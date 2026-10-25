# 执行20 P3：行情上下文与板块内容滚动

状态：已实现待真实验证。紧凑上下文/详情隐藏、总览纵滚与受控行业成员入口已实现，28项目标测试和定向类型检查通过。原PG/HTTP/本人浏览器/发布未执行，不能据CSS与VM断言声称真实滚动已验收。服务仍停，未启动服务/worker、构建、迁移、外呼或安装依赖，新增数据/模型预算0。

按`docs/superpowers/plans/2026-10-25-stock-chart-completion.md`执行20段完成三个包：兼容紧凑上下文与详情隐藏；自然内容高度和受控行业往返；目标验证与一次自审。承接19的K线，不再改图表/指标。root拥有dashboard/market-view，visible拥有screener，本包只改下列四文件，不认证相对19已观测15变化/5新增或19旧未知20/8。

## 实际文件与数据流

- `frontend/src/components/market/stock-market-overview.tsx`：新增可选presentation/suppressContext；同一boards状态和同一coverage JSX复用，不新增API，不复制金融或同步状态。外层统一有界纵滚、普通children自然高，详情模式提供有界填充区域。
- `frontend/src/components/market/stock-market-overview.test.mjs`：覆盖compact字段、单一折叠详情、唯一EodStatus、隐藏/恢复不重读及同一数据实例、错误恢复、滚动布局契约。
- `frontend/src/components/market/stock-board-browser.tsx`：受控industry非空时直接显示该行业成员；返回总览同时调用onIndustryChange("")，空行业仍可显式选全部股票。板块容器去自身h-full/overflow，保留六卡栅格/筛选/排序和成员原入口。
- `frontend/src/components/market/stock-board-browser.test.mjs`：受控行业首开与返回、全部股票、外部行业变化和自然高度；原覆盖/审计/刷新/六卡等断言保留。

StockMembers函数及其前面的imports/comments与本轮修改前字节完全相同，未改分页、参数、50行表格、10000 offset上限、关键词/排序、错误恢复或取消逻辑；`stock-members-workflow.test.mjs`仅执行，未修改。

## Props兼容契约

`StockMarketOverview`新增：

|字段|默认|行为|
|---|---|---|
|presentation?: "full" \| "compact"|full|full原覆盖/来源/同步呈现保留；compact用真实行情日、来源、复权、当日覆盖短摘要，原覆盖卡/来源字段/唯一EodStatus放在一个默认关闭的详细区域|
|suppressContext?: boolean|false|上部整个metadata容器hidden+inert，保留挂载；详情children获得min-h-0/flex-1有界区域|

原industry/onIndustryChange/refreshKey/collapsible/children契约保留。full仍遵循collapsible决定同步状态是否折叠；compact始终在同一默认折叠区域放原详细内容，不另造一份同步状态。缺少旧响应可选计数/来源追溯字段时继续不编造0或版本；失败/加载提示保持可见且可手动重试。

外层`flex h-full min-h-0 min-w-0 flex-col overflow-y-auto`，正常children容器`min-w-0 shrink-0`，板块自然内容高；suppressContext时children改为`min-h-0 min-w-0 flex-1`供原详情h-full填充，避免让上方metadata与详情分别争抢整页高度。隐藏不卸载children/元数据，也不更改读取effect依赖，不因切详情/返回增加目录读取；EodStatus自己的既有生命周期保持，不承诺停止其原有更新。

BoardBrowser的成员状态由非空industry直接决定，本地drilled仅用于“全部股票”；外部切到非空行业会清除旧的全部股票状态，后续外部清行业即可回板块。返回按钮同时清本地drilled和通知父级清行业。URL解析/长度/导航仍由root负责，本包没有拼接URL或改API。

## 实际验证

frontend目录：

```powershell
node --experimental-strip-types --import ../logs/node-alias-register.mjs --test src/components/market/stock-market-overview.test.mjs src/components/market/stock-board-browser.test.mjs
node --experimental-strip-types --import ../logs/node-alias-register.mjs --test src/components/market/stock-market-overview.test.mjs src/components/market/stock-board-browser.test.mjs src/components/market/stock-members-workflow.test.mjs
```

第一条先红5失败/16通过，明确缺compact、suppress、有界根和受控行业即成员；实施后21通过。追加异常/状态恢复与原成员回归后首次27通过/1失败：新增测试在VM的settle后未主动重渲染就读取published，拿到旧null。补测试重渲染后仍精确断言恢复的是同一个boards实例，未改生产逻辑或放宽断言；最终28/28通过，无跳过。

根目录定向类型检查：

```powershell
node frontend/node_modules/typescript/bin/tsc --project logs/stock-market-context-20-tsconfig.json
```

退出0。QA配置只在logs，extends原frontend/tsconfig，选择这两个生产组件及真实依赖，noEmit/incremental=false，typeRoots指向已装@types。未修改共享tsconfig、关闭类型检查或安装依赖。没有本包全前端/全TSC/构建；root统一整合。visible另报含新overview的49联合通过，此处不冒充本包亲自执行结果。

目标测试实际覆盖：full默认原字段与审计证据不丢、legacy不造可选元数据、唯一目录read/唯一EodStatus、相同revision刷新、compact失败重试、关闭/晚响应、hidden/inert切换保持实例、URL行业props直接到成员/返回清父级、六卡桌面响应式、null涨跌排后及0涨跌保留，原成员分页/自然50行/上限/取消仍通过。测试是React函数/VM生命周期与真实API调用边界桩，不等于已运行服务或本人视觉验收。

## 保护、风险与回退

修改前四文件逐字节备份：`logs/stock-market-context-20-backup/`及before-sha.json；本包overlay为`logs/stock-market-context-20-overlay-sha.json`，精确patch为`logs/stock-market-context-20-changes.patch`。这些只登记本包四项hunk，不替代完整接受基线、不接受未知金融/配置/worker差异。一次自审对照精确差异核新props默认兼容、覆盖和状态只有一份、只隐藏不卸载、industry受控/本地全部状态转换、成员原函数字节不动、纵滚容器职责，未扩大修改范围；代码和测试现已冻结。

未改金融公式、价量/复权/涨跌、来源、数据版本、权限/API、数据库或缓存；成本影响只有本地布局渲染，目录读取次数与原revision规则相同，新增外源/模型调用0。剩余风险是原浏览器真实高度/滚动及root/visible三入口接线需要整体验证；本包没有替其它页面宣称链路全部验收。

人工路径（获准原服务部署后）：打开行情页检查摘要/折叠覆盖来源/同步状态并滚动到下方板块；从带行业入口直接见成员，返回总览同时清行业，再显式查看全部股票；开成员详情确认上部metadata不可聚焦/不可见、图表有足够高度，返回保持原结果与条件；筛选compact模式同样验证；刷新和错误重试只按revision再读，检查移动端六卡/表格横滚和整页纵滚。本人/原PG/HTTP均未执行。

回退只在核当前SHA与后续修改后撤本包精确hunk，恢复原可选props缺省和BoardBrowser行为，不能整体覆盖其它来源未知内容；无数据库回滚或外部副作用补偿。下一轮三个工作包：root/visible三页面入口联合回归；获准浏览器滚动/返回和发布回退验收；继续原016任务/可信数据课程链路，不再扩展重复K线或计算核。
