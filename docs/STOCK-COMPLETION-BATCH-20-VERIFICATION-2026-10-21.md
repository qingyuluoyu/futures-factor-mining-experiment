# B20 股票K线参考价与小数成交量真实交付

## 状态

2026-10-21 04:57，北京时间。四包代码和本地验证完成，**系统仍部分完成、未发布**。这次确实修复股票用户入口的金融展示，不是补采或只新增审计脚本；但不因此宣称五年回测、AI、运行或发布验收完成。没有重启服务、改期货、写生产业务数据或请求上游。

执行前读用户AGENTS、主计划、期货保护计划和B19子计划/交付，B19原989保护verify通过；核实其他代理均为已结束只读审查，没有并发实施者。先搜索全部K线调用方、EOD模型/查询/测试、stock-api及股票详情/图表/回放，未碰共享认证、网关、布局或市场切换函数。

## 已复现原因与修复

1. **不复权涨跌基准丢失。** 供应商快照原有vendor_ex_rights_pre_close，但eod_bars丢弃；详情和图表用相邻已载入raw close分别计算，除权/漏日时可错。反例旧收100、除权参考10、收11，旧UI -89%，现在10%；只有一根且参考5/收11现在120%。参考缺失不回退，UI显示不可计算。
2. **小数成交量显示成零。** 图表原toFixed(0)把0.01手显示0。新确定性格式保留小数，非零小于0.000001显示明确阈值而不是0，万/亿为展示缩写，悬停title保留输入原值及单位；均量也用同方法。不改原始量、指标或撮合。
3. **前后端口径未充分校验。** 原详情已有OHLC/重复日线检查，并非完全无校验；但旧网络函数只作TS断言，未核返回code/参考/来源/复权单位一致性。新股票专用包装器补齐这些检查及有界数量/取消，不修改带共享市场切换的stock-api.ts。

## 实际文件与数据流

- `backend/app/services/stock_eod.py`：仅eod_bars新增默认false的include_reference keyword，老调用仍原字段形状；显式true时读取并校验正有限非bool pre_close，不填默认值，不容许坏参考变空成功。既有EOD方法/hash/存储数据/调度不变。
- `backend/app/api/stock.py`：仅股票kline ASCII代码校验、调用true和响应兼容扩展。raw增加bar.pre_close及reference_price/change_basis=vendor_ex_rights_pre_close；qfq仅标adjacent_loaded_adjusted_close，不携raw参考，不拼接原价。旧历史优先、无历史才按原flag回退EOD保留。
- 新`frontend/src/lib/stock-price-change.ts`：方法stock-price-change-2026-10-21.v1。统一reference/change/changePct及显示量；有限、正参考及结果溢出检查，缺参考/不可用不返回零。raw仅参考字段，qfq仅已载入前根，不由模型计算。
- 新`frontend/src/lib/stock-kline-contract.ts`：复用原getStockKlineApi和其超时/身份网络层，Zod校验code回显、有限正OHLC/日期和顺序唯一/量/可选正pre_close、来源/复权/量单位/基准。1—3000、响应≤请求数量、取消前后检查。不提供任意枚举或坏值默认成功；没有元数据的旧qfq明确兼容，旧raw没有参考只能显示不可计算。
- `frontend/src/components/market/stock-quote-detail.tsx`：改用新包装器；header与图表共用基准，展示来源解释；请求失败清空旧bars与metadata，不能沿用旧价格。`stock-kline-chart.tsx`：共用助手/小数量显示、OHLC颜色也用同参考；raw周/月跨除权不可比不算幅度，qfq明确相邻已载入复权变化而非官方幅度，缺省基准安全unavailable；独立说明方法版本。tick风格原指标/窗格/交互公式不改。
- `frontend/src/components/trading/stock-replay-workspace.tsx`只给原StockKlineChart调用补changeBasis="unavailable"，回放现无官方参考，不拿相邻撮合原价伪装官方涨幅。资金/成交/持仓/账本逻辑不改。
- 新保护器`scripts/check-stock-b20-preservation.py`；新后端测试`test_stock_kline_reference.py`、`test_stock_b20_preservation.py`；新前端测试`stock-price-change.test.mjs`、`stock-kline-contract.test.mjs`、`stock-kline-reference.test.mjs`，扩展原`stock-quote-detail.test.mjs`。新增隔离/只读实库脚本`test-stock-completion-batch-20-postgres.py`、`test-stock-completion-batch-20-live-readonly.py`，原harness02仅新增b20标签。
- 子计划`docs/superpowers/plans/2026-10-21-stock-completion-batch-20.md`及主/保护计划检查点更新。没有新依赖、DDL、API破坏性替换、原始来源/历史数据重导或用户账户写。

## 金融口径与兼容

确定性日幅度=(close-reference)/reference×100。不复权只用供应商除权昨收，这不是持仓总回报或公司行动现金流；供应商字段经既有授权盘后快照读取，不宣称独立真实性/PIT认证。qfq仅相邻loaded adjusted price变化，可能跨漏日，明确不叫官方当日幅度。raw周/月和当前回放没有足够可比参考，显示不可计算，仍可看OHLC/量；不把未认证幅度塞入指标或账户权益。原stock-chart-1.0.0指标公式版本保留，另有价格展示方法版本，不篡改历史研究结果。

新增字段旧消费者可忽略，include_reference默认false直接调用兼容。新前端先遇旧后端raw无pre_close时不算幅度，安全降级而非错用邻收；共同发布才有新参考价展示。stock-api.ts及共享request/AuthContext没有修改。日/月次数限制不恢复，单次参数/并发/超时/费用边界仍保留。

## 实际命令及结果

Python在仓库、Node/TSC在frontend，无安装/升级依赖。日志在logs。

```powershell
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b19-preservation.py verify logs/futures-preservation-b19.json
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b20-preservation.py snapshot logs/futures-preservation-b20.json
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_stock_kline_reference.py -q
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_stock_kline_reference.py backend/tests/test_stock_eod_integration.py backend/tests/test_stock_market_api.py -q
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_stock_kline_reference.py backend/tests/test_stock_eod_integration.py backend/tests/test_stock_b20_preservation.py -q
node --import ../logs/node-alias-register.mjs --test src/lib/stock-price-change.test.mjs src/lib/stock-kline-contract.test.mjs
node --import ../logs/node-alias-register.mjs --test src/components/market/stock-quote-detail.test.mjs
node --import ../logs/node-alias-register.mjs --test src/components/market/stock-kline-reference.test.mjs src/components/market/stock-quote-detail.test.mjs src/lib/stock-price-change.test.mjs src/lib/stock-kline-contract.test.mjs
backend/.venv/Scripts/python.exe -X utf8 scripts/test-stock-completion-batch-20-postgres.py
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests -q
node --import ../logs/node-alias-register.mjs --test (Get-ChildItem -Recurse src -Filter *.test.mjs | ForEach-Object FullName)
node node_modules/typescript/bin/tsc --noEmit --incremental false
backend/.venv/Scripts/python.exe -X utf8 scripts/test-stock-completion-batch-20-live-readonly.py
$program=Get-Content logs/b19-audit-command.txt -Raw
backend/.venv/Scripts/python.exe -X utf8 -c $program logs/b20-state-before.json
backend/.venv/Scripts/python.exe -X utf8 -c $program logs/b20-state-after.json
$program=Get-Content logs/b20-scope-compare-command.txt -Raw
backend/.venv/Scripts/python.exe -X utf8 -c $program
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b20-preservation.py verify logs/futures-preservation-b20.json
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b20-preservation.py snapshot logs/futures-preservation-b20-expanded.json
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b20-preservation.py verify logs/futures-preservation-b20-expanded.json
node --import ../logs/node-alias-register.mjs --test src/lib/stock-kline-contract.test.mjs
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b20-preservation.py snapshot logs/futures-preservation-b20-final.json
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b20-preservation.py verify logs/futures-preservation-b20-final.json
```

- `b20-backend-reference-red.log` **6失败**是真实旧行为反例：丢pre_close、坏参考仍成功；实现后与旧测试18通过。新增保护测试后目标`b20-backend-target.log` **13通过**（选择集合不同，非18退化）。
- 新模块初始`b20-frontend-contract-red.log`8失败为模块缺失，非旧金融断言。原组件新3行为反例`b20-detail-red.log`失败；接线后一项仍因测试VM Error跨realm而通用提示，`b20-detail-green.log`保留12通过1失败，修fixture注入同Error后13通过，不放宽断言/吞异常。
- 图表新增render测试首轮3失败为CJS require不支持lightweight-charts ESM（`b20-node-target.log`）；该测试只验证React渲染文本，不执行useEffect/canvas，故为图形库注入空边界、金融助手/指标均真实执行，`b20-node-target-fixed.log` **16通过**。不能将其说成真实浏览器/画布验收。
- 自查raw元数据单位：新分支原先允许缺volume_unit，补拒绝反例`b20-raw-unit-red.log` **1失败3通过**（Missing expected exception），实现一行要求raw必须lot_100_shares。既有raw后端从引入时已提供该字段，不破坏旧raw契约；无metadata旧qfq兼容保留。独立只读再复核未发现新P1/P2。
- 完整后端`b20-backend-full.log` **2689通过、3既有skip、739warnings、139.45秒、退出0**；完整Node初次`b20-node-full.log`及单位收紧后的最终`b20-node-confirmation.log`均 **510通过、0失败/skip**；最终`b20-tsc-confirmation.log`退出0。单位收紧后后端无变动，未假称重复跑后端。通知AsyncMock未await等既有warning未借机改期货；无新skip/删测试/关闭检查。
- 私有PG/JWT/ASGI **52检查通过、schema清理成功**：`stock-completion-b20-pg-20261020-204756.json`、`b20-pg-first.log`。真实存取含0.01手、官方参考到HTTP、qfq优先且不拼raw/未来价格、原eod_bars默认字段、关闭flag无假K线、匿名401/期货409、ASCII/上限422、源坏值503脱敏和request_id、public表摘要不变。期货账户/盘/他人/AI工具禁写沿原harness真实隔离回归；模型只用fixture，不是付费模型认证。
- 独立只读审查未发现明确P1/P2，已读目标/完整日志及PG代码/证据；没有自行执行、联网/DB或读取配置明文，不能把审查静态阅读说成独立运行全回归。

## 实库只读与保护证据

- `b20-state-before.json`、`b20-state-after.json`、`b20-scope-and-state-comparison.json`：READ ONLY核实on；paper四表、AI交易四表、股票目录/EOD/归档任务摘要/数量一致；三日归档数量/元数据、旧两日全量观察摘要、20日观察计数5549均不变。旧stock/futures日线count/maxid/maxdate和首尾20行相同，**不是所有历史逐行认证**。审计候选恢复表名不存在不代表其他恢复表已被覆盖。
- `stock-completion-b20-live-readonly-85334b7120c44635be0301a04684444c.json`、`b20-live-readonly.log` **4检查通过**：当前真实public行情，经本地ASGI股票API，在专用本机5433只读/可重复读/SQL8秒/总45秒事务读取；raw候选000006三根、到20日、参考价/成交量等于已存最新快照；qfq000001两根独立读，不附新raw。此脚本固定本地stock身份覆盖认证以避免修改真实账号，**不是生产JWT/运行服务/部署认证**；JWT边界由上项私有PG验证。股票数据为已存真实快照/旧历史，不调用上游，不重新抓20日或21日未收盘。
- 旧B20原987保护verify的**预期退出1**在`b20-preservation-original-diff.log`：仅新增3个Node测试及修改计划内详情测试1项，无删除/冻结生产改动；原986其他项相同。`b20-scope-and-state-comparison.json`对B19旧989逐项核对，只变本计划5股票生产文件及1测试；不是声称B19所有989无变化。
- 原基线保留，经复查扩展`futures-preservation-b20-expanded.json` **990文件**，初次`b20-preservation-final.log`无漂移；随后单位反例只改变`stock-kline-contract.test.mjs`，`b20-late-unit-test-diff.log`明确预期退出1且无新增/删除/其他变化。完整Node/TSC及只读复核后再存`futures-preservation-b20-final.json`，最终`b20-preservation-confirmation.log` **990无漂移**。不是重置未核实的生产变化；原/扩展清单保留，新增测试保护不追认原来不存在的状态。
- 7个允许股票文件最终SHA另存在`b20-approved-file-hashes-final.json`，绑定当前已审查/最终测试实现；早期`b20-approved-file-hashes.json`保留，不当作收紧之后最终版本。共享stock-api.ts、认证/网关/期货/配置/旧归档未改。
- 全轮新增来源请求0、产品模型0、public业务写0；不产生本轮上游/产品模型请求计费，B18供应商实际扣费仍未知。私有PG测试造的数据仅私有schema，已清理，未以其证明公共真实覆盖。

## 迁移、回退与未执行

无生产DDL/模型变更/迁移/公共数据写，旧响应和方法/存储保留。可独立撤回新前端包装器/展示及兼容字段，但默认金融回退应关闭该幅度，不恢复已知错误计算；不能说回退前端会修复旧浏览器已缓存逻辑。未修改期货或缓存，不需反向重导原价/qfq。发布需同时核对新股票前端与后端参考字段，不靠一次health200放行。

完整构建、专用运行恢复/启停、浏览器/真实canvas、人工双市场验收与发布回退演练 **未执行**。本轮端口只读观察PG5433、3000/3001监听；8002/6380/8110未监听，不明进程未杀、不抢3000，不把其页面算当前修复生效。B13 standalone EPERM仍需独立运行发布包解决。

人工路径（待运行发布包）：股票板块→没有旧qfq历史但有盘后价的证券→日K核供应商参考及小数量；一根也有参考时有幅度、缺参考为—；切周/月显示不可比较；有qfq证券不附raw且标相邻复权变化；模拟回放不显示伪官方幅度；最后切期货核原资金/订单/任务及K线不变。错误响应应为明确错误，不保留旧价格。真实除权日期不能凭当前fixture认定覆盖，另以授权公司行动证据核验。

## 剩余风险和下一四包

1. 9missing/19unknown真实原因、全市场历史目录/停牌/公司行动仍未获完整证据，复用缓存对账后按明确端点/授权/预算补采，不名称推断或无预算重试。
2. PIT/复权锚点、旧300股来源质量、五年8931缺价及600221估值缺口；B19原价产物仍非可用回测面板，不能因图表幅度正确而升级资格。
3. 可信版本股票DSL/指数基准/研究与模拟扩池、股票只读AI及确认任务等垂直切片；S04/S05、真正期货研究/GP按冻结保护另包设计，不修改期货绕门禁。
4. 独立服务/构建/迁移发布与回退/双市场人工验收。续做自动化保留，本批不满足全系统完成停止条件；读取最新记录，不重写B19或重复请求20日来源。
