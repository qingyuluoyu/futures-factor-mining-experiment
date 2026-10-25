# A股实际页面与用户链路矩阵（2026-10-25）

**执行16增量：** A股研究/因子/回测入口已新增“查看本人模型历史”，用户点开才读取新的股票只读models API；019私有实验登记、分页/参数/模型版本/窗口/重要性与原始JSON证据预览已代码接通。默认Flag关闭，019未应用、原服务/本人仍未验收；没有训练/评估POST或完整可视化ML回测工作台，不能将历史读取计为整个ML产品完成。最新证据见`docs/STOCK-COURSE-EXECUTION-16-2026-10-25.md`和其accepted清单，下面执行15的未完成描述作为历史基线保留。

**状态：已实现待验证。** 本矩阵按当前路由分发、组件引用、API挂载和执行14检查点定向读取，不以页面存在判断产品验收。原服务仍停，本轮未启动、迁移、外部取数或调用模型；以下“已接线”仅表示代码链路已有引用，不代表真实PG/HTTP/本人浏览器已通过。并行后端新交付以各执行记录为准，本矩阵不预先把它们标为可用。

所有正式页面共用已有登录布局、市场切换门禁；股票API有`require_stock_market`，研究与回放产物还应校验用户和JWT盘。市场展示不是私有交易权限。P0为阻塞金融业务闭环，P1为阻塞用户操作或恢复，P2为后续独立能力。

|实际入口|用户要完成的事 / 当前组件|前后端链路与已存在内容|未完边界 / 优先级|人工路径（本轮未执行）|
|---|---|---|---|---|
|`/dashboard`|看市场、选行业、点个股；`StockDashboard`|`/api/stock/market/boards` → 本地盘后/导入快照 → 覆盖、样本涨跌、最多6行业 → `StockMembers` → `StockQuoteDetail`。详情关闭保留原目录实例|缺实时并非造实时；当前分类非历史池；真实服务/视觉待验。P1|工作台选行业→成员筛选/翻页→个股→返回→核对行业和关键词|
|`/market`|板块6卡→成员→tick富图表；`StockMarketView/StockBoardBrowser`|boards/members/details与`/api/stock/kline`；桌面每行6行业，窄屏2/3；`?symbol=六位代码`详情；日/周/月、MA/BOLL/量/MACD/RSI/KDJ、联动、缩放、放大/复位|行情来源/复权/前收明确；最大载入1500根，仍有更早数据只披露，不提供完整历史加载；分时/分钟/异动未接。P1|板块搜索/排序→成员代码→图表切周期/指标→财务→返回；深链/缺价/失败重试|
|`/market/screener`基础|目录/行业/代码/名称/涨跌筛选；`StockScreenerWorkspace + StockMembers`|boards行业+members有界分页50条/offset≤10000，缺价不补0；在本页打开详情并保留筛选实例|只是当前快照筛选，不是历史成分；行业目录前200明确披露。P1|改行业/关键词→翻页→详情→返回，条件和页码保持；后页空→第一页|
|`/market/screener`多条件|AND价格/涨跌/量筛选、分页；`StockScreenResults`|`/api/stock/market/screen`→`StockScreenFilters`→`screen_snapshot`；服务端回显条件/单位，响应Schema验证，指纹阻止跨快照混页|**本轮修复**409后死循环重试旧指纹：显式按已应用条件回第一页重新锚定；工作区刷新也清旧指纹；不应用草稿。P1|应用下限10→下一页→草稿改999但不应用→模拟快照变化409→刷新第一页，仍查10；短暂503重试原页|
|`/market/screener`数据资格折叠区|解释覆盖缺口和源证据；`StockDataAvailability/EodGapBrowser/StockArchiveCatalog/StockArchiveObservation`|读取已有coverage/盘后状态/eod-gaps/影子归档版本/观察数据；只读追溯，不补采，不升级资格|旧归档元数据、哈希均非PIT/公司行动/可交易认证；固定数据v2picker/jobs尚未在生产页面引用。P0|查看数据覆盖→限定日期→缺口→归档版本→核对原价；源失败显示错误，不当空集|
|`/market?symbol=...`下财务视图|看估值、披露与截止日期历史；`StockQuoteDetail + StockFundamentalsHistory`|details概览与`/api/stock/market/fundamentals`独立读取；历史每页20，证券/日期/单位验证|旧库无财报修订版本，展示公告当日并不授予同日交易权；不是PIT历史。P0|切财务→历史种类/截止日期→读取/翻页；断开财务请求，K线仍可看|
|`/factor-research`|题目二统一研究；`StockResearchEntry → FactorResearchWorkspace`|9内置因子/六类别、日期、预处理、coverage/input解释、analysis/backtest持久任务、轮询/恢复/取消→已存报告；可进自定义管理和历史|正式可成交R3/R4、可信基准、固定数据v2研究任务/模型产品未接；当前结果保持研究限定。P0|选因子/日期/预处理→输入检查→提交→任务状态/取消→报告→历史|
|`/factor-lab`|因子检验入口；同workspace默认analysis|复用内置9因子、IC/RankIC/IR/衰减/分组和理论多空，不重复另造实验室；可转当前因子至回测|自定义定义管理不等于自定义因子已进入公开IC任务。v2计算函数已有，自定义固定版本→业务任务待接。P0|检验内置因子→核对保存参数/版本→用当前因子配置回测|
|`/factor-research`内自定义弹层|定义/校验/修订/停用与历史；`StockFactorEditor`|已挂`/api/stock/research/v2/custom-factors`；DSL结构化校验、私有版本、幂等保存/停用，超时原请求恢复|定义已保存提示明确不启动IC/回测/模型；固定FactorRef进入公开任务仍未接，不能虚报自定义研究完成。P0|打开管理→合法/非法表达式→保存→历史版本→新修订→停用；跨用户不可读|
|`/backtest`|组合研究；同workspace默认backtest|已有三种加权、因子组合/调仓/topN/费用配置、旧研究净值/指标/调仓明细；持久任务及历史报告|旧研究型执行不能冒充完整A股可成交回测：同固定数据/公司行动/退市/历史池/严格成交/可信基准和完整复现待闭环。并行执行器工作另验。P0|配置→预检→提交/取消→报告→保存/导出/比较；明确研究限定|
|`/history`|本人历史研究、报告、收藏/筛选/比较；`StockResearchHistory`|`/api/stock/research/jobs`列表/详情/result，bookmarks/export/compare；私有scope，页20/offset≤10000；恢复配置可回研究页|不是独立`/reports`路由。已存旧格式可降级原导出；旧数据指纹不等于不可变原始数据。P1|筛选→开报告→关闭/暂时失败重试→JSON/CSV→两份比较→应用历史参数|
|`/trading`|真实历史数据的模拟日线回放；`StockReplayWorkspace`默认overview|`/api/stock/replay` availability/accounts/订单/撤单/step，已登记数据集才能建账；账户/用户/JWT盘隔离，独立撮合账本|不是实时交易，不接资金；是否有合格真实数据集需真实服务核查。当前并行执行器/组合工作不由本页面修改宣称完成。P0|资格→选已注册集合→建回放账户→挂单→下一交易日→核对成交/持仓/现金|
|`/orders`|回放订单与分页审计；同workspace initialTab=orders|复用股票回放账户及`order-history`，撤单/重试有版本和幂等边界|不加载期货订单；跨会话/取消/流水需真实验证。P1|选本人回放账户→订单分页/过滤→撤待单→核对版本/事件|
|`/positions`|回放持仓/T+1可卖数量；同workspace initialTab=positions|独立股票账户/撮合结果展示|不是期货保证金/卖空持仓，R3组合执行增量另验。P1|回放成交→查看持仓→跨交易日→核对可卖数量|
|`/assets`|回放现金/净值/流水；同workspace initialTab=assets|本人股票回放账户数据，不连接期货资金|缺值/估值异常不能以默认资金掩盖；完整经济回报/基准在R4。P1|同一账户比较订单成本、持仓市值和现金变动|
|`/profile`|个人/显示/安全设置|现有个人资料、安全/显示组件；A股仅info/display/security可见|期货虚拟资金/交易设置/告警/语音未作为股票功能开放，禁止越界复用。P2|切显示/安全，身份切换旧私有资料不残留|
|`/ai-settings`|配置渠道、模型、知识/记忆/工具|现有共享AI配置接口/组件，可读写授权配置；该路由在股票白名单内|**配置不是股票AI研究或ML交付**；现布局A股隐藏FloatingAssistant。AI任务paused/user保持，不因看配置恢复付费任务。P2|只查看已配置渠道/模型；验证脱敏和权限，不点付费测试/对话|
|`/ai-market`, `/ai-market/volume-profile`, `/ai-trading`|用户期望股票AI看盘/自动研究|路由文件存在，但A股`StockFeatureBoundary`拒绝挂载股票未接通内容，菜单禁用|股票链路未实现；不以期货策略/聊天或GP代替LightGBM，不移除门禁来假接线。P0后续|A股直接访问看到边界说明与股票入口，不应加载期货私有账户/WS|
|`/strategy-favorites`, `/messages`, `/ai/skills`, `/ai/skills/installed`|收藏策略/消息/技能|前者等有实际通用路由，均不在股票白名单；股票研究收藏在/history内|不把通用路由文件数算成股票功能；消息菜单标demo。AI设置中的技能导航仍须遵守边界。P2|A股直接进入应显示独立边界，返回已有股票功能|
|无`/ml`或股票ML路由|本地LightGBM排序、滚动评估、模型报告和同基准比较|执行14有真实CPU LambdaRank/固定特征/日期purge/模型内容存储和QA结果；默认运行环境依赖未安装|**公开ML任务/API/UI未实现**，无生产模型登记/测试封存/最终R3/R4比较；不能造“训练成功”页面。并行滚动评估增量另验。P0|只能在获授权后按对应测试/研究入口验证；当前没有可让用户点击训练的正式页面|
|无`/sim`、无`/reports`独立路由|模拟盘与报告入口可发现性|模拟在/trading及/orders/positions/assets；报告在/history和研究完成结果中|不新造空页面凑入口。先让已存在的业务垂直链验收，再决定是否增加别名。P1|按上述真实入口走通回放和报告，不访问虚构地址|

## 本轮实际修复、证据与兼容

只修改`frontend/src/components/market/stock-screen-results.tsx`及`.test.mjs`。三个工作包：快照变化显式恢复、工作区刷新重新锚定、后页空与上限提示。已应用条件独立可见，草稿修改不冒充当前查询。原`StockScreenFilters`/fingerprint契约、AND筛选、成交量/价格口径、后端权限、已存结果不变；不增加请求重试，不自动放宽筛选，不引入数据/模型费用、依赖或迁移。暂时503仍允许用户重试同一页同一指纹；409在有固定指纹时改为明确刷新提示。刷新读取已存快照，不执行外部采集。

原文件字节备份与SHA在`logs/stock-parallel-pages-20261025/`。修改前新增3项回归先红：`red.log`（3旧通过/3新失败），根因分别为旧指纹重试、父级刷新仍offset50、后页空误报全结果空；实现后补暂时错误与offset上限覆盖。

前端目录实际执行：

```text
node --experimental-strip-types --import ../logs/node-alias-register.mjs --test src/components/market/stock-screen-results.test.mjs
node --experimental-strip-types --import ../logs/node-alias-register.mjs --test src/components/market/stock-screen-results.test.mjs src/components/market/stock-screener-workspace.test.mjs src/components/market/stock-board-browser.test.mjs src/components/market/stock-members-workflow.test.mjs src/components/market/stock-quote-detail.test.mjs src/lib/stock-screen-api.test.mjs src/lib/stock-market-api.test.mjs
node node_modules/typescript/bin/tsc --noEmit
node node_modules/typescript/bin/tsc --noEmit --incremental false
```

初轮受影响52通过；一次自审补已应用条件可读性的回归先红（`summary-red.log`），补齐条件摘要后最终受影响53通过/0失败/0跳过（`target.log`），TSC exit0（`tsc.log`）。均为当前真实组件和API解析的离线VM/Schema验证，传输为受控测试响应；不是原HTTP、真实PG或浏览器验收。未执行生产构建/后端/数据库迁移/真实数据/本人浏览器/部署回退。未修改期货、共享认证/网关/layout、运行配置；不提交、不建分支，全局保护清单由主执行统一核验。

回退：先核查当前SHA和后续修改，只反向应用本轮两股票文件的精确差异；备份不是授权覆盖后续用户修改。无需数据库回退，不删除历史产物。剩余金融P0风险和未挂载能力如上表。

下一四包依原课程依赖：合格固定数据接公开研究；自定义固定FactorRef接IC/任务/报告；R3可成交模拟组合及R4可信基准/复现；ML滚动/封存/硬资源任务/API/UI并与同一执行器比较。随后再做原服务/本人页面/两市场在线回归，不再用增加审查文档或按钮数替代金融功能完成。
