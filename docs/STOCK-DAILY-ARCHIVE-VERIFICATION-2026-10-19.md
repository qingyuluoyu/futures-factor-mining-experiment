# 2026-10-19 02:20（北京时间）A股原价日线影子归档检查点

## 完成状态与边界

本轮3个工作包已落地并完成所列本机验证：独立Decimal模型/版本迁移、确定性快照转换与内部CLI、真实PG/本地兼容扩展归档及审查。**整体仍部分完成**；这是内部影子归档，不是已切换用户历史K线/回测数据源，不是全部旧历史/数据授权/交易所认证。

已读取用户AGENTS、原总计划、00:37最新交付及相关模型/服务/迁移/测试。保留前轮修改，无Git，不提交/分支；0供应商HTTP、0模型、0新依赖、没有下载或盲重导旧历史，没有重启服务。

## 实际文件与数据流

新增10个源码/测试文件：

- `backend/app/models/stock_daily_archive.py`：独立ArchiveBase，`stock_archive_schema_versions`、`stock_daily_archive_batches`、`stock_daily_observations`，未注册到共享Base。
- `backend/migrations/20261019_004_stock_daily_archive.sql`、`backend/scripts/migrate_stock_daily_archive.py`、`backend/tests/test_stock_daily_archive_migration.py`：事务/锁/超时/SHA/列类型与默认值/PK/唯一/检查/索引/组合FK校验。
- `backend/app/services/stock_daily_archive.py`、`backend/tests/test_stock_daily_archive.py`：来源hash与金融元数据、日期/采集时点、标识/覆盖分区、有限数值及精度校验；Decimal和幂等事务写入。
- `scripts/archive-stock-eod.py`、`backend/tests/test_stock_daily_archive_cli.py`：本地PG5433内部入口，必填来源UUID及预期版本，默认READ ONLY dry-run；显式`--apply`且迁移check通过才写。
- `scripts/test-stock-daily-archive-local.py`：独立随机私有schema、21项真实PG验证及清理。
- `scripts/test-stock-daily-archive-rollout-local.py`：固定已核实Oct16来源ID/版本，9项真实本地迁移/归档/精确回读/旧数据与HTTP边界验证。

文档：本记录、`docs/superpowers/plans/2026-10-19-stock-daily-archive.md`、原总计划追加最新检查点。

入口→本地权限/数据库限制→显式来源批次/版本→5秒语句/2秒锁/30秒总边界→完整已存EOD校验→来源已匹配股票→单事务完成批次+500行分块写观察→返回版本/数量。没有上游调用和用户账户/资金写入；没有用户HTTP新入口。跨进程唯一约束阻止重复批次；同输入重跑校验已有批次元数据和最多10001行内容，全部精确一致才返回already_archived，绝不覆盖不一致记录。日志含来源ID/版本/操作/行数，提交由调用方事务负责，不输出密码/Token。

## 金融口径

- 只归档原价`none`、日线、CNY；保留`lot_100_shares`小数手和CNY金额，使用Numeric(38,18)，不再写共用Integer volume。拒绝NaN/Inf/布尔/负量/零价/超界及超过18位小数，绝不静默round/int截断。DB还有有限数值上界、OHLC、沪深北标准ID/单位/唯一/日期组合FK约束。
- Decimal由**已有规范化JSON数值的字符串**转换，精确保留该JSON值，不宣称恢复供应商HTTP原文小数精度；原EOD金额已换算为CNY，不再次乘1000。
- `source_collected_at`是已有快照采集元数据，`archived_at`是本次归档时刻；`data_available_at=null`，不回填历史时点可得性。归档不补1因子、不混raw/qfq、不自动换复权锚点。
- 拒绝未来或未收盘当日归档；还拒绝交易日当天16:30前采集的旧快照，防止17点归档把12点盘中采集升级为收盘。允许次日采集旧交易日，但不是对来源交易日历的独立认证。
- 保留`verification=snapshot_only_not_independently_certified`、来源目录版本、方法版本、证据元数据和授权边界。没有原daily响应证据的Oct16快照不会因归档变为已认证。
- 未匹配19只仍隔离，缺价10仍缺失，不靠补零/名称猜测或拼旧qfq价格凑齐。旧585392行/300只及报告完全没有重导、覆盖或自动修复；当前目录5258只无旧历史的事实不变。

## 真实本地应用与版本

隔离PG及审查通过后，004已在**专用本地5433**兼容扩展应用：SHA256 `b2f7c69d76d48c715d1572f9094a41563ecd7bf11cf607b26890cb332a729f07`。新表没有指向用户账户，旧001/002/003及共用kline_history未改。

只归档一批已存真实数据：来源EOD `14831b03-e618-4f9d-a89b-f6db88f3c62b`，trade_date=2026-10-16，来源版本 `2b85dbcca6e90c0899ceaa22208a6f3e9361f843e0c07e1c2f12811bc89204bd`。

新影子批次 `f5ebaa36-febc-4789-a2aa-f4c265cf7ba5`，版本 `f372ea185838fcad514e95224a7d23e23a08b5aa4f39561e12b81cb8cdec6e1f`，5548行，目录5558、缺10、隔离19。实际首写archived、重跑already_archived同ID；5548行全部价量/金额/日期/单位/标识逐字段Decimal精确回读一致。只有这个原价快照归档，未导入585392条旧qfq。

公共旧股/期货历史按市场count/sum(id)/sum(close)/sum(volume)、EOD与目录数量/行JSON摘要及目录序列前后不变。此证明只覆盖列出的聚合/摘要，不是全库/账户/真实性认证；新表写入是预期副作用。原HTTP仍读取原EOD并明确raw_response_evidence=false，没有切换归档读路径。

## 实际命令与验证

| 实际命令/检查 | 结果 | 证据 |
|---|---|---|
| `backend/.venv/Scripts/python.exe -W error -m pytest backend/tests/test_stock_daily_archive.py backend/tests/test_stock_daily_archive_cli.py backend/tests/test_stock_daily_archive_migration.py -q` | **85 passed** | `logs/stock-daily-archive-focused-reviewed-20261019.log` |
| `backend/.venv/Scripts/python.exe -m pytest backend/tests -q --junitxml=logs/stock-daily-archive-backend-20261019.xml` | **2070 passed / 3既有skipped / 502既有warnings**，55.49s | 同前缀`.log`/`.xml` |
| `backend/.venv/Scripts/python.exe scripts/test-stock-daily-archive-local.py` | **21检查通过**，随机私有schema已清理、公库聚合不变 | `logs/stock-daily-archive-pg-final-20261019.log`、`logs/stock-daily-archive-pg-20261018-181830-963b6bb6.json` |
| `backend/.venv/Scripts/python.exe scripts/test-stock-daily-archive-rollout-local.py` | **9检查通过**；实际包含004迁移、只选定一批apply、重跑和5548行精确回读、既有QA登录/401/原状态API | `logs/stock-daily-archive-public-rollout-20261019.log`、`logs/stock-daily-archive-public-rollout-20261018-181640.json` |
| `backend/.venv/Scripts/python.exe backend/scripts/migrate_stock_daily_archive.py --check` | checked=true / applied=false，SHA吻合 | 实际终端及rollout readiness |
| `backend/.venv/Scripts/python.exe scripts/archive-stock-eod.py --batch-id 14831b03-e618-4f9d-a89b-f6db88f3c62b --expected-data-version 2b85dbcca6e90c0899ceaa22208a6f3e9361f843e0c07e1c2f12811bc89204bd` | 真实只读dry-run通过5548行；无apply不写表 | `logs/stock-daily-archive-real-dry-run-20261019.log` |
| 同CLI但预期版本为64个a | **预期拒绝，退出1**，不归档 | `logs/stock-daily-archive-real-wrong-version-20261019.log` |
| 新/旧迁移四文件`pytest -W error` | **102 passed** | agent实际回归；主流程完整后端再次覆盖 |

TDD/修复证据：金融转换30红、CLI3红、迁移41红，边界4红、午间采集2红均转绿。迁移首次绿有一次测试导入错（Base类路径），修正测试导入后41绿。初次PG Infinity被PG以22003类型溢出拒绝而不是IntegrityError，测试原异常类型错误而中断；已精确断言22003/23514/23503，**不吞任意DB失败**。初次归档仅比数量可漏掉正数成交量变造，真实PG内容变造测试先红；现比完整批次+所有有界观察，拒绝不一致且不覆盖。各失败日志和私有schema清理报告保留。

独立审查发现上述内容核对、午间采集两个重要项，已红绿修复；最终无剩余Critical/Important。审查的非阻断“行数名称超出断言”已补真实观察count=1后重跑21绿。READ COMMITTED六并发1提交5幂等；CLI相同REPEATABLE READ四并发只承诺成功或明确40001安全回滚、只一批一行，不声称每个并发CLI都成功。没有自动重试。

未执行：本轮无前端改动，未重跑Node/tsc/build/浏览器（上轮297/tsc及截图仅为历史证据）；未重复付费模型、未做下一真实收盘、全历史因子/可得时点/独立来源核验。Windows发布EPERM/旧警告仍是已知风险，不假装已发布。

## 迁移、兼容和回退

现共享Integer存量表无法保存小数手且缺复权锚点，局部改Integer会波及期货且不能补回已丢精度，因此采用独立股票原价观察表作为expand阶段。只新增小空表/索引再归档一批，锁与语句有界，不在主应用启动时迁移或归档，不自动批量migrate旧历史。

部署顺序：审核004 → 显式迁移 → `--check` → 指定来源/预期版本dry-run → 显式apply → 逐字段核验 → 后续独立版本再接受控读路径/自动worker。**当前内部CLI不是长期自动同步方案**；尚未接worker，现EOD收盘采集仍照旧。

回退：停止新CLI/未来自动入口，保留004表及已归档数据，旧代码/读路径仍能工作；不DROP有数据表、不恢复整库、不清Redis。若影子内容不一致，当前重跑拒绝，不自动改动/覆盖；冻结该批次并通过新方法/新版本和前向迁移修复。004一经应用不再编辑；因子观察/锚点等后续采用005+独立扩展迁移。

## 运行、成本、人工验证与后续四包

本轮未启停服务；实际监听backend12652:8002、frontend7268:3000、PG3532:5433、bridge14236:8110，原Redis6380和原生6379未改。以后重新核实PID；health200但market.live.ok仍false、virtual67键，不代表全部期货行情正常。0外部费用；新增5548行本地存储，重复内部归档要有界比较最多10001行，有成本但无供应商/模型费用。

人工路径：用上面默认dry-run命令查看版本/数量；DB只读查询新批次与观察，确认Numeric38,18/不复权/原始来源ID；正常网页应仍显示Oct16、5548/5558、缺10/未匹配19及原响应缺失。新归档还没有用户读取入口，不应把当前页面仍旧来源当成切换已完成。

下一轮（含已约定05:15单次续做）先读本记录，勿重复本轮或盲重导：

1. 在保留原EOD成功语义前提下接Feature Flag/有状态、幂等、可恢复的归档worker；失败可追踪，不让影子归档失败吞掉真实采集，也不重复收费。不依赖一次人工CLI作为长期方案。
2. 新005+股票因子观察/可得时间/锚点和版本化研究读契约；保留旧报告，不把归档时间回填历史时点、不混raw/qfq、不改共用期货volume。
3. 10缺价/19未匹配来源标识对账和下一实际16:30后交易日收盘验证；先预算/身份/日历校验，05:15不抓未收盘daily、不猜价格。
4. 股票AI只读证据/权限/预算与历史报告回归、期货live/国际金银规格币种及Windows发布回退；外部阻塞继续如实披露。核心端到端和人工发布验收仍未完成，因此保留原续做自动化。
