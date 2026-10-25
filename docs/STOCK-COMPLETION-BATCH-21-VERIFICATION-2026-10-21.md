# B21 股票研究输入解释真实交付

## 完成边界

2026-10-21，北京时间。四包实现完成，股票独立入口、只读接口、真实隔离PG/HTTP和现有真实库查询已验证；完整回归最终结果见下。**系统仍部分完成、未发布、未完成人工双市场验收。** 本批不是因子引擎重写，不是缺口补采或五年回测成功。

开工读取用户AGENTS、期货保护计划、主计划、B20检查点及直接相关模型/接口/调用方/测试，先验证B20-final 990保护；没有并发实施者。按B21四包顺序执行，独立代理仅只读复核，不写文件、不运行数据库或网络。期货及共享认证/网关/布局/配置保持冻结。

## 已解决的问题

原coverage-v1/v2主要解释容量和输入表数量，不能说明当前所选PE、ROE等是否有金融域有效源值。用户只在提交后才遇到缺字段/窗口等失败。本批补充显式点击的候选输入诊断，不替代worker最终校验，不把诊断结果用作绕过提交权限或金融检查的许可。

数据流：股票研究/回测工作区当前日期、因子和市值依赖 → 显式点击 → 捕获当前token/scope的股票专用client → 原股票JWT门禁下的新GET接口 → 同一PG可重复读只读事务内最多四条业务聚合 → Schema/数量分区/query回显验证 → 展示原因及来源局限。修改日期/因子/身份/登录盘或卸载，会取消请求并隐藏旧结果；不自动重试或提交任务。

## 实际修改文件

- 新`backend/app/schemas/stock_research_inputs.py`：严格ISO日期、1—9个已知不重复因子、单标识最多40字符、权重枚举、额外参数拒绝、区间最多3660天。
- 新`backend/app/services/stock_research_inputs.py`：股票价格/异常/重复、同证券同日价格交集内估值、结束日前候选披露记录聚合；有界数量、只读/可重复读核验、SQL/锁超时，不写数据或调用来源。
- 新`backend/app/api/stock_research_inputs.py`：GET `/api/stock/research/inputs`、12秒总时限、全路径no-store、request_id、脱敏错误503；`backend/app/api/stock_research_jobs.py`仅import/include新股票子router。原提交、coverage及worker契约未改。
- 新`frontend/src/lib/stock-research-inputs-api.ts`：严格Zod契约、因子依赖、字段单位/规则、计数分区、状态和请求回显交叉检查；20秒/取消/no-store/无重试，旧后端404明确提示未部署，错误不返回零数组。
- 新`frontend/src/components/factor-research/stock-research-input-check.tsx`：显式按钮、loading/结果/错误/重试、晚到结果隔离、候选值计数与局限；`factor-research-workspace.tsx`只import/挂载该面板，不改任务执行。
- 新后端`test_stock_research_inputs.py`、`test_stock_b21_preservation.py`，新前端`stock-research-inputs-api.test.mjs`、`stock-research-input-check.test.mjs`。原`factor-research-page.test.mjs`只新增子组件stub；单独新测试执行真实面板逻辑，没有以stub代替金融/PG验证。
- 新`scripts/test-stock-completion-batch-21-postgres.py`、`scripts/test-stock-completion-batch-21-live-readonly.py`、`scripts/check-stock-b21-preservation.py`。原batch-02 PG harness只新增b21允许标签。
- 子计划`docs/superpowers/plans/2026-10-21-stock-completion-batch-21.md`、本交付、主计划与期货保护计划检查点。`logs/b21-scope-compare-command.txt`保存可复核的范围/状态比较命令。

上述前端完整路径位于`frontend/src/components/factor-research/`及`frontend/src/lib/`，后端测试位于`backend/tests/`。无依赖升级、DDL、生产数据重导、模型调用或账户写。

## 金融口径与非目标

- 方法`stock-research-inputs-v1`。PE/PB/总市值只接受正有限值；ROE、毛利率、增长率允许有限负数。null、非有限、非正排除分别计数，不填0或1。
- 估值必须有同股票、同日期的正有限close；不是从其他市场或不相交日期凑记录。总市值仅中性化/市值权重需要，单位沿用现有模型`100_million_CNY`，百分比字段沿用percent。
- 财报要求report_date≤publish_date、publish_date<结束日、证券属于区间有效股票池。可早于开始日的公告进入候选集合；**没有重放每个历史日期的报告frontier、后修订/PIT可得时间或预处理后的有效面板**。
- 动量/波动21个、换手20个观测日只是必要条件，不是每只股票连续窗口认证；不检查策略horizon、groups、中性化样本或持仓缺价的全部条件。
- 状态只有invalid_inputs / missing_inputs / potential_inputs。potential表示存在候选，不是全池完整、因子重算成功、研究/回测成功、数据真实性或PIT认证。`point_in_time_certified=false`明确返回。
- 返回读取时间/方法版本，不把聚合计数冒称固定不可变输入数据版本。与旧容量预检分开读取，不承诺两个查询同一快照。原金融公式/版本和历史结果不变。

## 实际命令

Python在仓库运行，Node/TSC在frontend运行；日志位于logs。没有安装依赖、启停服务、提交或创建分支。

```powershell
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b20-preservation.py verify logs/futures-preservation-b20-final.json
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b21-preservation.py snapshot logs/futures-preservation-b21.json
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_stock_research_inputs.py -q
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_stock_research_inputs.py backend/tests/test_stock_b21_preservation.py -q
node --import ../logs/node-alias-register.mjs --test src/lib/stock-research-inputs-api.test.mjs
node --import ../logs/node-alias-register.mjs --test src/components/factor-research/stock-research-input-check.test.mjs src/lib/stock-research-inputs-api.test.mjs
node --import ../logs/node-alias-register.mjs --test src/components/factor-research/factor-research-page.test.mjs
backend/.venv/Scripts/python.exe -X utf8 scripts/test-stock-completion-batch-21-postgres.py
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests -q
node --import ../logs/node-alias-register.mjs --test (Get-ChildItem -Recurse src -Filter *.test.mjs | ForEach-Object FullName)
node node_modules/typescript/bin/tsc --noEmit --incremental false
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_ws_subscriber_recovery.py -q
backend/.venv/Scripts/python.exe -X utf8 scripts/test-stock-completion-batch-21-live-readonly.py
$program=Get-Content logs/b19-audit-command.txt -Raw
backend/.venv/Scripts/python.exe -X utf8 -c $program logs/b21-state-before.json
backend/.venv/Scripts/python.exe -X utf8 -c $program logs/b21-state-after.json
backend/.venv/Scripts/python.exe -X utf8 -c $program logs/b21-state-after-final.json
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b21-preservation.py verify logs/futures-preservation-b21.json
$program=Get-Content logs/b21-scope-compare-command.txt -Raw
backend/.venv/Scripts/python.exe -X utf8 -c $program
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b21-preservation.py snapshot logs/futures-preservation-b21-final.json
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b21-preservation.py verify logs/futures-preservation-b21-final.json
```

## 通过、失败与未执行

- Red：`b21-inputs-red.log`10失败、`b21-client-red.log`3失败为新模块缺失；`b21-panel-red.log`4失败包含组件缺失及实际工作区未挂载反例，不冒称所有都是旧金融断言。对应首次green为10、3、7通过。
- 旧工作区测试首次因新增child import没有stub失败，保留`b21-workspace-harness-red.log`；只加stub不删断言，完整Node回归随后通过。一次apply_patch因原行上下文不匹配整体失败，核实没有部分写入后修正路径上下文重做，不覆写其他代码。
- 自查因子单字符串长度原无显式上限，先加预期`string_too_long`反例实际失败（`b21-factor-length-red.log`），后补40字符边界。最终目标`b21-target-confirmation.log` **12通过、1既有datetime.utcnow警告**。
- 最终前端`b21-node-confirmation.log` **517通过、0跳过**，`b21-tsc-confirmation.log`退出0。包括真实client/面板测试及现有期货/共享回归；并非真实浏览器画布或端到端部署验收。
- 首次完整后端`b21-backend-full.log`2700通过/3既有skip/738warnings（新增长度测试前）。第二轮`b21-backend-confirmation.log` **2700通过、1失败、3既有skip、736warnings**：旧共享WebSocket recovery测试的0.2秒等待超时，非B21金融断言。未改冻结生产/测试、不增加超时、不跳过该用例；只读检查栈与fixture后单独重跑`b21-ws-recheck.log` **24通过**，保留失败证据，不能断言已修复旧偶发时序问题。
- 最终顺序全后端`b21-backend-final-sequential.log` **2701通过、3既有skip、735warnings，124.87秒，退出0**。没有并发其他重验证时本次全过，但不能因此消除前轮WS时序风险；未修改或跳过任何旧测试，不以最终green掩盖保留的失败。
- 私有PG最终`stock-completion-b21-pg-20261020-222710.json` **53检查通过、schema清理true**；真实JWT股票/匿名/期货门禁、私有PG实际Infinity/null/非正/公告边界、重复坏价、503/no-store/trace、公表账本/任务不变。首次PG53同样通过，最终复验包含长度Schema修复后的代码。
- 独立只读审查未发现明确P1/P2；审查者未执行测试/DB/网络，只读取代码及既有PG报告；此处运行证据由主执行者实际执行。
- 查询过一次不存在的`scripts/check-stock-b15-preservation.py`返回路径不存在；随后检查实际存在的`check-futures-preservation.py`，没有据此伪报通过。当前工作区子树未检出额外AGENTS文件。
- 未执行生产构建、服务部署/重启、生产JWT浏览器访问、人工页面/双市场发布回退验收、付费模型/数据源、历史补采或完整策略重算。只读监听看到PG5433和3000/3001，不认定后两端口是本项目健康服务；8002/6380/8110当次未监听。未抢占或强停进程。

## 实际行情只读结果与保护

`stock-completion-b21-live-readonly-fcdd8b3ec4ca4e40ba6047ff5ccc9d0c.json`：本机ASGI真实已存库，固定本地股票上下文覆盖，不是生产JWT或运行中HTTP服务。选择最新61观测日 **2026-07-16—2026-10-16**、九因子及市值中性化，结果 **potential_inputs**。18266价格行、300证券、61日，坏收盘/重复组0。

估值交集6100行：PE可用5850/缺250，PB、换手、总市值可用6100；财报候选10324条：ROE可用10303/缺21，毛利率8616/缺1708，营收增长10137/缺187，利润增长10139/缺185，非有限0。这是不同语义的源记录，**不是10324个日期×股票因子格，更不证明300只均有每个因子**；本区间不能声称ROE完全没有数据或所有研究必然成功。

`b21-state-before.json`、`b21-state-after.json`、`b21-state-after-final.json`及`b21-scope-and-state-comparison-final.json`6项检查通过：期货/股票原账本、AI任务、目录、EOD和旧归档全行摘要不变，20日source版本与5549观察未改；旧kline仅计数/日期/首尾20行样本摘要核对，不冒充全历史审计。可选不存在的审计表明确exists=false，不据此认证其他表。

原B21 996清单verify如实报告**仅原父工作区Node测试一处变化**；去掉新增的唯一stub后字节SHA与原清单完全相同，995其余受保护文件未漂移。原B20-final 990核对仅股票router、股票workspace及上述测试三处计划改动；B20七个允许股票生产文件在B21均已纳入保护且没有改动。保留原清单及预期失败记录，不重写原证据。最终`futures-preservation-b21-final.json` **996文件零漂移**，不把新基线说成所有原文件从未变化或期货全部功能认证。

## 兼容、迁移、成本与人工路径

无DDL/数据库迁移；原coverage、旧任务参数/提交/worker不变，认证和网关不改。新前端遇旧后端明确404未部署；新接口可与旧前端共存。回退撤下新面板/import和新子router/include即可，不涉及数据库/任务/资金补偿；不能用恢复整文件覆盖其他轮改动。无新增依赖；来源、产品模型及公共业务写均0，私有PG测试schema已清理。每次诊断最多四业务聚合、单SQL5秒/锁2秒/总12秒，仍遵守已有登录与系统网络边界；并非取消外部成本或系统并发保护。

人工验收（待部署后执行）：股票模式进入因子研究/历史回测，设当前有历史覆盖的区间及PE/ROE，点击“检查当前因子输入”，核对loading、候选表及局限；切换日期/因子/市值中性化/权重、账户或盘后旧结果立即消失；断网/旧后端应显示错误/未部署而非0成功；请求ID可追踪。确认期货入口没有该股票面板，原期货研究/交易/资金仍走原代码。此步骤**尚未实际浏览器或人工执行**。

## 剩余风险和下一四包

1. 9缺/19未知仍待来源原因和授权补采预算核实；只读诊断不修复来源。当前时间未到21日收盘，未抓21日收盘。
2. 历史版本/PIT/复权锚点/历史证券池与五年8931缺价、600221持仓缺价，需兼容数据资格和前向修复，不造值或删样本凑成功。
3. 可信版本股票DSL、指数基准、研究重算和回放扩池，再推进受控股票AI。S04持久人工确认/幂等、S05服务端上下文等仍未验收；敏感AI写不能开放。
4. 独立端口服务恢复、构建EPERM、迁移基线、发布/前向修复回退及双市场人工验收；旧共享WS偶发0.2秒时序失败另包调查，不在股票包修改冻结期货。全系统未完成，保留既有续做自动化。
