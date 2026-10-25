# B23 A股模拟委托全历史真实交付

## 状态与原因

2026-10-21 08:06，北京时间。本轮四包已实现并本机验证：只读历史服务/API、真实前端筛选分页入口、PG/权限/金融回归与保护、公共状态审计及检查点。新切片通过目标、完整回归和真实隔离验证，证据见下；**全系统仍部分完成、未发布、未完成人工双市场验收**。

确认根因：`StockReplayService.view`只返回所有pending加最近200条历史，前端`ReplayOrders`再对这份数组slice分页；更早记录仍在数据库但无法从用户入口检索。不能只调前端页数或移除SQL上限返回全表。本批新增服务端分页，不改旧撮合/撤单/资金计算及原200表。

开工读AGENTS、最新B22交付、主/期货保护计划及直接相关API/模型/schema/service/engine/调用方/测试；B22-final 1003保护通过，没有并发实施者。旧路径Windows rg通配符曾报错，改用实际路径读取；未据报错假称审查完成。当前工作区未检出新子目录AGENTS。使用writing-plans/executing-plans/TDD；独立代理仅只读审查，无Git/分支/提交，不重启或强停其他服务。

## 实际修改文件和链路

- 新`backend/app/schemas/stock_replay_history.py`：expected_revision、状态/方向/股票/提交日期、limit1—100/offset0—100000的严格查询契约。
- 新`backend/app/services/stock_replay_history.py`：`read_order_history`只选本人且同盘账户；校验规则版本、快照绑定、账户revision、游标日期；独立PG可重复读只读事务内三业务SQL（账户、过滤计数、页），稳定created_at/id倒序、limit+1。金额Decimal字符串、订单状态/成交日期/字段校验，异常409不改0或空页。
- 新`backend/app/api/stock_replay_history.py`：router工厂复用原replay_service门禁和迁移开关；新GET `/api/stock/replay/accounts/{account_id}/order-history`，trace/no-store、12秒/脱敏错误。读取前释放仅鉴权/ready的原读事务，避免同请求占两连接；待写变更拒绝，不悄悄提交或丢弃写任务。
- 原`backend/app/api/stock_replay.py`只import/include新工厂，旧路由、service/schema/engine/模型/迁移不改。新`frontend/src/lib/stock-replay-history-api.ts`严格Zod、捕获token/mode/账户/revision/query回显、20秒取消/no-store/无自动重试，409要求刷新账户。
- 新`frontend/src/components/trading/stock-replay-order-history.tsx`：显式查询、状态/方向/主板代码/提交起止筛选、前后20条/正常空页/错误/重试；身份/市场/盘/账户/revision/查询变化或开始交易忙状态时隐藏并取消旧结果。价格/费用/现金变动/已实现盈亏原字符串展示，不用浮点重算账本。完成的账户仍能查历史，新面板只读，没有模型或交易按钮。
- 原`frontend/src/components/trading/stock-replay-workspace.tsx`只import并在委托页挂载新面板，原近期委托/撤单保留。`stock-replay-api.ts`、原状态/helper、所有期货/共享认证/网关/布局/配置冻结。
- 新`backend/tests/test_stock_replay_history.py`、`backend/tests/test_stock_b23_preservation.py`；新前端`stock-replay-history-api.test.mjs`、`stock-replay-order-history.test.mjs`（后者在components/trading）。测试实际转译原ReplaySession，验证已结束账户也挂载历史且原ReplayOrders还在，不仅搜索文案。
- 新`scripts/test-stock-completion-batch-23-postgres.py`、`scripts/check-stock-b23-preservation.py`、`scripts/audit-stock-replay-history-readonly.py`；原batch-02 PG harness仅加b23标签。子计划、本交付、主/保护计划和`logs/b23-scope-compare-command.txt`保存检查点。

用户选择股票模拟账户/委托页 → 显式筛选并读取 → 当前身份token/盘/账户revision绑定的股票client → 原股票JWT/回放enabled/迁移检查 → 结束旧只读鉴权事务 → 独立RR/READ ONLY查询 → 严格前端校验 → 真分页结果。推进/提交/撤单导致revision变化时409，不把新旧状态混为同一页、不自动执行重试交易。

## 金融、权限与成本边界

- 保留原`stock-replay-v1`：主板、百股、T+1、下一开盘、公司行动变化阻断、缺价拒绝等撮合规则，本批不改变税费/滑点/现金/持仓/幂等或资金口径。
- 新方法`stock-replay-order-history-v1`仅查询已存模拟委托。价格最多四位、现金/费用/盈亏两位Decimal字符串且有限/有界；价格正、费用非负，非成交不能伪有成交字段/费用，成交日必须严格晚于提交日且不晚于游标。null表示未成交等真实缺项，不填零。
- 本人/登录盘必须匹配；他人/另一盘/不存在账户统一404。原股票市场门禁仍生效，期货会话409。客户端模式参数只用于比对响应，服务端模式只认JWT，不接受query改盘。
- query日期不晚于回放游标，筛选股票必须属于账户绑定；expected_revision不是历史版本，也不允许读“某个旧revision”的状态。实际数据变化必须刷新账户，新页不会静默沿用新版本。
- 账户绑定快照ID如实返回dataset_bindings，不冒充内容hash或本次重验来源。响应列明内部模拟器、私有账户授权范围、CNY/股/元股/时区、事件频率、不复权、采集时间null/数据版本null、PIT=false及局限；不是券商账单、实盘收益或行情真实性认证。
- 无DDL/依赖/运行配置变更、无公共业务写、无来源/产品模型调用。私有schema测试内写入/撮合不等于生产写。次数不限不意味着取消单次limit、SQL5秒/锁2秒/总12秒/客户端20秒和权限/成本边界。偏移到10万时提示缩小提交日期或证券筛选，不后台全量拉取。

## 实际命令

Python在仓库，Node/TSC在frontend；同类命令按red/green/修复后多次执行，完整日志位于logs。

```powershell
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b22-preservation.py verify logs/futures-preservation-b22-final.json
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b23-preservation.py snapshot logs/futures-preservation-b23.json
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_stock_replay_history.py -q
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_stock_replay_history.py backend/tests/test_stock_b23_preservation.py -q
node --import ../logs/node-alias-register.mjs --test src/lib/stock-replay-history-api.test.mjs
node --import ../logs/node-alias-register.mjs --test src/components/trading/stock-replay-order-history.test.mjs src/lib/stock-replay-history-api.test.mjs
node node_modules/typescript/bin/tsc --noEmit --incremental false
backend/.venv/Scripts/python.exe -X utf8 scripts/test-stock-completion-batch-23-postgres.py
node --import ../logs/node-alias-register.mjs --test (Get-ChildItem -Recurse src -Filter *.test.mjs | ForEach-Object FullName)
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests -q
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_stock_replay_history.py backend/tests/test_stock_b23_preservation.py backend/tests/test_stock_replay_api.py backend/tests/test_stock_replay_service.py backend/tests/test_stock_replay_engine.py backend/tests/test_stock_replay_integration.py backend/tests/test_stock_replay_data.py -q
backend/.venv/Scripts/python.exe -X utf8 scripts/audit-stock-replay-history-readonly.py
$program=Get-Content logs/b19-audit-command.txt -Raw
backend/.venv/Scripts/python.exe -X utf8 -c $program logs/b23-state-before.json
backend/.venv/Scripts/python.exe -X utf8 -c $program logs/b23-state-after.json
backend/.venv/Scripts/python.exe -X utf8 -c $program logs/b23-state-after-final.json
$program=Get-Content logs/b23-scope-compare-command.txt -Raw
backend/.venv/Scripts/python.exe -X utf8 -c $program
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b23-preservation.py verify logs/futures-preservation-b23.json
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b23-preservation.py snapshot logs/futures-preservation-b23-final.json
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b23-preservation.py verify logs/futures-preservation-b23-final.json
```

真实PG/HTTP响应再经实际前端契约验证（首次报告也执行过同类命令）：

```powershell
node --import ../logs/node-alias-register.mjs --input-type=module -e 'import {readFileSync} from "node:fs"; import {replayOrderHistorySchema} from "./src/lib/stock-replay-history-api.ts"; const report=JSON.parse(readFileSync("../logs/b23-private-response-contracts-8d5d4c8de9984b2798bf7809c634ac05.json","utf8")); if(report.results.length!==4) throw new Error("bad private response fixture"); for(const result of report.results) replayOrderHistorySchema.parse(result); process.stdout.write(JSON.stringify({status:"passed",real_PG_HTTP_contracts:4,public_tables_unchanged:JSON.stringify(report.public_tables_before)===JSON.stringify(report.public_tables_after),scope:report.scope}));'
```

## 通过、失败、修复与未执行

- 后端red `b23-backend-red.log` **15失败**为新模块缺失；实现后含guard `b23-backend-green.log` **16通过、6既有datetime.utcnow警告**。有真实SQLite的210条全页并集、筛选、用户/盘/日期/revision/异常记录和不写库测试，不把模块缺失说成旧撮合公式错误。
- 前端client red **3失败**、panel red **4失败**（3个组件缺失及原ReplaySession实际未挂载断言），随后 **7通过**。TSC首次 **4处BigInt literal低于ES2020目标错误**，未升级全局target或关类型检查；改成有界金额字符串的符号/非零校验，不用Number复算；`b23-tsc-confirmation.log`退出0，目标Node7仍通过。
- 独立审查发现P2：新接口在依赖session持有JWT/ready读连接时，再借第二连接；池满时可饥饿。先加真实`pool_size=1/max_overflow=0/pool_timeout=0.2`，`b23-pool-red.log`实际单请求503/TimeoutError，PG失败报告`stock-completion-b23-pg-20261021-000246.json`保留并清理。
- 修复只在新router保存标量身份/盘、检查无待写变更、rollback释放原只读依赖连接，然后建立新只读RR事务；日志不访问已过期ORM。`b23-pool-green.log` **单连接单请求、5并发读取、连接归还0检查全通过**。独立复核确认P2关闭且无新增明确P1/P2；审查者只读代码/日志，未运行测试/数据库/网络或改文件。
- 修复后受影响后端/原回放集 `b23-backend-target-final.log` **92通过、16既有警告**。完整Node `b23-node-full.log` **532通过、0skip**，完整TSC退出0；连接修复仅后端，无前端后续代码变化。
- 首次完整后端在连接修复前 `b23-backend-full.log` **2731通过、3既有skip、741warnings，137.13秒**。修复后最终`b23-backend-final.log` **2731通过、3既有skip、741warnings，122.47秒，退出0**，未出现新WS失败；不把既有skip/warnings说成0，也不据此宣布前轮WS偶发时序问题已修复。
- 最终私有PG报告`stock-completion-b23-pg-20261021-000310.json` **68检查通过、schema清理true**（初轮65通过，后加3个单连接池验证）。包括真实JWT/HTTP本人/盘/匿名/期货/他人、旧feature flag、日期/额外字段/revision冲突、503脱敏、不改原账本，未跳过失败测试。
- `b23-real-client-contract-final.log` **4份真实PG/HTTP响应通过实际前端Zod**，public摘要比较true。响应为私有fixture，不是真实用户账户验收。

## 真撮合证据与公共状态保护

隔离PG沿用原HTTP接口真实创建初始10000元账户、买100股、推进下一开盘：现金 **8994.99**、收盘权益 **10094.99**、费用 **5.01**；再提交限价委托并撤单，冻结归零、现金不变。新历史检索保留实际成交价10.0000、费用5.01、现金变动-1005.01。另加 **210条私有拒单夹具**验证长历史，合计212条按100/100/12完整读取；**不是实际执行了212笔成交**。旧view仍200条且资金/权益不变，原pending/cancel入口不动。

`b23-scope-and-state-comparison.json` **6项通过**：原B22文件仅两个股票挂载文件改变，逆去新增import/include/mount后字节SHA完全一致；原B23 1012受保护文件未改或删除，仅新增受保护的只读audit脚本，原verify失败差异如实保留，不能称原清单完全零差异。最终扩展清单`futures-preservation-b23-final.json` **1013文件零漂移**，原清单未重写。

三次既有公表审计的原账本/AI任务/目录/EOD/旧归档全行摘要及源20日版本/5549观察不变；旧kline仅计数/日期/首尾20样本，不认证全历史。新只读聚合审计在本地PG5433核验RR/READ ONLY，记录现存 **3快照/13账户/30委托/33步骤**，两次表内容摘要相同。真实PG隔离演练也独立记录public回放/期货账本/任务前后全行摘要一致，未写public序列或用户业务数据。

真实用户私有接口**未执行**：没有用户会话授权凭据，不冒用已有13个账户、不为验证创建公共测试账户或资金；只读聚合不等于本人权限端到端验收。两份audit报告为`stock-replay-history-readonly-c1a61dadbad246e8888083ac83a670df.json`、`stock-replay-history-readonly-54dc352fc45242b68ee397e1aeb57f84.json`。

## 兼容、回退、运行与人工路径

无迁移/DDL；旧API/回放schema/数据和方法保留，新接口复用原enabled及版本检查。回退仅去本包import/include和面板挂载/新模块，旧200表、撤单和撮合仍可用；不要整文件回滚覆盖其他批次。无新增依赖或源/模型费用；CPU/PG增加仅显式有界查询，真实单连接测试已通过，不用取消并发/SQL边界换取成功。

服务/生产构建/EPERM/迁移基线/发布回退、运行中浏览器及人工双市场未验收。最新只读监听仅5433/3001；3000/8002/6380/8110无监听，未认定3001属于本项目健康服务，未启停/抢占/强停进程。**不能说用户现在打开原页面就已可用**，也不冒险启动会运行遗留DDL的生产入口。

人工步骤（待部署执行）：本人股票模拟账户 → 委托页 → “全部已存委托历史” → 选股票/方向/状态/提交日范围显式查询，翻页看更早记录；核对成交价、费用、现金变动，原下方近期表仍可撤单。另一窗口推进/撤单后旧revision查询应409并要求顶部刷新账户；切号/市场/盘不显示旧结果；完成账户仍可查；禁用回放则历史503。期货原界面/资金/下单/研究仍需单独人工回归，不以hash当运行认证。

## 剩余风险与下一四包

1. 来源9缺/19未知原因与明确授权补采预算；只用已有真数据，不盲重导或填价。
2. 历史/PIT/复权锚点、五年8931缺价/600221持仓缺价和可复算版本，不删样本凑成功。
3. 可信股票DSL/指数基准/研究重算及回放扩池；原3只主板快照范围没扩，S04真实人工确认/幂等与S05服务端context仍未验收，敏感AI写仍关闭。
4. 独立运行/构建/迁移发布/前向修复回退和人工双市场验收，优先解决实际服务未运行；旧WS偶发0.2秒时序风险另包，不夹带修改期货。全系统未完成，保留续做。
