# 执行18：明确选源、页面分工与配置恢复

**总体状态：部分完成。** 承接用户最新页面重复/K线数据要求，四包源码与上下游已接通并通过离线回归、TSC及当前源码构建；新版本未发布，原PG/HTTP/本人/发布回退未验收。不是全市场历史、完整课程或ML产品完成。

执行前设计及共享路由兼容例外见`docs/superpowers/plans/2026-10-25-stock-pages-data-repair.md`。三子agent独占P1/P2/P3，根独占历史恢复类型与整合；所有源/模型预算0、原服务停，不提交/建分支/迁移/重启。

## 四个实际业务包

### P1 K线不再隐式读错来源

修改股票`backend/app/api/stock.py`及日线接口测试；前端`stock-api.ts`、`stock-kline-contract.ts`及测试、`stock-quote-detail.tsx`及测试。新增`GET /api/stock/kline/sources?code=…`，返回固定catalog_version、标准代码、两个物理来源history_qfq/eod_raw的本地数量/首末日期/价格口径/可用原因与读取cap；默认模式仍legacy_auto，不将其冒充第三个物理源。EOD关闭时数量null、不开表读取，不能用source参数绕过开关或市场权限。

旧`/kline`不传source继续原历史优先语义；显式history_qfq/eod_raw请求不可用即明确错误，不自动换源。响应selected_source必须与请求匹配，客户端严格核代码/来源/口径/覆盖和窗口。新详情先取目录，按最新可用日期选择明确来源（同日raw优先），允许手动切换历史前复权；切股/切源取消旧请求，晚响应不回写，显示真实来源和日期，不拼接raw/qfq。旧后端目录404/405明确显示旧模式并可重试；若已经选过明确来源，目录消失必须用户显式返回旧模式。503、格式冲突、目录空不伪装成功降级。

每次显式加载多一次本地目录GET/最多两项DB聚合；仍12秒后端/20秒前端边界，真实PG延迟待验。金融公式、价量、250 EOD/3000历史cap不变。tick263根仍未认证候选，本轮没有补采、导入、构造历史可得时间或声称历史完整。完整契约及红绿见`docs/STOCK-SOURCES-EXECUTION-18-2026-10-25.md`。

### P2 因子三页真正分工

修改股票`stock-research-entry.tsx`、`factor-research-workspace.tsx`及直接测试，新增`stock-factor-library.tsx`和测试；两个路由只在股票JSX末尾增加view属性。实验室view=library展示真实内置公式/方向/方法/数据要求和现有自定义版本编辑；研究view=analysis只显示检验，回测view=backtest只显示组合。原Workspace不传view仍保留混合兼容，原Entry initialTab仍可作为默认role。

专页的其它kind最近任务保留全局状态/对应入口，但不自动回填错页参数、不展示另一种报告或切错页。模型历史仅在analysis显式打开，未开放假训练按钮。Entry身份stamp/key包含view，类型切换不残留旧模型/表单。未改统一`use-research-task.ts`持久槽、活动任务/未知提交/幂等保护，未复制计算核。参考`docs/STOCK-RESEARCH-PAGES-18-2026-10-25.md`。

### P3 交易与三账本不再是同一整页

修改股票`stock-replay-workspace.tsx`与原历史测试，新增`stock-replay-pages.test.mjs`。交易保留K线/下单/回放推进与近期委托；持仓、订单、资产直接呈现对应账本，保持账户选择/创建/摘要，不挂完整交易图表/推进/下单表单。四页导航用真实路由Link，initialTab直接作为页面角色，属性变化立即反映；忙碌/未确认重试时本页新增导航禁用。

11个原读取/写入/校验函数AST原文保持，T+1、费用、账户记忆、金额合法性、原键重试及用户/JWT盘/市场隔离不变。共享侧栏离页后的旧未确认写入恢复仍是既有风险，未借此声称跨页写日志已持久化，需独立金融任务包处理。详`docs/STOCK-REPLAY-PAGES-18-2026-10-25.md`。

### P4 历史恢复不把回测塞进因子页

根修改`stock-research-history.tsx`、`research-restore-panel.tsx`及两项直接测试。历史回测恢复进入`/backtest?restore=…`，检验仍进入`/factor-research?restore=…`。RestorePanel新增可选expectedKind，错类型显示正确入口、不渲染应用按钮；旧事件处理器再次检查latest.expectedKind，不能在页面用途改变后回填。未传参数仍保留旧混合调用兼容，匹配回测原比例费率精确保留，应用配置不创建任务。

根先红3项（错类型可应用、旧handler越页应用、恢复链接路由错误），实现后40通过；补反向回测/费率正例后最终41通过。证据`logs/stock-pages-18-restore-red.log`、`...-restore-accepted.log`；根4文件原字节及SHA在`...-root-backups.json`，精确patch `...-root-changes.patch`。

## 实际验证与失败记录

frontend目录实际命令：

```powershell
$testFiles = @(rg --files src -g '*.test.mjs')
node --experimental-strip-types --import ../logs/node-alias-register.mjs --test --test-concurrency=1 @testFiles
node node_modules/typescript/bin/tsc --noEmit --incremental false
```

**778通过、0失败/跳过，41.80秒**，`logs/stock-pages-18-frontend.log`，实际文件列表`...-frontend-test-files.log`；TSC exit0，`...-tsc.log`。全前端包含原期货分发/骨架/身份、股票source协议、页面分工、账本私有状态及native Tailwind/真实ECharts SSR回归。子包33后端/30前端、130研究目标及最后key6、23回放、41恢复均与联合有重叠，不相加为独立总量。

backend目录：

```text
.venv/Scripts/python.exe -X utf8 -m pytest tests/test_stock_kline_reference.py tests/test_stock_market_capability.py tests/test_stock_market_api.py tests/test_stock_eod.py tests/test_stock_eod_integration.py tests/test_stock_research_api.py tests/test_stock_research_legacy_adapter.py -q
```

**76通过、36条既有utcnow警告，10.16秒**，`logs/stock-pages-18-backend.log`。真实SQLite注入factory与ASGI请求/权限/故障路径，不当原PG或部署HTTP。

进程局部BACKEND_URL=http://127.0.0.1:8002、WENYUAN_BUILD_DIR=.next-stock-pages18、WENYUAN_PREVIEW_BUILD=1、RAYON_NUM_THREADS=2、NODE_OPTIONS=--max-old-space-size=2048，在frontend执行`node node_modules/next/dist/bin/next build`，**exit0，编译19秒、40页生成成功**，`logs/stock-pages-18-build.log`；ID `-1FAJotsborAp9NRUMnmd`，未部署。Next添加/重排tsconfig include后只撤生成副作用，按`...-tsconfig-before.bin`逐字节恢复，SHA仍05636e5b2998cb7ee216821014240e016a2fb8f4ceca4ef9c9a80b8ed2b42250，差异留在`...-tsconfig-generated.patch`。

各包红/失败保留：P1缺目录/明确源/回执与前端入口先红后绿；P2页面仍双tab/跨kind回填/缺library先红，旧字符串断言曾因view属性位置失败，仅调整股票JSX新增属性到末尾后原市场测试通过，未修改原断言；P3先红3/7，历史测试只去掉已删除local tab状态槽假设，保留金融断言。根恢复红例见上，不删除/skip/放宽身份金融校验。一次合并自审核来源选择/开关/旧后端兼容、跨kind恢复、真实导航、旧任务幂等和金融函数边界，没有扩展期货。

## 兼容、保护与未执行

两共享路由factor-lab/backtest逐字节验证：只删除本次单个股票view属性后，整个文件与原备份完全一致，期货表达式/函数体/导入均不变。原统一任务hook SHA与17一致。证据`logs/stock-pages-18-compatibility.json`。其余共享认证/网关/布局/运行配置/依赖/锁文件不改，17的Tailwind source修复保留。

新主接受清单`logs/stock-pages-18-accepted-inventory.json` **1182**，补充`...-accepted-supplemental-sha.json` **34**，verify通过。相对17的1179仅18个声明项变化，其余**1161不变**，新增3个股票组件/测试，无删除；补充仅既有日线测试声明变化，另33不变。准确名单及字段在`...-preservation.json`。各包备份/SHA/精确patch齐全，根核实所有冻结SHA一致，旧清单不覆盖。

五原端口3001/8002/8110/5433/6380仍无监听，`logs/stock-pages-18-runtime.json`。未因heartbeat重启PG/Redis/前后端/connector/worker、应用015–019、恢复paused/user AI或调用任何新增金融数据/模型。未做本人视觉、原PGsource聚合/性能、新HTTP、完整五年/全市场/ML任务及发布回退验收。新增费用0，无新依赖/迁移/金融方法版本变化。

本轮未处理工作台与行情/筛选的剩余职责重叠，未把AI未接通门禁改成假功能，未接正式自定义/ML有界业务任务。候选263历史来源/采集/授权缺口依然存在，不能以选源目录当作补齐真实数据。

## 人工路径、回退与下一四包

明确获准运行后部署新构建，依次验：有两源证券默认最新日期→切历史qfq→回raw，日期/口径清楚且无拼接；605058仍仅真实5根；关闭EOD不能越界读取；实验室/研究/回测内容各自独立；跨kind最近任务不串草稿，历史回测恢复进入回测；交易有图/下单/推进，其余三页只有账本，切账户不残留旧私有内容；核原期货分发和旧接口。异常/身份/晚响应按子包路径验证。

回退先核目标SHA，按各独占patch撤增量而非整个文件恢复；保留用户后续编辑/旧任务/账户/结果/数据。新目录/参数/expectedKind可回退，旧接口默认兼容；两个共享路由仅撤股票view属性，期货不变。新源码尚未发布，不伪称验证过运行回退。部署保留17及旧成功构建，不DROP数据。

下一四包：①获准恢复原服务后的PG/HTTP/本人与期货发布回归；②工作台/行情/筛选职责及原账户未确认写跨页恢复；③tick候选来源/授权/修订/单位对账和显式预算下补采；④原016任务兼容演进，正式固定数据/自定义IC/可成交回测/基准/ML/报告完整链路。仅上述核心及金融权限、发布/本人验收全过后才能称系统完成。
