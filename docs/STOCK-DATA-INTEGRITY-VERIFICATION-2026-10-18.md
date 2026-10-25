# A股数据真实性与盘后同步：执行记录

2026-10-18，北京时间18:51运行检查点。承接国际金银启动记录；本轮只加强A股来源/历史导入，不继续扩展国际品种。

## 状态与真实边界

**部分完成。** A股盘后同步安全校验、来源响应证据、只读同源抽查及旧历史导入安全门已实现并验证。原后端已加载新的盘后接入代码并运行。**不能宣称第三方数据100%正确，也不能宣称全市场/全部旧历史已经认证。** 下一真实交易日的新快照原始证据落库仍待观察；旧历史成交量/复权版本迁移尚未实施。

原则：不生成/随机填充行情，不把模拟资金称实际资金；缺价不填零、不冒称停牌；供应商失败不变成空成功。第三方TLS接口返回、同源一致性、交易所独立核验是三件不同的事，本轮没有冒充最后一项。

## 四个工作包及数据流

### 1. 盘后接入与证据

保留现有 `StockEodWorker`：应用持续运行时，每5分钟检查；**北京时间16:30之后**才尝试当天同步。先核验Teajoin交易日历，再取当前上市目录和当天daily；每日PG唯一记录/事务领取，重复轮询、重启不会重复下载。正常交易日最多3次来源请求；确认休市只查日历，不造周日价格。

本轮加强：

- 原始JSON重复键、NaN/Infinity、假等价code（false/0.0）、必要字段缺失、重复列、异常结构拒绝；保持8MiB/10000行/64列/超时边界、has_more=false和无自动来源重试。
- 日历必须是请求的SSE、同一日期、恰好一行，is_open仅0/1及对应字符串；不能将错误交易所或bool/float当休市。缓存daily也必须经过日历检查，不能跳过休市防护。
- 新 `EodResponse` / `EodFetchResult` 在进程内部传递结构化数据。`run_eod`仍适配旧三元组来源，已有调用方/隔离测试兼容；HTTP和数据库字段不破坏性替换。
- 新成功快照的可选 `source_evidence` 记录接口名、**原始HTTP响应SHA256**、字节数、行数、真实接收时间；不保存Token、请求密钥或整个响应文本到日志。缓存输入单独标记cached_daily及规范化缓存hash，不伪称刚下载的行情。
- 原价、除权昨收参考、手/元、证券标识、日期、OHLC、缺失/未知列表的原校验保留。原始成交额千元转元，涨跌由代码按close/pre_close计算；板块等权统计和K线指标仍由程序计算，不调用AI猜价格。
- 新来源证据参与该次快照版本；旧快照未加写“历史采集时间”，旧hash不变。原 `market_from_eod` 可以校验/读取新旧快照；原价盘后数据不拼入前复权历史。

参考字段与单位：[daily官方文档](https://tushare.pro/document/2?doc_id=27)；[交易日历文档](https://tushare.pro/document/2?doc_id=26)。文档用于协议解释，不代替Teajoin渠道本身的质量/授权认证。16:30是本项目保守调度时间，不是对供应商更新成功的承诺。

### 2. 有预算只读抽查

新增 `scripts/audit-stock-eod.py`：仅专用本地PG5433；数据库事务READ ONLY；默认只校验已存快照hash；显式 `--verify-source` 才做**一次**daily请求，最多3只不同标准证券/一个已存交易日，不循环全市场，不自动重试。

从已匹配快照选择沪/深/北代表样本，隔离unknown代码；核对原价OHLC、除权参考pre_close、成交量、金额和程序计算涨跌。异常价量/重复/错日/返回非请求代码拒绝；缺样本/数值不一致明确报告no_data/different，不覆写旧数据。输出文件存在时在加载配置/调用来源前拒绝。

独立代码审查发现并修复一项P2：原先仅检查输出文件存在性，实际创建文件发生在付费查询之后，同路径并发/已调用来源后失败重跑可能重复收费。现**先原子创建 `<output>.claim.json` 并fsync**，记录claimed/source_started/completed/failed；已开始尝试的失败/取消保留标记，同路径不能自动再调用。source_attempts是保守的已尝试上限，不冒充供应商实际扣费账单；领取文件不含密钥。审查复验47专项通过，无新增Critical/Important。另已在daily分页未确认时直接停止，不再额外调用adj_factor。

**本次实际调用：1次**，2026-10-16，样本 `600519.SH`、`000001.SZ`、`920000.BJ`，3条全部matched，differences为空。接收时间2026-10-18 18:40:59（北京时间），响应427字节，SHA256 `3aae80b358880fea4ce5d48465414aeab13624212001edba7533412dafe0432d`。

报告：`logs/stock-eod-integrity-live-20261018.json`，明确 `independent_verification=false` / same_provider_sample_consistency_only / production_data_modified=false。旧快照没有原HTTP响应证据，因此stored_raw_response_evidence=false；本次抽查没有伪造给旧批次补证据。

### 3. 现有运行、数据库与真实链路

- 盘后读/同步旗标均true，来源账号已配置；不输出密钥。后端实际PID9808，前端7268、PG3532、桥14236、WSL保活32884；以 `logs/local-services.json` 和实时端口复核为准。
- 后端8002及前端3000/market实际200。`/health`的进程ok不代表所有期货行情完整；原market.live.ok=false的已知问题未在本轮修复。
- PG记录2026-10-18：no_data / market_closed / attempts=1，无价格快照。2026-10-16仍partially_completed，data_version仍 `2b85dbcca6e90c0899ceaa22208a6f3e9361f843e0c07e1c2f12811bc89204bd`，未覆盖。
- 原目录5558、当日覆盖5548、缺10/未知19保持明示；本轮只抽3只，不能据此将其余数据认证通过。行情缺失不填假价，旧qfq历史与raw EOD分开。

### 4. 审查发现的旧历史导入金融问题与安全门

在 `stock_history.py` / `import-stock-data.py` 发现与本次“保证真实”直接相关的问题，已用红绿测试修复未来导入：

1. 缺逐日复权因子时原代码默认1或借用其他日因子；现在必须有该日及实际最后K线的有效因子，零/负/NaN/Infinity/bool/缺失拒绝。
2. 原前复权锚点会取无对应K线的更晚因子；改为**最后实际K线日期**因子。原公式与4位小数精度保留，溢出/精度舍入成零价拒绝。
3. 缺成交量原来补0，小数手原来被int截断；现在抓取层保留真实小数手，缺失/非法价量、错误OHLC/标识/日期/重复、分页未确认均拒绝，不静默跳过。
4. 920北交所裸代码原来先匹配9→SH；现先识别920→BJ。
5. 旧 `kline_history.volume` 是整数手字段，不能无损容纳小数手；**写入前拒绝小数手**，等待独立版本化字段迁移。整手才精确转int，不擅自改单位或四舍五入。
6. 旧qfq历史没有可核验复权锚点元数据；禁止向已有序列增量拼接新的复权基准。不重算/覆盖旧表和旧报告，不以新接口正常就证明旧批次正确。

这意味着旧手动历史导入工具遇到上述情况会报错，而不是继续“成功导入”。当日日线盘后worker不使用该旧导入路径，继续可运行；期货路径/账户/订单不变。旧历史是否实际受过缺因子/截断或混锚点影响，**本轮未逐批证明**，后续必须做版本化审计和迁移，不能假称已修复既有数据。

## 实际修改文件

新增：

- `backend/app/services/stock_eod_audit.py`：hash校验、代表样本选择、确定性比较。
- `scripts/audit-stock-eod.py`：只读本地PG/有预算同源验证入口。
- `backend/tests/test_stock_eod_integrity.py`、`backend/tests/test_stock_eod_audit.py`、`backend/tests/test_stock_history_integrity.py`、`backend/tests/test_stock_audit_claim.py`。
- 本记录与 `docs/superpowers/plans/2026-10-18-stock-data-integrity.md`；原扩展计划只追加续做检查点。

修改：

- `backend/app/services/stock_eod_sync.py`：严格接入、来源证据DTO、缓存日历及小样本查询。
- `backend/app/services/stock_eod.py`：可选证据进入新快照版本，旧调用保持默认。
- `backend/app/data/stock_history.py`：完整分页/日期/身份/价量/逐日因子/锚点与BSE代码。
- `scripts/import-stock-data.py`：整数手与旧qfq增量写入安全门。
- `backend/tests/test_stock_eod_source.py`：合法休市/取消fixture补全请求契约中的exchange；原断言保留，没有放宽。

## 实际命令与结果

从仓库根目录执行：

|命令|结果/证据|
|---|---|
|`backend/.venv/Scripts/python.exe scripts/audit-stock-eod.py --output logs/stock-eod-integrity-offline-20261018.json`|退出0，0上游调用，仅旧快照hash验证|
|`backend/.venv/Scripts/python.exe scripts/audit-stock-eod.py --verify-source --output logs/stock-eod-integrity-live-20261018.json`|退出0，1上游调用、3样本一致；不是独立认证|
|`backend/.venv/Scripts/python.exe -W error -m pytest backend/tests/test_stock_audit_claim.py backend/tests/test_stock_history_integrity.py backend/tests/test_stock_eod_integrity.py backend/tests/test_stock_eod_audit.py backend/tests/test_stock_eod_source.py backend/tests/test_stock_eod.py backend/tests/test_stock_eod_integration.py backend/tests/test_stock_eod_lifecycle.py -q`|**83通过**，`logs/stock-data-integrity-focused-reviewed.log`|
|`backend/.venv/Scripts/python.exe -m pytest backend/tests -q --junitxml=logs/stock-data-integrity-backend-reviewed.xml`|**1921通过 / 3既有跳过 / 502 warnings，60.70秒**；`stock-data-integrity-backend-reviewed.log`|
|`backend/.venv/Scripts/python.exe scripts/test-stock-eod-local.py`|真实PG隔离schema **17/17**；并发领取、失败/过期、事务回滚、JSON投影、原public目录/快照/序列不变、清理通过；`stock-data-integrity-pg.log`、`stock-eod-postgres-results-20261018-104111-ba15d39a.json`|
|`backend/.venv/Scripts/python.exe backend/scripts/migrate_stock_eod.py --check`|原20261018_003/checksum/结构通过，applied=false|
|`pwsh -NoProfile -File scripts/run-local.ps1`|控制重启本轮自有后端后退出0，实际新监听9808；`stock-data-integrity-launcher.log`|
|`backend/.venv/Scripts/python.exe scripts/test-stock-expansion-local.py`|最新后端真实HTTP **19/19**；`stock-integrity-final-stock-expansion-http.log`|
|`backend/.venv/Scripts/python.exe scripts/test-stock-product-local.py`|最新后端真实HTTP **14/14**；覆盖、资金幂等/权限/股期货隔离、raw/qfq不混合；`stock-integrity-final-stock-product-http.log`|
|`backend/.venv/Scripts/python.exe scripts/test-stock-replay-local.py`|最新后端真实HTTP **18/18**；原价、T+1、下一开盘、费用、幂等/权限；`stock-integrity-final-stock-replay-http.log`|

红测记录：来源15用例中14失败/1原已拒绝；只读审计9失败；历史安全门15用例14失败/1原拒绝，随后溢出2失败、整数DB类型1失败、旧锚点拼接1失败；审查后费用领取/分页早停4失败。相应 `logs/*-red.log` 保留。最终83专项和1921全后端通过。没有删测试、跳过新用例、降低金融断言或用测试结果写死业务。

PG集成使用自有测试schema和明确fixture，不调用上游，也不将其发布为真实行情。实际网络核对是另外一次授权Teajoin请求，实际读生产快照且事务只读，未用mock替代该验证。

未执行：本轮无前端修改，因此没有重跑前端构建/浏览器UI；不复用上轮285作为本轮新跑结果。下一真实收盘增量落库、完整旧历史重建、独立交易所/第二来源交叉核验、生产发布未执行。此前standalone EPERM/主题hydration风险不因本轮后端测试通过消失。

## 兼容、迁移、回退与成本

- 无新依赖、无数据库结构变更、无DDL迁移、新旧JSON快照兼容；旧方法/旧hash/旧报告保留。原EOD迁移SHA仍 `32c748cd87746a1117457dafeb8adaabd473557057dadaad74d9f15ba70910fe`。
- 新证据元数据是新的抓取观测版本；重复业务日不会再次领取。旧三元组来源仍可用，但不会凭空得到raw HTTP证据。
- 成本：本轮**1次**Teajoin小样本请求，0模型调用，0批量历史重导。正常EOD仍最多3请求/日；手动缓存路径新增日历校验，最多2次（日历+目录），不盲目信任缓存日期。重复轮询读取PG，不无限收费。
- 只在qa_账户执行真实HTTP模拟资金验证；原用户/期货账户和真实资金未动。PG隔离验证前后public快照/目录/序列一致。
- 回退安全路径：如新供应商严格校验触发兼容问题，关闭STOCK_EOD_SYNC_ENABLED并重启，保留STOCK_EOD_ENABLED和最后成功快照，先核实失败，不删除任务/清库强行重试。无结构回退需求；旧读取代码可验证带可选证据的完整hash。旧历史导入安全门不要回退成“缺因子补1/量补0”，暂不使用旧CLI扩充历史，先迁移再启用。
- 当前任务表attempts=1，来源失败不会自动重试；应用离线漏日补采/预算可控重试需独立状态机与版本迁移。服务需持续运行，不承诺电脑关机后仍会自动抓取。
- 审计领取文件保持为审计轨迹，不自动删除。失败后先检查phase/source_attempts；已进入source_started时不应删除标记盲重试。需要另一次核对须由操作者明确采用新的输出路径和预算，而不是脚本无限重试。

### 审查后追加验证

审查P2及来源分页早停新增/增强测试共4项先红后绿。真实临时文件验证并发同路径仅1次模拟付费边界，失败重跑不再进入来源；这是费用幂等单元测试，不冒充真实网络核对。实际来源核对仍只有上面的**1次**。

新代码实际离线调用：`backend/.venv/Scripts/python.exe scripts/audit-stock-eod.py --output logs/stock-eod-integrity-claimed-offline-20261018.json` 首次退出0/0来源调用，再执行相同命令退出1（FileExistsError，预期拒绝）；真实PG读取成功，领取文件phase=completed/source_attempts=0。没有为测试领取机制再请求上游。

## 人工验证与下一轮四包

人工：打开 http://127.0.0.1:3000/market → A股，确认交易日/采集时间/缺10未知19，不将历史日线当实时。新建报告前看研究覆盖范围；不要以目录5558推断历史行情5558全有。审计默认离线命令如上；在线核对会消耗来源额度，不重复运行本轮已存在的报告路径。出现错误应保留旧数据和错误任务，不能手填价格“修好页面”。

下一轮：

1. **真实收盘验收**：观察下一交易日16:30后单次调度、来源证据/目录/行情原子落库和用户展示；失败保留记录，再设计有预算重试/漏日补采。
2. **旧历史审计与迁移**：版本化raw/因子/复权锚点、成交量小数手字段、采集/修订时点；先隔离演练和备份，不能就地盲重导或污染旧报告。
3. **数据缺口对账**：19未知身份/10缺价的来源版本、上市状态与交易所口径；缺数据不猜停牌，受控补数/逐批来源证据。
4. **旧功能及发布风险**：继续股票AI预算评测、历史报告可得时点、原期货市场行情盘缺报价、构建与发布回退；全产品仍部分完成，保留续做自动化。
