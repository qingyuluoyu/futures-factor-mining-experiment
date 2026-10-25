# 第三轮：行情筛选打开个股后能回到原结果

**已实现待验证**。承接UI第二轮与全功能交付队列U3，本轮只补股票可见流程，不改期货、共享认证/网关/布局/运行配置、API、数据库或金融公式。先核对其他执行者（只有当前root），1143项修改前保护校验通过，登记白名单与字节备份后实施。

三包：基础筛选→个股→返回原行业/列表；多条件筛选→个股→返回原已应用条件/分页/结果；基础成分股空页恢复及分页边界。前两包复用同一个StockQuoteDetail和保留实例方式，没有重复建设行情或财务实现。

## 实际实现与数据流

修改`frontend/src/components/market/stock-screener-workspace.tsx`、`stock-screener-workspace.test.mjs`、`stock-board-browser.tsx`；新增同目录`stock-members-workflow.test.mjs`。AGENTS、原可见页面计划、课程总包及交付队列仅更新真实状态和指针。

原基础/多条件筛选选股均push到`/market?symbol=...`，原筛选卸载；详情返回按钮再push到`/market`，无法回到原筛选。现在选股在股票筛选页打开既有StockQuoteDetail，原main保留相同子组件类型/key，只隐藏并设置inert；点击“返回行情筛选”仅清除选中代码。原行业、覆盖展开、基础关键词/排序/分页及高级已应用条件/结果由原实例保留，不增加revision、不额外请求筛选。个股仍走原证券标识与行情响应校验、独立K线/财务加载、原错误恢复；不新造行情或改股票单位/复权/收益口径。

这次采用本页显式返回，不增加URL历史项；不承诺浏览器刷新后恢复选中的详情，也不把实例保留测试当作本人浏览器验收。现有`/market?symbol=...`直接入口及其它模块导航保留。

基础列表处于后页而数据变化变空时，原来会显示“51–50 / 20”，现在显示本页载入0条、明确当前页可能已变化，并提供手动返回第一页。保留原关键词/行业/排序，不自动扩大筛选或伪称全市场无股票。下一页按原API允许的offset≤10000约束禁用越界导航，说明缩小筛选及列表未完整展示；不是新增业务次数限制，也没有修改后端范围。旧请求abort/晚到响应不可覆盖新关键词的回归保持通过。

一次自审检查：详情挂载不替换筛选树；隐藏工作区不接受键盘操作；返回不改已应用查询；高级原会话隔离逻辑不变；原市场分发未改；大结果集不发offset10050；空页不自动回退为另一查询。没有触碰确定性金融计算、权限或持久结果。入口已接股票既有筛选组件，但原服务未运行，因此不能标整包已完成并验证。

## 实际命令与结果

前端命令工作目录`frontend`，Node别名沿用现有`logs/node-alias-register.mjs`。

```text
node --experimental-strip-types --import ../logs/node-alias-register.mjs --test src/components/market/stock-screener-workspace.test.mjs src/components/market/stock-members-workflow.test.mjs
node --experimental-strip-types --import ../logs/node-alias-register.mjs --test src/components/market/stock-screener-workspace.test.mjs src/components/market/stock-members-workflow.test.mjs src/components/market/stock-board-browser.test.mjs src/components/market/stock-screen-results.test.mjs src/components/market/stock-quote-detail.test.mjs
node --experimental-strip-types --import ../logs/node-alias-register.mjs --test <Get-ChildItem src -Recurse -Filter '*.test.mjs'得到的全部路径>
node node_modules/typescript/bin/tsc --noEmit
pnpm build
```

先红：10项中5失败，证明缺详情返回保留、空页倒置范围及越界下一页；日志`logs/stock-ux-round3-red.log`。实施后32目标通过，`stock-ux-round3-target.log`；**700全前端通过、0失败、0跳过**，`stock-ux-round3-frontend-all.log`；TSC **exit0**，`stock-ux-round3-tsc.log`。独立构建环境`WENYUAN_BUILD_DIR=.next-stock-ux-round3`、`WENYUAN_PREVIEW_BUILD=1`、`BACKEND_URL=http://127.0.0.1:8002`，`pnpm build` **exit0**，`stock-ux-round3-build.log`；编译阶段实际102秒，不据此宣称页面耗时改善。

测试为前端VM钩子/交互及既有服务契约回归；11000条分页测试为明确测试夹具，用200次翻页验证10000边界，不是实际股票目录。后端未改，本轮没有重跑后端用例。未执行实PG、原HTTP、本人浏览器、付费AI对话、迁移、真实金融样本、五年性能及发布验收。

保护命令在仓库根目录：

```text
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b33-preservation.py verify logs/stock-ux-round2-final-inventory.json
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b33-preservation.py snapshot logs/stock-ux-round3-final-inventory.json
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b33-preservation.py verify logs/stock-ux-round3-final-inventory.json
```

新清单**1144**。原1143中仅声明3股票文件变化，其他**1140旧SHA不变**，新增1股票测试；未删除文件。先独立逐项断言变更集合后生成新清单，没有把未知漂移重设基线。字节备份`logs/stock-ux-round3-backups.json`、精确差异`stock-ux-round3-changes.diff`、保护证据`stock-ux-round3-preservation.json`、最终guard日志`stock-ux-round3-final-guard.log`。Next自动增添/重排tsconfig include后已精确patch还原，逐字节匹配本轮备份。

只读监听清单`logs/stock-ux-round3-runtime.json`确认原3001/8002/8110/5433/6380均未监听；未重启服务、迁移015–018或恢复paused/user付费AI。新外部数据/模型调用0；无新增依赖及运行费用配置。本轮局部前端修改不需要数据迁移，旧接口/结果保留。回退是逐项核对当前SHA后恢复声明的3文件差异与对应新增测试，不整体覆盖用户修改、期货或历史数据。

## 人工路径及下一轮

运行许可和原服务恢复后：A股→行情筛选→行业/关键词及后页→打开股票→K线/财务→返回行情筛选，核对原条件/页码/结果/滚动与键盘焦点；多条件筛选应用价格范围后重复，确认不发额外筛选请求。模拟数据变化使后页为空，手动返回第一页；模拟详情失败仍可回原筛选。本人路径尚未执行，尤其滚动/焦点与实际图表加载仍待验收。

下一四包：①原股票筛选/tick/登录本人视觉交互（需运行许可，不自动重启）；②研究→历史→报告现有v1入口与失败恢复；③自定义因子精确版本接固定数据资格/IC与报告，保持未认证执行门禁；④解决真实Parquet引擎及有界数据任务断链后接可成交回测/基准/复现，再按原队列落实必做本地LightGBM。外部阻塞时推进安全切片，不重复审核或伪称全系统完整。
