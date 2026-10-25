# 执行20 P2：筛选默认多条件与结果上下文保留

状态：已实现待验证。本包目标49/49通过；未运行全量构建、启动服务、应用迁移、连接PG或调用外源/模型。TSC/全前端/发布由根整合，真实HTTP与本人浏览器未验收。

实际修改仅`frontend/src/components/market/stock-screener-workspace.tsx`及同目录`stock-screener-workspace.test.mjs`。基于本轮观察到的当前字节继续，不覆盖19之后15变化/5新增，不接受未知research/replay/worker/共享运行配置差异。

## 页面与数据流

- 筛选默认挂载真实已有`StockScreenResults`，以价格、涨跌幅、成交量等结构化条件及原分页结果为主，基础股票目录改为显式按钮。没有重写结果组件、服务或金融核。
- 基础目录第一次点击前不创建`StockMembers`节点；访问后与多条件结果各在固定位置的section保留挂载，用hidden/inert切换，不因模式切换重建条件、分页或结果实例。打开个股仅隐藏/inert父main，详情返回恢复原模式和实例。
- 两种结果仍以`overview.industry`为原key；只有行业变化按原设计重建。刷新只传`overview.revision`，不把刷新值或模式加入key。原多条件刷新会按已应用条件重锚第一页、取消旧快照指纹；未应用草稿仍由原组件保留。
- 消费同轮新增可选overview契约`presentation="compact"`、`suppressContext={selectedCode!==null}`。覆盖/来源/审计上部在看详情时隐藏但不卸载；筛选main取消h-full、min-h-0和独立纵向overflow，使用自然内容高度，由overview统一外层滚动。元数据/目录读取仍由overview持有一次，筛选未另发相同目录请求。
- 数据覆盖资格仍是显式查看，打开个股返回保留其展开选择；当前行业不代表历史成分、缺价不填零、不等于停牌、筛选不等于历史回测股票池等说明保留。

金融公式、数据来源/单位/复权、权限/市场分流、分页边界、旧接口、全局store与收费模型调用没有变化。默认工作流从成员目录查询改为已有多条件查询，是用户授权的页面职责变化，不表示额外补采。基础目录访问后手动刷新会更新已挂载的两种结果；其请求仍为原有有界本地API，没有新增自动轮询或上游费用。

## 红绿与一次自审

初次定向10项4绿6红，失败集中于旧基础默认、模式卸载及未消费compact/scroll契约，证据`logs/stock-screener-pages-20-red.log`。实现首轮10/10后增加基础详情返回路径，最终联合49/49、0失败/跳过，证据`logs/stock-screener-pages-20-green.log`。

在`frontend`实际执行：

```powershell
node --experimental-strip-types --import ../logs/node-alias-register.mjs --test src/components/market/stock-screener-workspace.test.mjs
node --experimental-strip-types --import ../logs/node-alias-register.mjs --test src/components/market/stock-screener-workspace.test.mjs src/components/market/stock-screen-results.test.mjs src/components/market/stock-members-workflow.test.mjs src/components/market/stock-market-overview.test.mjs src/lib/stock-market-api.test.mjs src/lib/stock-screen-api.test.mjs
```

覆盖默认真实多条件、未访问基础不挂载、两模式同位置/type/key与hidden/inert保留、两类详情返回、行业重置/刷新不换key、唯一元数据读取/状态节点、compact及详情抑制/单外层滚动、目录错误与取消，以及原结果组件草稿/应用条件、409快照恢复、刷新第一页、503失败重试、权限scope取消晚响应、10000分页与API参数/金额/身份契约。组件夹具验证不等于本人真实浏览器挂载/滚动验收。

一次定向自审：读取本包相对字节备份的精确`git -c core.autocrlf=false diff --no-index`，核对当前StockScreenResults请求依赖只受原条件/行业/refresh影响，确认切模式不改变请求输入或重建key；核对daily已实现的overview props和children容器契约。未改概览、BoardBrowser、dashboard、marketview、renderer或其它在建源码；没有为了通过测试删除金融/权限断言。

## 保护、回退与剩余验证

修改前字节`logs/stock-screener-pages-20-backup/`及SHA `logs/stock-screener-pages-20-before.json`；本包两文件overlay `logs/stock-screener-pages-20-overlay-sha.json`；精确差异`logs/stock-screener-pages-20-final.patch`。overlay只声明本包hunk，不代替18正式全量基线或接受其它未知差异。回退按hunk；有后续修改时不得整体覆盖。

无DB迁移、无新增依赖、外部数据/模型费用0。两种已访问结果各保留一个有界实例，保持条件会使用少量客户端状态；未测试真实浏览器内存/滚动，不虚构性能收益或上线完成。

人工路径（未执行）：获准恢复当前源码后以股票身份进入行情筛选，默认看到多条件表单；应用条件并翻页，切基础搜索/翻页再切回，多条件草稿及已应用条件/页码应保持；各模式打开个股并返回，保留原模式、行业、条件与结果；手动刷新按原已应用条件恢复第一页，行业改变重建两种结果；检查紧凑来源折叠、详情时上部上下文隐藏和单外层纵向滚动。

下一轮3包：根整合页面职责/目标及全前端/TSC；获准当前源码构建发布与原服务/HTTP/本人验收；验收闭合后回到已存在正式任务/课程链路及可信数据，避免继续重复扩UI。
