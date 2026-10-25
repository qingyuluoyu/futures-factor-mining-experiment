# B12 A股研究依赖预检与真实输入加载交付

日期2026-10-20，北京时间12:32；续做a-12-10。目录`F:/111/因子回测/futures-factor-mining-experiment-main`。先读取B11真实交付、主计划、直接代码/接口/模型/测试，确认无其他执行者修改本轮文件；顺序执行四包，不提交/建分支。

**状态：部分完成。** 本轮依赖契约、实际读取、覆盖v2/前端和第二层验证已实现并通过，且安全重载本机服务；不是整个系统完成。五年回测从“无关面板超限”推进到真实计算，但因已有历史持仓缺价明确失败，未伪造曲线。R03完整资源/金融验收、D03历史版本、GP来源/前端和S04/P01仍未全部通过。

## 1. 根因及四包修改

旧coverage和snapshot无论选择哪个因子都读取/透视4估值+4财报字段。ret_20d只需close，却按全部辅助面板计费，300证券五年2302800格超过200万；这不是日/月次数限制，不能删掉安全预算解决。

### B12-1 确定性依赖契约

新增`backend/app/services/stock_factors/dependencies.py`、`backend/tests/test_stock_research_dependencies.py`。

- `resolve_dependencies`仅接受有界、非空、已知不重复的因子列表；非法列表、布尔或加权方式422，先于数据库读取。None保留旧全字段兼容。
- ret_20d/vol_20d仅close；PE、PB、turnover_20d分别引入对应估值列；ROE/毛利/营收/净利同比各自引入所需财务列；市值中性化或mktcap加权额外total_mv，不依赖模型猜列。
- 冻结类型和固定列顺序，共享实际面板格数函数；不改变行数/格数阈值、不删股票或日期、不补价格。

### B12-2 实际快照和异步调用方

修改`backend/app/services/stock_factors/snapshots.py`、`backend/app/services/stock_factors/engine.py`的面板构造、`backend/app/services/stock_research_process.py`。

- 未依赖估值/财报时完全不查询该表。依赖时SQL只读所需真实列；未请求字段在既有固定tuple形状中显式为null，且不透视、不进入因子；不是给真实缺失值补值。价量不造数。
- 财报仍使用原7字段frontier择期器，报告期/公告日期/重复及数值校验不放松；公告日不可用于当天信号。新面板所需列和旧full逐字段一致，市值依赖没有被省掉。
- 选择性manifest使用`stock-local-snapshot-v2-selective`、dependencies及实际行数/格数；指纹同时绑定该版本、字段和实际输入。相同输入的ret/vol可同hash；未用PB改变不影响纯价格输入，PB被选中时无穷仍失败。
- 无显式选择时旧v1的manifest字段及指纹字节序列不变；既有报告不改。旧同步接口仍原全量读取，R02没有因此完成。公式方法保持`stock-research-2026-10-19.v5`；没有更改收益、成本、样本、预热、复权或金融填值规则。

### B12-3 coverage-v2和前端闭环

修改`backend/app/services/stock_factors/coverage.py`、`backend/app/api/stock_research_jobs.py`；前端修改`lib/stock-research-coverage.ts`、`lib/stock-research-api.ts`、`components/factor-research/factor-research-workspace.tsx`、`research-coverage-panel.tsx`及对应coverage/page测试。

- 原GET coverage-v1契约不变。新增GET `/api/stock/research/coverage-v2`，必填有界重复factor_ids查询参数，附mktcap_neutral和weight；与实际读取共用plan/格数，保留no-store、request_id、认证和股票市场校验，不调用来源。
- availability新增`selective_coverage`；旧响应前端默认false，继续保守v1。只在明确true时请求v2，v2请求收到旧v1结构会拒绝，不在失败后静默更换口径；因此先热更新前端也不把未重载旧服务变成404死入口。
- 覆盖键绑定用户/token/盘scope、日期、tab、因子、市值中性化和权重；变化立即使旧预检失效，取消/拒绝晚到响应。恢复历史参数仍重新预检且不自动创建；统计展示所需列及格数，不把within_limits说成金融可运行。
- 更新`backend/app/services/stock_factors/METHOD.md`记录口径和真实失败边界。本轮不改行情布局、模拟资金、原期货交易或AI任务规则。

### B12-4 实际验证及容量观测

新增`scripts/test-stock-completion-batch-12-postgres.py`、`scripts/audit-stock-research-capacity-local.py`；B02共用探针仅加入b12报告名。新建本计划/交付记录并更新主计划。没有新增依赖、数据库迁移、真实.env修改或公共业务数据写入。

## 2. 先红后绿和独立复查

- `logs/b12-dependencies-red.log`13失败：缺依赖模块和snapshot关键字；实现后相关68通过。
- `logs/b12-coverage-red.log`6失败：预检未接同一依赖/无新路由；实现后相关79通过。
- `logs/b12-ui-red.log`：新query/契约及预检失效反例失败；实现后31通过。预处理测试现在等待新的容量预检，而非立即提交，不放宽传入参数断言。
- `logs/b12-capability-red.log`：旧后端仍被请求新v2；显式能力门控后32目标前端通过。新库客户端保持旧v1契约反例。
- `logs/b12-version-red.log`：修改reader版本不改变指纹的反例失败；将选择性版本同时绑定manifest/hash后通过，v1未改变。
- `logs/b12-targeted-last.log`命令误写不存在的`test_stock_research_jobs.py`，exit4/未执行测试；不是绿记录。查询真实文件名后改为job_service/jobs_api并真实运行。
- 独立只读复查：未发现具体P1/P2，独立纯依赖/前端/版本回归通过；没有访问数据库或来源。后续能力门控和版本绑定复核亦通过；未冒称原StockResearchWorker完整重启验收。

## 3. 实际命令和结果

仓库根目录：
```powershell
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_stock_research_dependencies.py backend/tests/test_stock_research_coverage.py backend/tests/test_stock_research_snapshots.py backend/tests/test_stock_financial_selection.py backend/tests/test_stock_research_job_service.py backend/tests/test_stock_research_jobs_api.py backend/tests/test_stock_research_worker_process.py -q
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests -q
backend/.venv/Scripts/python.exe -X utf8 scripts/test-stock-completion-batch-12-postgres.py
backend/.venv/Scripts/python.exe -X utf8 scripts/audit-stock-research-capacity-local.py
backend/.venv/Scripts/python.exe -X utf8 scripts/audit-product-readiness-local.py
./scripts/run-local.ps1 -TimeoutSeconds 60
```

frontend目录：
```powershell
node --import ../logs/node-alias-register.mjs --test (Get-ChildItem -Recurse src -Filter *.test.mjs | ForEach-Object FullName)
node node_modules/typescript/bin/tsc --noEmit --incremental false
```

- 最终受影响 **101 passed、44 warnings、exit0**：`logs/b12-targeted-confirmation.log`。
- 最终完整后端 **2600 passed、3既有skipped、704 warnings、exit0**，118.36秒：`logs/b12-backend-confirmation.log`。早期2599不是最后版本；既有utcnow/异步mock告警不假称消除。
- Node **452通过、0失败/skip**；TSC **exit0**：`logs/b12-node-first.log`、`logs/b12-tsc-first.log`。其后仅后端版本hash及探针/文档变化，无前端实现变化。
- 真实隔离PG **70检查通过、0失败、私有schema清理**：`logs/stock-completion-b12-pg-20261020-042803.json`、`logs/b12-pg-final.log`。v1/v2计数、JWT401/错市场409/非法依赖422、只读可重复读事务、所需面板与full和计算结果相等、财报公告日边界、市值依赖、真实任务claim/结果持久化/HTTP归属及错盘拒绝、坏字段分选、公共表/账本/资金不变均通过。
- HTTP为实际ASGI请求+真实JWT/PG，非浏览器验收。提交的worker健康状态是测试接入边界替身；核心认领/读取/金融计算/保存未mock。另实际调用`stock_research_process.calculate`，仅连接构造限定私有search_path、emit收集作为边界适配，验证mktcap参数和真实PG计算同full；**没有把这说成原StockResearchWorker生产子进程/崩溃重启测试**。

## 4. 五年真实只读容量结果和仍存在的阻断

最终`logs/stock-research-capacity-20261020-042521.json`、`logs/b12-capacity-confirmation.log`，独立进程、0来源/0模型/0公共业务写：

- 区间2021-10-16至2026-10-16；300证券、1212观测日、354669真实本地价格行、8931缺价格。旧全字段2302800格over_limit；只选ret_20d为363600格within_limits，辅助行0，没有截断日期或样本。
- 实际只读repeatable-read加载 **2.747秒**，进程峰值工作集 **284647424字节（约271.5MiB）**，是该次Windows进程观测值，不是生产容量上限或所有配置认证。金融方法/PIT/授权状态未改变。
- 随后真实回测 **失败：missing_held_price**，600221在 **2022-09-29** 无持仓估值价。0.252秒是早停失败耗时，**不能当作完整五年回测耗时**；没有保存假净值、删股票、填0或把未知缺口当停牌前填。R03完整五年结果仍阻塞，需追溯缺口和同基准真实版本。
- 纯价格因子不必因未使用财报而读取/审核全部财报；反之这不说明未加载字段健康。所选因子自身数据和现有金融错误继续严格拒绝。

## 5. 本机重载、迁移和回退

- 起初B11记录AI1 running；本轮12:27只读发现已为1 paused，本人未替用户改状态。新预检`logs/b12-safe-reload-preflight.log`核对六类任务无pending/running/queued/executing、catchup=false、GPflagfalse、public GP表0，safe_to_reload=true。
- 逐项核实托管backend PID6820、项目local_service.py命令及8002监听才停止该backend；未停止PG/Redis/bridge/frontend或执行数据迁移。`logs/b12-local-reload.log`五服务就绪，新backend PID13760，live/virtual健康字段均true；这只是服务状态，不是来源认证。
- `logs/b12-runtime-http.log`health/login/market/factor-research/history200，匿名coverage-v2和GP-v2都401，OpenAPI确认两新路由已加载。**B11现在路由已部署但GPflag仍false/public008009未应用，不能据此解锁GP任务。** 未创建公共测试账户/任务或冒用真实用户JWT。
- 全局数据最近只读快照`logs/full-code-audit-snapshot-20261020-042551.json`仍19日5545/5558、13缺/19未知；旧研究300到16日、回放3、影子两日。没有抓20日未收盘收盘价或启动漏日补采。
- 无数据库结构/数据迁移。回退可恢复旧reader/API/前端：旧客户端v1兼容，旧后端未声明能力时新UI自动保守v1；已保存v2报告只读保留，不重写为v1。不同数据指纹仅并排报告，不假算差值。正常回退需先让研究任务空闲/停止确认，再重载；不恢复旧GP错误推进或_init_tables启动DDL。
- 本轮新增探针/测试上游和业务付费模型调用0，CPU/内存实测如上；不代表既有常驻行情流量期间没有其正常成本。不恢复日/月业务次数限制，不绕过TPM/RPM。
- 发布构建（含既有Windows standalone EPERM）、本人登录浏览器/视觉/交易验收、原研究worker实际子进程全链及系统级重启容量演练 **未执行**。不绕过既有浏览器安全限制，不用HTTP200代替人工验收。

## 6. 人工路径及下一四包

本人登录→切A股→因子研究：选ret_20d、五年日期，检查预检列为close、无估值/财报依赖；打开市值中性化或市值加权后应加入total_mv且按钮等待新预检；从历史填入配置不自动执行。实际提交五年可能返回上述真实缺价失败，应显示明确原因而非净值；切期货时不复用股票任务/账户。登录后GP仍应明确未开放，不能把匿名401理解为可挖掘。

下一四包：
1. **缺价及历史时点证据：** 从600221/2022-09-29和8931格清单做有界只读分类，核对授权Teajoin可用历史/停牌/上市及版本基准；没有同基准证据不前填、不混raw/qfq、不盲重导，先保留失败证据。
2. **R03/D03完整研究：** 因子预热/预测周期样本预检、混合依赖容量、版本reader复算和原worker真实子进程/重启；取得完整数据后再验收五年完整结果、基准及回放扩池，不能仅用本轮容量通过打勾。
3. **GP后续：** 可信期货固定输入、来源/具体合约日历及使用授权核验→v2前端，补OS强杀/孤儿进程和峰值内存演练；008/009仍只隔离验证，不能盲开flag。
4. **AI/发布安全：** S04持久人工确认及写幂等、S05上下文、R02旧消费者、P01完整版本迁移/构建回退；股票AI/消息按主计划依赖推进。自然语言仍只能生成校验草稿，不能模型自行确认交易。

全系统尚未验收完成，保留所有续做自动化。
