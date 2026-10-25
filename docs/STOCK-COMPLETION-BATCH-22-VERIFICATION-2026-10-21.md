# B22 股票估值与披露历史真实交付

## 状态与边界

2026-10-21，北京时间。本批四包已实现并本机验证：独立只读历史接口、详情可操作分页入口、金融/权限回归及保护、实库只读和检查点。新切片通过目标、完整回归及真实隔离验证，实际证据见下。**全系统仍部分完成，未发布，未执行浏览器/人工双市场验收。** 不是历史补采、PIT认证或五年回测交付。

开工读取用户AGENTS、17日原计划、最新B21交付/主/期货保护计划和直接相关代码/模型/接口/调用方/测试；B21-final 996保护通过，原只读审查代理均已结束，无并发实施者。子计划`docs/superpowers/plans/2026-10-21-stock-completion-batch-22.md`顺序执行，不建分支/提交、无Git元数据，不覆盖既有工作。期货/共享认证/模型网关/布局/运行配置冻结。

## 问题与实现

原股票详情只有最新单日估值和最多8期财报；底层旧接口虽有分页，用户无法从详情浏览更早记录或控制公告截止。本批增加完整的股票独立历史读切片，不替换旧摘要或旧接口，也不改研究/模拟任务。

用户在个股“估值与财务”选择财务披露/每日估值、截止日期 → 显式读取历史（不自动请求） → 捕获当前token/用户/市场/盘/代码/日期的专用client → 原股票JWT门禁下新GET接口 → 独立PG可重复读只读事务内目录定位、分类计数、分页三次业务SQL → Zod校验标识/计数/日期/单位/分页/query回显 → 每页20条表格与上一/下一页。改参数、身份、市场、盘、证券或卸载立即隐藏并取消旧结果，晚到结果不覆盖。错误保留请求编号，不展示假空表；合法空页与源/DB失败区分。

## 实际文件

- 新`backend/app/schemas/stock_fundamentals.py`：ASCII六位代码、financial/valuation、必填ISO截止、limit1—100/offset0—100000、额外字段拒绝。
- 新`backend/app/services/stock_fundamentals.py`：`read_fundamentals(factory,query,*,today)`；标准证券目录/交易所核实，分类恒等式/稳定排序/有限值检查，单证券源行上限50000；PG核验READ ONLY和REPEATABLE READ，SQL5秒/锁2秒，不调用源或写库。
- 新`backend/app/api/stock_fundamentals.py`：`GET /api/stock/market/fundamentals`，12秒接口时限、身份/原父股票门禁、全路径no-store/request_id、已知404/409/422和脱敏503。`backend/app/api/stock_market.py`仅import/include该子router，既有details/boards/members/screen/gaps路径不改。
- 新`frontend/src/lib/stock-fundamentals-api.ts`：严格响应契约/两类行/标识和日期顺序唯一/单位/频率/日期语义/计数和分页恒等式/query回显；捕获token、20秒/取消/no-store、不重试，不显示任意后端异常正文。
- 新`frontend/src/components/market/stock-fundamentals-history.tsx`：显式筛选/读取/重试/前后页/负值与null区分、金融单位和来源局限；`frontend/src/components/market/stock-quote-detail.tsx`仅import和财务tab挂载。原单日估值/8期摘要和K线不改。
- 新`backend/tests/test_stock_fundamentals.py`、`backend/tests/test_stock_b22_preservation.py`；新`frontend/src/lib/stock-fundamentals-api.test.mjs`、`frontend/src/components/market/stock-fundamentals-history.test.mjs`；原`stock-quote-detail.test.mjs`新增child stub和实际挂载断言，原断言保留。
- 新`scripts/test-stock-completion-batch-22-postgres.py`、`scripts/test-stock-completion-batch-22-live-readonly.py`、`scripts/check-stock-b22-preservation.py`；既有batch-02 PG harness只加b22允许标签。新测试不把真实金融/PG验证替换成组件stub。
- 本文、B22子计划、主/期货保护计划检查点；`logs/b22-scope-compare-command.txt`保留范围/公表比较实际命令。没有新增依赖、数据库迁移或共享配置。

## 金融与数据口径

方法`stock-fundamentals-history-v1`。financial展示条件为publish非空、report≤publish≤as_of；同日公告仅用于展示，**未改变研究publish<决策日的安全口径**，不授权交易。分类互斥：source_rows=eligible_rows+unknown_publication+invalid_dates+later_rows；缺公告优先unknown，report>publish为invalid，剩余publish>as_of为later。valuation只有eligible/later，其余两类为0。后续或异常记录数量可见，不把排除等同不存在。

PE/PB等有限负值原样展示，不等于可用于有效价值筛选；ROE等有限负值合法。null保留缺失，UI显示“—”；非有限/非法数字整页409而不是改null、丢行或填0。亿元、元/股、元、百分数仅沿用既有模型声明，**没有声称逐行单位已认证**。不计算财务比率，不让模型推算历史数据。

响应明确供应商unknown、授权unverified、采集时间null、交易日历/财报口径unverified、单位schema_declared_not_row_verified、无数据版本、PIT=false；读取时间不是采集时间。季度/日频及报告期/公告日/交易日语义按类型返回。来源仅已存local_import/legacy_unverified；未请求Teajoin或替换渠道，不假造追溯证据。原表每报告期一行，无修订历史，因此按旧表公告日期筛选**不能重建历史真实可得时点**。固定as_of与稳定排序也不是不可变版本，后续修订可改变offset跨页；面板明确限制。

## 实际命令与结果

Python在仓库，Node/TSC在frontend，所有真实失败与通过日志保留。以下同类命令按red/green/最终多次执行，无安装/升级或生产启动。

```powershell
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b21-preservation.py verify logs/futures-preservation-b21-final.json
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b22-preservation.py snapshot logs/futures-preservation-b22.json
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_stock_fundamentals.py -q
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_stock_fundamentals.py backend/tests/test_stock_b22_preservation.py -q
node --import ../logs/node-alias-register.mjs --test src/lib/stock-fundamentals-api.test.mjs
node --import ../logs/node-alias-register.mjs --test src/components/market/stock-fundamentals-history.test.mjs src/components/market/stock-quote-detail.test.mjs
node --import ../logs/node-alias-register.mjs --test src/lib/stock-fundamentals-api.test.mjs src/components/market/stock-fundamentals-history.test.mjs src/components/market/stock-quote-detail.test.mjs
backend/.venv/Scripts/python.exe -X utf8 scripts/test-stock-completion-batch-22-postgres.py
backend/.venv/Scripts/python.exe -X utf8 scripts/test-stock-completion-batch-22-live-readonly.py
node --import ../logs/node-alias-register.mjs --test (Get-ChildItem -Recurse src -Filter *.test.mjs | ForEach-Object FullName)
node node_modules/typescript/bin/tsc --noEmit --incremental false
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests -q
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_stock_fundamentals.py::test_disclosure_classification_page_union_and_same_day_display -q
$program=Get-Content logs/b19-audit-command.txt -Raw
backend/.venv/Scripts/python.exe -X utf8 -c $program logs/b22-state-before.json
backend/.venv/Scripts/python.exe -X utf8 -c $program logs/b22-state-after.json
backend/.venv/Scripts/python.exe -X utf8 -c $program logs/b22-state-after-final.json
$program=Get-Content logs/b22-scope-compare-command.txt -Raw
backend/.venv/Scripts/python.exe -X utf8 -c $program
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b22-preservation.py verify logs/futures-preservation-b22.json
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b22-preservation.py snapshot logs/futures-preservation-b22-final.json
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b22-preservation.py verify logs/futures-preservation-b22-final.json
```

另在frontend执行真实响应契约验证（首次报告和最终报告各一次，路径不同）：

```powershell
node --import ../logs/node-alias-register.mjs --input-type=module -e 'import {readFileSync} from "node:fs"; import {stockFundamentalsSchema} from "./src/lib/stock-fundamentals-api.ts"; const report=JSON.parse(readFileSync("../logs/stock-completion-b22-live-readonly-4238c24a82764b88bf8ce892bf71103c.json","utf8")); if(report.status!=="passed" || report.results.length!==4) throw new Error("bad real report"); for (const item of report.results) stockFundamentalsSchema.parse(item.result); process.stdout.write(JSON.stringify({status:"passed",real_response_contracts:report.results.length,source_calls:0,model_calls:0}));'
```

### 红绿与失败

- `b22-backend-red.log`首次为测试fixture导入路径错误、collection error1；查看既有测试后改为`tests.test_stock_research_snapshots`。这是测试准备错误，不称有效业务red。随后`b22-backend-red-confirmed.log` **13失败**为新模块缺失，首次green含guard **14通过**。
- `b22-client-red.log` **4失败**为新client缺失；`b22-panel-red.log` **4失败/5通过**包含3个组件文件缺失及原详情实际未挂载断言失败。实现后`b22-panel-green.log` **13通过**，TSC退出0；不是浏览器画布验收。
- 自查AGENTS金融元数据后补supplier等显式未知字段：先加断言观察`b22-metadata-red.log` supplier KeyError **1失败**；client增加字段反例`b22-metadata-client-red.log` **3失败/1通过**。增补后最终目标后端`b22-backend-target-final.log` **14通过**、前端`b22-node-target-final.log` **13通过**。未放宽断言、吞错或重写旧接口。
- 最初探索两个猜测panel路径和backend/tests/conftest.py不存在，返回文件不存在；只读转查实际代码，不据此宣称验证过文件。未执行破坏性修复。

### 完整回归与集成

- 首次完整后端`b22-backend-full.log` **2715通过、3既有skip、740warnings，125.60秒**，发生在元数据增补前。增补后最终`b22-backend-final.log` **2715通过、3既有skip、736warnings，130.11秒，退出0**；完整前端/TSC结束后顺序执行后端，没有新WS失败，但不因此宣称前轮偶发时序风险已修复。
- 首次完整Node525/TSC0；增补后最终`b22-node-final.log` **525通过、0skip**，`b22-tsc-final.log` **退出0**。既有期货/共享测试保持，不删或新跳过测试；没有将旧warnings说成0或放宽断言。
- 最终私有PG/JWT/HTTP `stock-completion-b22-pg-20261020-231947.json` **57检查通过、schema清理true**（此前57也通过）。真实PG包含Infinity/null/负值、公告同日/未来/缺失/倒置分类、两页并集、JWT匿名401/期货409、unicode/extra/future422、未知证券404、非有限409、DB503/no-store/trace、公表金融/目录/账本/任务全行摘要不变。
- 最终真实行情只读`stock-completion-b22-live-readonly-4238c24a82764b88bf8ce892bf71103c.json` **4份响应通过**；`b22-real-client-contract-final.log`真实JSON经新前端Zod **4通过**。这是本地ASGI+真实PG，固定本地股票上下文，不是运行中服务/生产JWT认证；JWT由上述私有PG脚本验证。
- 独立只读审查及元数据增补后复核均未发现明确P1/P2。审查者未运行测试/数据库/网络或修改代码，不能代替主执行者实际命令。

## 真实结果与期货保护

受控实库仅自动选择一只已有财报/估值的目录证券 **000001.SZSE 平安银行**，截至 **2026-10-21**：已存财报35条、估值2130条，各读取offset0/2、每页2条，两页无重叠且计数一致。该样本unknown/invalid/later均0；不是全市场零问题、所有历史完整或独立真实性认证。没有抓21日未收盘行情。首次实库报告`4b6e87a87f2d420493060b97e6f0a78d`及最终报告均保留。

`b22-scope-and-state-comparison-final.json` **6项通过**：原B22保护1003文件零漂移；原B21-final仅stock_market.py、stock-quote-detail.tsx和其测试三处计划变化。对两既有生产文件逆去新增import/include/mount后字节SHA与B21-final一致，说明没有夹带原行情/期货/研究逻辑改动。不是拿新清单覆盖老差异。最终`futures-preservation-b22-final.json`同样1003零漂移，所有原清单保留。

三次public只读审计的账本/AI任务/目录/EOD/旧归档全行摘要相同，来源20日版本和5549观察未改；旧kline仅计数/日期/首尾20条样本摘要，不是完整历史数据认证。不存在的可选审计表明确exists=false，不推断其他表均已审计。

## 兼容、迁移、回退与成本

无DDL/迁移、无生产数据/账户/任务写。新接口及面板只读，旧details、financials、valuation、研究/模拟接口及摘要保留；新前端遇404明确“证券未收录或历史接口尚未部署”，不自动退到空成功。正常空页只有后端真实SQL和计数契约匹配才展示。

回退只移除本包router include/import与详情面板mount/import及新增独立文件；旧8期/单日摘要仍可使用，无数据库恢复/缓存清空/任务或资金补偿；不能恢复整文件覆盖其他轮改动。无依赖或运行配置变更，stock-market-api.ts / stock-api.ts和共享网络层未动。

来源调用、产品模型调用及public业务写均 **0**；私有PG测试表已清理且显式ID不消耗public序列。用户业务次数不限未恢复日/月门槛；保留单请求数量、超时、权限和网络边界。新增每次显式读取至多三业务查询，不后台自动翻页，不无限重试。监听只读见5433/3000/3001，8002/6380/8110无监听；不认定3000/3001属于本项目健康服务，未启停/抢占/强停。

## 未执行、人工路径及下一四包

生产构建/EPERM处理、服务部署、运行中HTTP浏览器、迁移基线发布回退和人工双市场验收未执行。没有真实外部源、付费AI或策略重算验证；旧WS偶发0.2秒超时仍为前轮记录的独立风险，本包不修改冻结共享/期货生产代码。全系统未完成，续做保留。

人工步骤（尚未执行）：部署兼容股票后端/前端 → 股票行情板块/股票详情或工作台 → “估值与财务” → 新“估值与披露历史查询”，选日期/类型点击读取，核对单位、负值/缺失、前后页及分类/来源说明；换证券/用户/市场/盘不显示旧结果；断网/旧后端错误不伪装空数据。期货页面没有此股票历史入口，原交易/资金/AI/研究仍需独立人工回归，不以源码hash代替功能验收。

下一四包：
1. 已存来源9缺/19未知原因及明确授权补采预算，不盲重导或伪造行情。
2. 历史PIT/复权锚点/旧导入版本、五年8931缺价及600221持仓缺价；不删持仓或填价凑成功。
3. 可信版本股票DSL/指数基准/同版本研究重算与回放扩池；受控AI仍依赖S04人工确认/写幂等和S05服务端context，敏感写继续关闭。
4. 独立运行/构建/迁移发布/前向修复回退和双市场人工验收；未通过不报告整体完成或删除续做。
