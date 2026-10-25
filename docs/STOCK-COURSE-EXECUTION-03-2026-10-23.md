# 课程整改执行03：私有数据版本与历史时点（2026-10-23）

## 真实状态

**D2、D3部分完成。** 本轮按执行02检查点推进四个内部业务子包；没有增加另一套总计划，也没有把停机授权当开发停止。所有新能力仅内部调用，未挂HTTP/前端入口，不暴露未完成计算或空数据页。

1. **私有产物权限和登记恢复：已实现待验证。** 严格Scope→015迁移readiness→有界规范JSON摘要→用户/登录盘幂等锁→已有ArtifactStore原子不可变文件→同一事务登记→按所属用户/盘/UUID读取并重新核对SHA/大小/路径/状态。未登记孤儿不能读取，事务回滚后原键可重新登记同一内容；不能把物理文件存在当可用数据。SQLite/真实临时文件测试通过，真实PG并发/提交断连及部署文件系统未验证。
2. **来源修订与固定观察清单：已实现待验证。** 来源白名单Schema保留供应商/接口/授权与Schema引用、市场/币种/单位/时区/频率/复权锚点/报告/公告/可得/采集/披露版本信息，Decimal原字符串不截断。日线/估值/财务规范化批次追加保存，修订保留原批次；数据集指定原来源UUID与SHA，保存暖机/研究区间/固定证券池版本，旧来源不自动改最新；固定内容读取重新校验全部来源和关联。新manifest仅observation_only，不认证行情覆盖或回测资格。离线通过，真实供源/授权、原始HTTP响应全量归档与其他六类来源/Parquet未完成。
3. **财报时点选择：已实现待验证。** 所属财务来源读取→源SHA/字节数校验→转换为保留原始/修订引用的Disclosure→按报告期与真实可得时间取当时前沿。默认合并口径，母公司必须显式选择；精确时刻和仅公告日期分开。日期精度不足时必须提供真实日历，保守下一交易日09:30上海时间可用，不猜工作日；这是一条明确方法，不是假造源公告时刻。未来披露/晚修订不变过去信号，当前修订无原始版本证据拒绝、冲突拒绝，当前报告缺字段不借旧报告补造。内部归档到PIT有离线贯通测试；真实历史披露与修订证据、日历来源、D4任务接线未验收。
4. **历史股票池：已实现待验证。** 固定池版本/已知时间/左闭右开成员区间→明确覆盖检查→标准证券列表。退市/退出前仍在历史池，新上市/新成分未生效或当时未知不提前加入，退出须有当时已知的证据，不用现在名单覆盖历史。冲突区间/未知覆盖/版本不符拒绝，结果确定排序；不实现退市持仓现金结算或下单。源真实性/成员公告/交易日口径仍需真实证据，不能把测试名单称全部A股历史。

D1仍是执行01的请求/预算内部部分实现，R1/R2保留已有代码；D4及ML未开始。本轮没有推进自定义因子IC/组合回测，没有把定义编辑器伪装成可用研究。原17包范围及LightGBM必做不减。

## 文件与兼容性

新增7个保护范围文件，**没有修改任何既有生产源或前端文件**：
- `backend/app/schemas/stock_research_v2.py`：Scope/ArtifactRef/SourceRef/DatasetRef/SourcePage/SourceObservation/DatasetBuild严格契约。
- `backend/app/models/stock_research_dataset.py`：独立DatasetBase，四业务表与专用迁移版本表，不加入共享create_all。
- `backend/app/services/stock_research_v2/archive.py`：私有登记、权限、幂等/修订/固定清单、内容重校验、资源限制、request_id日志。
- `backend/app/services/stock_research_v2/point_in_time.py`：Disclosure/TradingCalendar、源指纹映射和确定性asof选择。
- `backend/app/services/stock_research_v2/universe.py`：已知时间/退出证据/成员区间/版本与覆盖检查。
- `backend/migrations/20261023_015_stock_research_datasets.sql`：兼容新增四业务表。
- `backend/scripts/migrate_stock_research_datasets.py`：显式check/apply、校验和/结构验证，默认check只读。

新增测试四个：`backend/tests/test_stock_research_archive_v2.py`、`backend/tests/test_stock_research_dataset_migration.py`、`backend/tests/test_stock_point_in_time_v2.py`、`backend/tests/test_stock_universe_v2.py`。只更新本检查点、根AGENTS续做指针、新主计划/数据子计划/旧主计划的真实状态，四个既有文档字节备份见`logs/stock-course-execution-03-backups.json`。

开工对执行02清单verify通过1097零漂移，活跃Agent库存只有本/root，没有重复调度旧子代理。完工对旧清单差异必须恰为7新增/0修改/0删除，原1097个保护文件全部SHA不变；旧清单不覆盖。新增完整清单1104并用原guard复核，证据`logs/stock-course-execution-03-preservation.json`、`logs/stock-course-execution-03-preservation-final.log`。期货/共享认证、网关、布局、运行配置及旧股票API/金融公式/报告均保持不变。

## 数据、资源与状态契约

- 仅接收**已经标准化**的Teajoin三类内部SourcePage，不是真实API适配器：不读取凭据、不解析网页、不出网。authorization_reference/source_schema_reference/vintage_reference是待核验的来源证据索引，不因填写了字符串就认证授权或历史真实性。
- normalization_version固定`stock-normalized-observation-v1`，产物Hash覆盖规范化JSON的真实字节；发布后新增字段/默认/口径不得直接改写v1序列化，须新版本及旧读取兼容，不能重写旧hash。
- 原始十进制字符串或显式null，不接受浮点/布尔猜数，未知单位/证券交易所/负量/价格/错误OHLC/未来锚点拒绝；缺量不补0、缺调整因子不补1，前复权必须明确锚点，同一manifest不混原价/不同锚点。
- 日期必须YYYY-MM-DD/date，时刻必须含时区的ISO/datetime并归一UTC，拒绝epoch单位推测、日期截断时刻、未来采集、报告期晚于公告/观察时间。登记created_at由数据库真实当前时间生成，不冒充历史采集时间。
- 文件利用原ArtifactStore单产物默认8MiB/上限64MiB；每SourcePage最多1万观察行，DatasetBuild最多1000来源/1万证券，实际来源合计最多64MiB/100万观察行。先查所属元数据/合计预算，再逐文件验证，不一次拼全市场DataFrame；超限422，不删原有研究面板保护。不把资源上限当五年性能证据。
- 私有文件按内容SHA去重，但引用/所有权/幂等按owner+JWT盘隔离，禁止用户路径/URL读取；没有公共发布或跨用户研究缓存。owner/mode必须由未来可信认证/任务上下文构造，不能从HTTP body直接信任Scope。
- 状态仅ready可读，quarantined拒读；无公开修改状态入口。临时写/不可变发布在前，元数据和来源/manifest关系在同一数据库事务里准备；日志明确prepared_not_committed，调用方负责整个事务commit，异常必须rollback。失败孤儿既不登记也不自动读/删除，原键有限人工/任务重试收敛；不把本地准备成功当外部提交已确认。
- 源重复原键同内容返回原引用，同键改内容/用途409；修订必须所属用户/盘、相同provider/kind/stream/单位/频率/口径/锚点，采集时间前进。物理SHA/大小/路径、源元数据、manifest关联与原版本都需一致，不能静默重建新版本代替旧文件损坏。
- 观察数据集版本=真实manifest字节SHA，清单包含有序来源ID/SHA/大小/行数/类别、研究与暖机日期、固定池版本和标准证券列表；不含随机request_id或当前时间。相同来源/参数原键返回相同DatasetRef，后续修订来源不会重定向旧数据集。
- 015的qualification约束刻意只允许observation_only；不能用户/管理员提交backtest_ready。未来D4需要单独有证据的资格计算和兼容升级迁移/报告，不直接修改已应用015或调高字符串“认证”。当前数据目录、缺失分层、真实可得覆盖和执行资格还未实现。

PIT方法`stock-disclosure-asof-v1`，日期精度政策`strict-next-session-0930-shanghai-v1`，历史池`stock-historical-universe-v1`。这些纯函数不计算收益、不代替中性化/财务单位核验/退市结算；归档映射校验Hash不等于检验证据引用对应的原始财报真实性。默认asof取合并口径、最新可得报告后取该期最新已知修订，同一最终前沿异值冲突拒绝；过时报告的冲突不按输入顺序随机影响当前前沿。保留数据原公告/可得/采集字段，不把推导的日期政策时间写回成真实公告。

## 实际命令和结果

Python统一`backend/.venv/Scripts/python.exe -X utf8`，最终执行：

```powershell
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_stock_research_archive_v2.py backend/tests/test_stock_point_in_time_v2.py backend/tests/test_stock_universe_v2.py backend/tests/test_stock_research_dataset_migration.py backend/tests/test_stock_custom_factors_api.py backend/tests/test_stock_custom_factors_migration.py backend/tests/test_stock_factor_expressions.py backend/tests/test_stock_provider_contract.py backend/tests/test_stock_research_artifact_store.py backend/tests/test_stock_metrics_v2.py backend/tests/test_stock_research_engine.py backend/tests/test_stock_research_regressions.py backend/tests/test_stock_research_dependencies.py backend/tests/test_stock_research_snapshots.py backend/tests/test_stock_report_artifacts.py backend/tests/test_stock_report_bookmarks.py backend/tests/test_ai_trading_mode_isolation.py backend/tests/test_market_history_isolation.py -q
backend/.venv/Scripts/python.exe -X utf8 backend/scripts/migrate_stock_research_datasets.py --help
```

**202 passed，192条现有User模型datetime.utcnow弃用警告；pytest exit0；CLI help exit0**，`logs/stock-course-execution-03-regression-final.log`、`logs/stock-course-execution-03-migration-help.log`。新目标35测试在最终代码上单独复跑通过（上述命令的前四个测试文件，`logs/stock-course-execution-03-target-final.log`），没有跳过项。

测试使用已有离线SQLite夹具+真实临时JSON文件，显式标QA来源/证据，未写public/停机数据库假行情。覆盖权限/盘/幂等、回滚孤儿/同键恢复、元数据路径CHECK拒绝、文件损坏/来源和manifest版本变更、Decimal精度、字段/单位/时序、原版本读取、PIT晚披露/修订/仅日期日历/未知原版/口径、成员上市/退出/覆盖/已知时间、PG DDL编译与独立元数据/检查表达式静态规范化。

红→绿日志保留：`...-red.log`初次缺PIT/universe模块的收集错误不算金融行为证明；`...-behavior-red.log`有真实数值类型及PIT最终前沿输入顺序/未来不可得原版误阻断的失败、缺迁移工具；`...-boundaries-red.log`有财报口径重复、epoch推测、非法未来锚点/报告期、旧数据集回执返回变更版本等失败，以及退出证据字段尚未支持导致的新契约失败；`...-time-red.log`有采集未来及epoch日期静默接受；`...-bridge-red.log`是原始披露证据契约和归档映射尚未支持。均通过对应最小实现修复，最终相关断言保留，没有跳过/删测试/放宽关键口径。

首次损坏测试尝试写`../outside.json`时，被SQLite真实CHECK先拒绝，最初测试未预期该约束而失败（`...-archive-first.log`）；改为同时断言数据库拒绝越界，再注入满足CHECK但Hash错误的元数据，验证文件读取仍409，未拆掉约束让测试过。

未执行：真实PG015 apply/check/重复应用/锁与并发/约束/事务断连/前向修复、真实源/授权/历史样本真实性对账、运行HTTP/本人浏览器、Parquet/五年性能/ML/完整构建。前端完全未改，本轮不重复运行其669历史测试/TSC，更不把执行02结果当本轮新验证。停机门禁没有借SQLite夹具或临时端口绕开。

## 迁移、回退、成本和剩余风险

015只创建四个空业务表与两索引，专用版本表由显式工具在同一事务内建立；不修改旧表/索引，不启动时迁移，不回填旧库。FK引用users和本包不可变引用，删除RESTRICT；私有幂等唯一约束、记录状态/用途/大小/观察资格约束。check默认READ ONLY，apply须显式，5秒锁/30秒语句预算，版本锁20261023015，校验和冲突/结构漂移失败；检查列/类型/空值/默认/PK/FK/唯一/命名CHECK/非部分索引/convalidated，PG等价反射仅静态测过，尚未真实PG认证。

获用户明确恢复许可后，先正常恢复/核实既有数据库，再在真实隔离schema完整验证工具函数和权限/并发/损坏/恢复；正式部署顺序是备份/配置产物根与私有权限→015显式应用/检查→后续D4接线/认证→灰度能力。`backend/.venv/Scripts/python.exe -X utf8 backend/scripts/migrate_stock_research_datasets.py --check`与`--apply`均**未执行**；独立私有验证驱动尚未创建，列为下一步，不借现有业务用户做假数据认证。

当前无挂载的新入口，回退无需碰旧服务；如未来接通后回退，关闭新任务/读取能力，等事务结束，保留所有引用表和产物，再按精确股票patch回退。已有新数据时不DROP表/整目录删除，不删旧文件来模拟回滚；文件孤儿处理须先明确任务/Hash归属并有受控前向核对，当前不自动扫删。物理硬链接/fsync在实际部署文件系统、磁盘容量/ACL、多进程并发、PG反射兼容、提交回执丢失仍需实测，不能以本机临时文件通过替代。

本轮未启动/停止/重启服务、未恢复付费AI、未改运行配置、未读取/展示密钥；源/业务模型调用0，新增这两类外部调用费用0，无依赖安装/批量导数据/实盘/模拟账户写操作。用户不重启门禁继续生效。12:10续做自动化仅更新提示以遵守新课程计划/最新检查点/期货冻结与门禁，名称/时间规则（含原COUNT）/ACTIVE/所属对话保留；项目未完成，未删除续做。

## 人工验收路径与下一四包

现在没有公开数据集按钮可验收，不要求用户重启或提供密钥。恢复获准后，先私有PG迁移及文件ACL/权限测试，再用已获授权且真实可追溯的小批数据：源接口/原始证据→规范化归档→源UUID/SHA/单位/时区/公告核对→固定manifest→源新增修订后再读旧manifest/PIT→跨用户/盘拒绝→旧期货只读回归。不能把QA字符串引用当真实授权/原始财报。

下一四包：
1. D2/D3收尾：按文档/授权/预算接真实来源证据、补缺口与所选字段资格分层；保留现有归档/PIT/universe，未知财报/成员/日历证据明确受阻。
2. D4：列式分区和有界Panel读取、锁定依赖/许可证、016异步任务/租约/取消/恢复；没有真实数据或worker时capability关闭，不在普通HTTP长计算。
3. R1/R2：固定DatasetRef/FactorRef、财报口径/方向/单位/正值域检查，复用原预处理/IC并保存结果与原引用；完整后再开自定义研究配置与历史恢复。
4. 交付门禁：补私有PG验证驱动与故障恢复/鉴权测试代码，获恢复许可后实际运行015/017/HTTP/本人浏览器及回退；随后继续R3/R4与M1-M4必做ML，不以本轮内部模块代替完整课程验收。
