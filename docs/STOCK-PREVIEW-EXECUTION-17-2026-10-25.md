# 执行17：截图问题修复、真实日线缺口与当前源码构建

**总体状态：部分完成。** 四包源码已实现待真实运行验证；目标及联合回归、TSC和当前源码生产构建通过。当前五原端口无监听，未自动重启、发布新构建或应用迁移，不能称本人页面/完整课程已经验收。

执行边界先见`docs/STOCK-PREVIEW-FIX-17-2026-10-25.md`。先前执行被heartbeat打断，续做先核文件、备份/红绿证据和在建者，三个子agent按原独占范围完成，不重写已有实现。根负责共享样式扫描例外、整合测试和接受清单。

## 四个工作包与实际文件

1. 股票列表/图表：修改`stock-board-browser.tsx`、`stock-universe.tsx`、`stock-quote-detail.tsx`、`stock-kline-chart.tsx`和股票tick派生`echarts-candlestick.tsx`及四个直接测试。列表由外层承载纵向滚动，表格最小宽度和横向滚动保留，搜索弹窗至少20rem且可滚动；详情取消双层纵向裁剪，图表/副图按内容高度不收缩，tick蜡烛/量柱/MACD柱最大12px、主图至少240px。保持六卡桌面、指标/价量/涨跌参考与原市场隔离。真实ECharts SSR检查五根蜡烛几何与所有grid边界通过，但不是本人浏览器视觉验收。见`docs/STOCK-VISIBLE-FIX-17-2026-10-25.md`。
2. 日线读取：修改`backend/app/api/stock.py`、`backend/app/services/stock_eod.py`、`backend/tests/test_stock_kline_reference.py`、`frontend/src/lib/stock-kline-contract.ts`及测试。沿原证券/市场/日期/status/unknown过滤和取源优先级，增加可选coverage，数据库聚合所选本地源总数/首末日期，说明请求/实际窗口及是否读完本地来源，不认证交易日完整性。不混qfq和原价，不改250根EOD读取边界。客户端接受旧响应，但有coverage时严格核数量/日期/has_more/窗口，冲突不展示。股票详情已消费该字段。见`docs/STOCK-DAILY-FIX-17-2026-10-25.md`。
3. 研究/回测：修改股票专用`factor-research-workspace.tsx`及`factor-research-page.test.mjs`，原入口和计算核不动。参考tick的StrategyBacktest参数分区/费用交互，修复回测左目录点击未真正多选的断链；基点输入换算为本系统原比例费率，保留空/非法输入并阻止提交；当前任务参数可显式恢复，忙碌/不确定恢复时不能替换草稿，重新预检且不自动提交。不是复制tick的不同后端协议或假ML按钮。见`docs/STOCK-TICK-RESEARCH-17-2026-10-25.md`。
4. 共享构建例外：仅`frontend/src/app/globals.css`首行改为`@import "tailwindcss" source("../");`，新增`frontend/src/lib/build-source-scope.test.mjs`。全体股票/期货代码都位于src，不更改任何主题值、期货组件或金融逻辑。真实native扫描发现原前端根扫描9970个文件，9407个来自历史.next-*；新src根544个、历史产物0。原生PostCSS实际编译同时核期货/股票组件依赖和utility通过。此前内存失败不能等同业务泄漏；本次限定扫描后完整构建成功，也不伪称已做全机器内存峰值A/B性能认证。精确单行patch见`logs/stock-preview-fix-17-styles.patch`。

## 用户所说的日线资产确实存在，但尚未安全接入

前一执行者在原PG5433只读核实：605058澳弘电子旧`kline_history`为0，EOD/observations各5根（2026-10-16、19、20、21、23），服务limit5/250/3000均5根、has_more=false。旧qfq总585392行仅300只股票，不含此票。证据`logs/stock-daily-fix-17-db-before.json`，是代码修改前真实读取，不冒称本轮新coverage在原PG通过。

tick本地实际找到605058的263根Parquet日线，2025-08-11至2026-09-08，无重复日期，逐文件SHA保留在`logs/stock-daily-fix-17-tick-local.json`。当前代码契约为价格元/成交量手/金额元，daily与enriched复权不同。关键风险是原pipeline允许Sina快照写同daily表，canonical upsert不保留逐行source，视图硬编码source不能证明历史供应商。现有文件缺逐行供应商/采集时刻/原响应hash/授权边界和版本等；没有自动归因全为Teajoin、拼接5根新EOD、导入旧表或伪造缺失日期。供应商是否有更早数据尚未新增调用确认，新增数据/模型预算仍0。

## 命令、失败与验证结果

根在frontend执行：

```powershell
$testFiles = @(rg --files src -g '*.test.mjs')
node --experimental-strip-types --import ../logs/node-alias-register.mjs --test --test-concurrency=1 @testFiles
node node_modules/typescript/bin/tsc --noEmit --incremental false
```

**749通过、0失败/跳过，42.68秒**，`logs/stock-preview-fix-17-frontend.log`与同前缀`-frontend-test-files.log`。包括原期货展示HTML/市场分发、股票权限状态、真实SVG以及native样式扫描；不相加子包60/94/6等重叠计数。TSC exit0，`...-tsc.log`。

根在backend执行：

```text
.venv/Scripts/python.exe -X utf8 -m pytest tests/test_stock_kline_reference.py tests/test_stock_market_capability.py tests/test_stock_market_api.py tests/test_stock_eod.py tests/test_stock_research_api.py tests/test_stock_research_legacy_adapter.py -q
```

**69通过，36条既有utcnow警告，12.49秒**，`logs/stock-preview-fix-17-backend.log`。SQLite/ASGI及相关目标测试，不是原PG新代码/在线HTTP。

根进程局部设置BACKEND_URL=http://127.0.0.1:8002、WENYUAN_BUILD_DIR=.next-stock-visible-fix17、WENYUAN_PREVIEW_BUILD=1、RAYON_NUM_THREADS=2、NODE_OPTIONS=--max-old-space-size=2048，在frontend执行`node node_modules/next/dist/bin/next build`，**exit0**，编译27秒、40页生成通过，`logs/stock-preview-fix-17-build.log`。构建ID `OP0l397nX9AZc6vvK8JgH`，未部署。Next自动增改tsconfig include后，核对仅新增构建类型路径/排序副作用，按`...-tsconfig-before.bin`字节恢复，SHA仍05636e5b2998cb7ee216821014240e016a2fb8f4ceca4ef9c9a80b8ed2b42250；恢复后TSC通过。

红/失败证据全部保留：native扫描声明测试红→真实编译绿；可见修复初26中4红，中断后两处测试遍历假设修正并补20rem/coverage红例后60绿；研究旧日志28绿4红→32绿，旧日志未写命令不伪造历史调用；日线先缺coverage/func导入失败及一次不存在测试文件的0执行均在子包记录。没有删除/skip/放宽金融权限断言。

## 保护、运行与回退

主接受清单`logs/stock-preview-fix-17-accepted-inventory.json` **1179**，补充`...-accepted-supplemental-sha.json` **34**，verify通过。旧1178中16个声明文件变化，其余**1162不变**，新增1个native扫描测试，无删除。旧33补充全不变，加入既有后端日线测试保护。两清单有交集，不将计数相加。精确名单`...-preservation.json`；各子包原字节/patch/accepted SHA及root备份均保留。除已先设计验证的共享CSS source单行，期货生产与共享认证/网关/布局/运行配置、锁文件与依赖均未修改。

当前端口3001/8002/8110/5433/6380均无监听，见`logs/stock-preview-fix-17-runtime.json`，停止原因未知，未由历史启动许可推断在线或自动重启。浏览器工具两次初始化均返回kernel assets路径错误，未取得本人页面状态，不能宣称滚动视觉已验。未应用015–019、启动新worker、恢复付费AI或新增外部数据/模型调用。

回退只在核当前目标SHA后按各包patch撤增量，保留后续修改、旧历史结果和tick候选数据；共享CSS只退source声明，不全仓还原。部署时保留旧成功`.next-stock-ux-round3`及其原spec/PID作为前端回退；当前未替换正在运行的服务，无DB回滚。若原PG实际coverage查询有问题保留旧字段兼容、先停用新展示并独立修复，不删历史。

人工路径（待恢复许可）：本人股票页浏览列表末行/下一页、Ctrl+K弹窗，详情切所有副图滚至底部/缩窄窗口/放大Esc；605058显示本地5根与正确日期、不假称60根；旧qfq标的不混源；回测左目录多选→基点参数→覆盖预检→任务结果→显式恢复，不改变旧结果；另核期货原页面与权限。未执行本人、原PG新代码、在线HTTP、全课程五年/真实数据与发布回退验收。

下一四包：①恢复当前构建后原PG/HTTP/本人滚动与期货回归；②tick263根候选的不可变来源/许可和缺口对账，获显式预算才补采；③正式研究/ML任务兼容演进与结果分区；④可信数据接IC/可成交回测/基准/ML和完整报告端到端验收。没有以本轮UI/构建通过称整个系统或课程完成。
