# B10 GP检查点内核、固定输入及安全边界交付

检查点：2026-10-20 07:53，上海。**部分完成：可复现内核及内部持久服务已实现并通过本机验证，生产任务执行器尚未接通。超级因子整体未完成；整个金融系统未达到发布验收。** 没有把检查点库当成已可用的任务页面。

## 1. 三个工作包的真实交付

### B10-1 逐代内核及JSON检查点

修改 `backend/app/services/factor_lab/search.py`，新增 `backend/app/services/factor_lab/checkpoint.py`、`backend/tests/test_gp_checkpoint.py`。

- 根因：旧stepwise在繁殖前yield，旧后台每tick只取一个snapshot就退出generator，导致繁殖永不执行；数字start_generation只跳循环、不恢复种群。
- 现在先繁殖/跨岛迁移再yield；snapshot兼容增加checkpoint字段，完整保存**下一代种群、Random状态、全部唯一候选历史、已完成代数**。同输入/参数/环境恢复，后续每代种群和冠军逐字段一致，不只比较最终冠军数量。
- 绑定完整bars、timeframe、全部SearchConfig、checkpoint方法版本、Python minor/numpy版本、特征/算子名称-元数/固定token offset。源码金融方法变化仍须主动升级版本，不能只凭hash认证新方法。
- 检查点JSON严格字段/类型、RNG形状、种群岛数/大小、token树结构、代数、hash、8MiB状态预算；无pickle/eval。拒绝混入seed_best、错误代数、篡改输入/配置/环境和非有限数字。hash不是签名或权限凭据。
- checkpoint恢复不重新评估初始种子；到末代直接结束。旧仅传start_generation的内部调用兼容为确定性真实重放，不伪改代号；这不是生产推荐的续训方式，更不能在每次重放中换数据。
- 去重保留全部**唯一token历史的最高分记录**，分数相同保留最早者，排序语义与原_dedup_top一致；不按top-N丢失候选，不改变trials多重检验口径。原同步search和评分/手续费/样本外筛选公式不变。

### B10-2 固定输入与原子持久服务（内部、未接公开写入口）

新增：
- `backend/app/models/gp_checkpoint.py`：独立metadata的版本表和gp_checkpoint_sessions，不把新表自动加入旧启动DDL/全局ORM检查。
- `backend/migrations/20261020_008_gp_checkpoint.sql`、`backend/scripts/migrate_gp_checkpoint.py`：独立expand版本、checksum、超时/事务锁、列/类型/默认/PK/唯一/CHECK校验、重复/check-only；目标版本表限定current_schema，防私有验证错误读写public版本。旧迁移不改。
- `backend/app/services/factor_mining/checkpoint_store.py`、`backend/tests/test_gp_checkpoint_store.py`：可信业务内部接口，固定输入资源不是异步任务状态机；未来调用方必须从AuthContext绑定owner/盘，不能让模型自由传用户或直接访问。

数据流：调用方提供真实输入及来源声明 → 校验具体合约/交易所/币种/单位/频率/收盘窗口/有序OHLCV → 冻结输入和完整参数hash → 同owner+market+mode+key幂等 → 内核产生完整JSON → 行锁和expected_revision CAS → 原子保存代数/状态/hash → 调用方明确commit。服务不自己提交，也不下载、补造或覆盖旧task。

约束：
- 仅支持明确小写品种+YYMM的**具体月份国内期货合约**及对应交易所ID；不接受rb/rb888/混杂标点、不猜郑商所三位年份、不能把未知合约/连续主力冒充单合约。格式/静态品种映射不是合约上市真实性认证。
- v1内部输入仅日线、80—8000根、单合约、不调整、CNY、contracts和明确价格单位；分钟/连续换月/国际USD金银不在此能力内。上海时区采集时间、16:30保守收盘窗口；无缺值填0或小数手截断，123.45成交量往返保存不变。
- 来源元数据是**声明，不是独立供应商授权/真实性或PIT认证**；source_certified=false。校验记录supplier/exchange/currency/units/timezone/calendar/adjustment/contract_basis/source_version/license_scope/acquired_at；不能凭声明开放生产源。
- 输入4MiB、配置64KiB、状态8MiB；新持久运行最多5000个单次试验（population×generations+种子），严格控制种子/布尔/控制参数，不暗转换。**这是单次CPU/存储预算，不是每日、每月或业务调用次数限制。** 独特候选超出8MiB仍显式预算失败，不丢弃候选/假装完成，生产worker以后须正确落终态。
- owner/市场/盘隔离，复用key异意图拒绝，generation+1且revision一致才能推进；错误用户/盘404、股票scope403，不能跳代、覆盖旧代或在并发中重复提交。数据库约束约束状态一致性；应用层冻结输入，没有宣称DB trigger不可篡改。
- 暂未关联旧FactorMiningTask、用户删除/孤儿清理、生产worker租约/进程取消；没有公开写API/自动迁移旧任务，不能把此内部存储称为生产链路完成。

### B10-3 旧未验证任务边界与UI

修改 `backend/app/api/factor_mining.py`、`backend/app/services/factor_mining/loop.py`；新增 `factor_mining/capabilities.py`、`backend/tests/test_gp_capability_guard.py`；修改前端 `lib/super-factor-api.ts`、`components/super-factor/super-factor-page.tsx`、`mining-feedback.test.mjs`。

- supported明确accepting=false、exact_resume_available=false、checkpoint内核版本及原因，不再宣称2005全历史/固定分钟覆盖。
- **旧/tasks整组暂返回503**：旧记录没有可信JWT盘/固定输入，不能据当前用户偏好推断所属盘；读/新建/恢复/变更都不能绕过边界。认证/期货市场校验仍先执行，匿名401、A股409。原任务行未删除/修改，不能谎称返回空列表成功。
- 老后台在获取任务数据库连接之前返回0，不再进行不可信的首代/重放推进或下载。没有环境开关可以仅设true就打开未实现的生产worker；必须完成后续接入和验收才能改变能力。
- 前端默认未知能力也不允许新建，恢复按钮按实际能力关闭，保留明确错误/修复说明。旧HTTP客户端会收到有request_id/no-store/错误码的503，这是有意的安全兼容影响，不是因子/回测日配额。
- 当前本地旧挖掘任务0，故没有已有运行任务被静默中断。若其他部署有活动旧任务，发布前须由旧版本/受控管理员停止并保留记录，不能直接套用本机0任务结论。
- 其他期货手工模拟交易、资金、AI/量化任务、同步因子实验室和A股历史报告未改为此503；A股/history仍走StockResearchHistory，期货/history才是SuperFactorPage。

## 2. 先红后绿与独立复查

- `b10-kernel-red.log`：11失败，先验证新checkpoint及旧跳代反例；首次内核/原挖掘18通过。
- `b10-store-red.log`：缺模块预期collection失败；补内部模型/存储后23通过。
- `b10-capability-red.log`：2失败；`b10-view-red.log`：前端能力保护失败；实现后相关25及前端3通过。
- 独立复查发现两项P2：宽松品种解析接受rb/rb888/rb!2701+NYSE；重复best_seen导致200×200重复种子终态约17.1MB、超过8MiB。`b10-review-red.log` 8失败 → 严格具体合约/交易所、无损去重及单次预算后33通过。复查确认修复，无剩余具体重要发现。
- **真实PG发现而SQLite未暴露的失败**：JSONB把-0.0规范为0.0，导致下一次加载hash不符。`b10-pg-first.log`、`b10-pg-diagnosis.log`、`b10-pg-roundtrip-red.log`保留CAS结果及合成字段差异。canonical JSON仅统一有符号零表示，不舍入价格/成交量/评分、不改数值；实际PG恢复随后通过。
- `b10-config-red.log`：4个静默控制参数转换反例；补严格种子/布尔及cross_peers后通过。`b10-legacy-scope-red.log`：旧未保护读路由误取数据库，补全/tasks保护后通过。追加重算hash但坏RNG/树/代数仍拒绝。
- 最终受影响 **41通过**，`logs/b10-targeted-last.log`；独立复查再次实跑3项边界测试通过。没有删测试/放宽金融或权限断言。

## 3. 实际验证命令和结果

```powershell
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_gp_checkpoint.py backend/tests/test_gp_checkpoint_store.py backend/tests/test_factor_mining.py backend/tests/test_gp_capability_guard.py -q
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests -q
backend/.venv/Scripts/python.exe -X utf8 scripts/test-stock-completion-batch-10-postgres.py
backend/.venv/Scripts/python.exe -X utf8 scripts/audit-product-readiness-local.py
./scripts/run-local.ps1 -TimeoutSeconds 60
```

frontend目录：
```powershell
node --import ../logs/node-alias-register.mjs --test (Get-ChildItem -Recurse src -Filter *.test.mjs | ForEach-Object FullName)
node node_modules/typescript/bin/tsc --noEmit --incremental false
```

- 后端最新 **2553 passed、3既有skipped、671 warnings，exit0**，`logs/b10-backend-confirmation.log`。较早2537/2550是实现中的完整运行，不拿它们代替最终结果；既有utcnow/异步mock告警没假称消除。
- Node **449通过、0失败/skip**，`logs/b10-node.log`；TSC0，`logs/b10-tsc.log`。
- 新 `scripts/test-stock-completion-batch-10-postgres.py`（B02只加b10报告名）真实本地PG/JWT/HTTP：**66检查通过/私有schema清理**，`logs/stock-completion-b10-pg-20261019-235109.json`、`logs/b10-pg-last.log`。覆盖008事务DDL回滚/check-only/重复应用、表结构及checksum漂移、并发同key冻结、行锁CAS一成功一stale、事务中断保留前代、从实际JSONB精确恢复、多用户/盘/市场、旧任务API拒绝且无下载。没有用mock替代这些核心PG行为。
- PG测试只有明确标记synthetic的私有fixture；公共业务写0/来源0/模型0，公共旧task和gp表数量不变。测试打印的JSONB差异仅为合成字段，未输出密钥/真实用户行情。
- 运行HTTP第一次误用不存在的/factor-mining页面得到404，记录 `b10-runtime-http.log`，未创建假路由掩盖。查调用方后正确入口/history，`b10-runtime-confirmation.log`：health/login/history/market200，匿名mining/supported及paper/account401。不是已登录人工E2E。
- 新生产build/部署、浏览器视觉与真实用户新GP任务未执行；前者既有Windows standalone复制EPERM未修，后者明确未开放，不能为测成功绕过边界。

## 4. 数据、服务与迁移回退

- 只读安全重载预检 `logs/b10-before-reload.log`：研究9failed/17completed/7cancelled、挖掘/主播0、AI1paused、EOD终态/归档2completed；public GP新表0、catchup=false。核实原项目PID1532/父进程/8002监听才停止该backend；五服务07:50就绪，新backend PID6820。该PID只是时点观测。
- 最新07:51 READ ONLY `logs/full-code-audit-snapshot-20261019-235144.json`：19日5545/5558、缺13/未匹配19；旧研究300/585392到16日，回放3，影子11093观测/5549证券/2日；五年仍over_limit。没有抓20日未收盘，没有重复B08诊断/恢复或混复权。
- **008只在隔离PG应用并验证；真实public没有应用。** 独立metadata和关闭的生产写入口不要求本机启动自动迁移，不恢复_init_tables。未来生产接入前须单独受控apply/check，保留旧task并明确绑定/兼容计划；不可依赖自动补旧盘字段。
- 008是新增表，可回退代码而保留表/已写输入；事务内DDL/代次中断回滚已实测。没有删除旧表或定义破坏性down迁移。未来已被worker/报告使用的输入不得盲删，应停止相关worker、核对派生引用后前向修复。
- 回退到旧GP后台会恢复已知的不可靠推进/盘隔离风险，**不是安全回退**。回退必须同时保持旧/tasks网关关闭及后台停止，不能靠恢复旧代码声称恢复业务；真正重新开放须完成新worker整链验收。
- 0受控来源/0付费模型调用，无新依赖；常驻行情服务常规流量不冒称包含于探针。单次GP试验/字节预算不影响用户调用次数，不恢复每日/月或累计1000限制。

## 5. 人工路径和下一轮五包

人工待验收：本人登录切期货→/history，看到明确“执行器尚未接通”且新建/恢复不可用，不显示假成功或假冠军；切A股/history仍为股票保存报告。其他期货模拟资金/下单需另行本人验证；此轮HTTP200不证明交互通过。生产前还需新worker闭环、迁移与发布回退、浏览器及数据授权验收。

下一轮五包：
1. **GP任务scope与冻结入口：** 新任务从真实AuthContext绑定用户/市场/JWT盘/幂等key，真实合约及数据来源预检→固定输入；旧无盘任务仅显式管理员隔离审计，不能猜盘；分页列表/报告权限与新表绑定。
2. **GP原子认领/进程预算：** 新worker代次lease/heartbeat/expected_revision、可杀子进程、取消/暂停/超时/重启；同一代仅一次提交，不用不可中断to_thread超时后再启动新计算，先真PG/HTTP异常测试再开放能力。
3. **真实期货数据/研究：** 目前本地合格日线候选0，需既有授权渠道的合约、日期、换月/源hash；不能用错标签股票/合成测试补行情。分钟/连续/国际金银保持未验证边界。
4. **R02/S04/P01：** 旧研究CLI迁移与关闭演练、持久人工确认/写幂等、全局版本迁移和发布产物；B09连接池修正已在本轮独立复查确认，其他发布阻断保留。
5. **A股数据及研究：** 13缺失/19未匹配逐项对账、预算补采、原价/复权/时点版本桥接；PIT/五年/基准/DSL/回放扩池/股票AI/消息按主计划继续。

本轮不停止续做自动化，不能报告“全功能已完成”。
