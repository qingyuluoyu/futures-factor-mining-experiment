# 课程执行05：D4独立任务基础收尾

## 状态和范围

**已实现待验证**。本轮承接上一次执行中被用户“完整运行”打断的三个内部子包，不另开总计划、不重复改已有期货。三包分别为独立任务契约/兼容迁移、私有幂等任务提交/取消、租约隔离与固定结果登记。12项目标测试、230项受影响回归通过；无公开入口、无真实PG迁移/并发验证、无实际后台执行器，因此D4整体仍实施中，不将内部基础当成用户已可用完整功能。

运行许可沿用户最新授权：原项目已启动，本轮核对3001/8002/8110/5433/6380监听仍存在，未重启或停止服务。AI付费任务保持此前暂停，不恢复模型调用。启动记录见`STOCK-RUN-2026-10-23.md`；master和旧检查点“不重启”为历史，不覆盖最新授权。但新015/016/017不随启动应用，v2不挂生产，不把监听证明为功能验收。

## 三包文件和数据流

1. `backend/app/schemas/stock_research_v2_jobs.py`、`backend/app/models/stock_research_v2_job.py`、`backend/migrations/20261023_016_stock_research_v2_jobs.sql`、`backend/scripts/migrate_stock_research_v2_jobs.py`：独立metadata和版本表，只有dataset kind，不接受ML/回测等尚未实现操作；用户/盘/幂等唯一键、状态/结果非空约束、索引。迁移默认只读check，apply必须明确指定；校验版本checksum、字段/默认/主键/检查/唯一/外键/索引/已验证约束。需已有users/015数据表，5秒锁等待/30秒SQL时限，无启动建表/历史回填。
2. `backend/app/services/stock_research_v2/jobs.py`：由服务端Scope约束用户和JWT盘；创建前需要有效内部worker租约和来源权限，事务内固定参数/方法指纹及来源元数据。相同键同参可在worker离线后查回原记录，异参409。每盘最多2个活动任务、全局16个活动任务是瞬时背压，不是日/月/累计次数限额。列表最多100，明确省略大参数且SQL列裁剪禁止隐式加载；详情保留固定参数。该列表为新内部接口，无旧客户端兼容变更。
3. 同模块的acquire/claim/renew/expire/cancel/fail/finish/release：PG事务advisory锁串行关键更新，任务只能执行一次；worker随机实例令牌、claim令牌、owner/mode、运行deadline和lease多重fencing。取消/过期不能发布completed；取消仍保留占用直到确认释放或租约失效，不立即启动重叠执行。旧租约失效后任务过期，不自动重排/重采。完成前验证真实归档manifest/内容、所属盘、精确固定参数/源指纹，仅允许observation_only；结果准备与调用者事务共同提交，不在服务内部commit。当前没有进程执行器，该注册表不是已启动worker。

新增测试`backend/tests/test_stock_research_v2_jobs.py`、`backend/tests/test_stock_research_v2_job_migration.py`。测试使用明确qa-only SQLite夹具及临时真实归档文件；不是PG并发证明或真实证券供源认证。日志只存request/job/user/mode/version和允许错误码，不记录源内容、参数或令牌；prepared_not_committed表示尚未提交，不伪称持久化成功。

## 实际验证

Python统一为`backend/.venv/Scripts/python.exe -X utf8`，命令如下：

```text
python -m pytest backend/tests/test_stock_research_v2_jobs.py -k list_does_not -q
python -m pytest backend/tests/test_stock_research_v2_jobs.py backend/tests/test_stock_research_v2_job_migration.py -q
python -m pytest backend/tests/test_stock_research_v2_jobs.py backend/tests/test_stock_research_v2_job_migration.py backend/tests/test_stock_dataset_catalog_api.py backend/tests/test_stock_dataset_qualification.py backend/tests/test_stock_research_panels_v2.py backend/tests/test_stock_research_archive_v2.py backend/tests/test_stock_point_in_time_v2.py backend/tests/test_stock_universe_v2.py backend/tests/test_stock_research_dataset_migration.py backend/tests/test_stock_custom_factors_api.py backend/tests/test_stock_custom_factors_migration.py backend/tests/test_stock_factor_expressions.py backend/tests/test_stock_provider_contract.py backend/tests/test_stock_research_artifact_store.py backend/tests/test_stock_metrics_v2.py backend/tests/test_stock_research_engine.py backend/tests/test_stock_research_regressions.py backend/tests/test_stock_research_dependencies.py backend/tests/test_stock_research_snapshots.py backend/tests/test_stock_report_artifacts.py backend/tests/test_stock_report_bookmarks.py backend/tests/test_ai_trading_mode_isolation.py backend/tests/test_market_history_isolation.py -q
python backend/scripts/migrate_stock_research_v2_jobs.py --help
python scripts/check-stock-b33-preservation.py verify logs/stock-course-execution-04-current-inventory.json
python scripts/check-stock-b33-preservation.py snapshot logs/stock-course-execution-05-current-inventory.json
python scripts/check-stock-b33-preservation.py verify logs/stock-course-execution-05-current-inventory.json
```

- 列表行为红1失败：原来返回/加载parameters；精确列裁剪与摘要后目标12通过，`logs/stock-course-execution-05-list-red.log`、`...-target-final.log`。12项36个既有utcnow弃用警告，未更改共享模型修无关警告。
- 上次实现红绿保留：最初7项missing modules；边界红4失败含真实SQL NULL可绕完成结果校验漏洞，修正结果hash/qualification显式IS NOT NULL；没有删测试或放宽断言。见`...-jobs-red.log`、`...-boundaries-red.log`、`...-target-first.log`。
- 受影响回归**230通过、0失败**，288个既有datetime.utcnow弃用警告，exit0，`...-regression-final.log`。
- 迁移`--help`成功，`...-migration-help.log`，没有运行apply/check连接原库。
- 旧清单verify返回1是五个声明新增，不是冻结文件漂移。精确白名单和旧SHA断言通过：1113个旧文件全部不变，五新增，无修改删除，`...-preservation.json`。生成新清单1118文件，再verify exit0，`...-preservation-final.log`；不覆盖执行04或任何历史baseline。
- 本轮未改前端，未重跑TSC/构建；上一运行构建成功是历史证据，不当本轮新验证。真实PG迁移、双连接claim/事务回滚、进程取消/超时/恢复、HTTP/本人浏览器和课程验收均未执行。

## 一次自审和风险

修复完成结果SQL NULL漏洞、列表大参数加载问题。数据库租约互斥需真实PG验证，SQLite不能证明。当前事务会持有advisory锁完成内容验证，最大源字节范围受已有归档约束；未来worker需隔离进程、父进程租约/超时/取消、短事务结果发布和实际内存/IO测量，不能直接把同步归档挂HTTP。没有尝试自动运行worker，也没有创建虚假的生产租约。

不改既有因子/IC/年化/费税/成交公式，新增workflow固定`stock-v2-dataset-job-v1`，观察manifest方法固定`stock-observation-manifest-v1`。来源声明仍只observation_only，不因测试通过升级历史/PIT/可成交资格。Parquet、真实时点数据认证、五年性能、自定义因子固定数据IC、可成交回测、LightGBM仍未交付。

未新增模型或数据调用、未安装依赖、未读/输出密钥、未写账户/交易、未提交/建分支。原运行后台可能按既有生命周期采集，不把原后台费用声称本轮全部外部调用为零；本轮主动新增外部预算使用0。

## 兼容、迁移、回退、人工路径

015/016/017仍未应用。016为追加空表/索引，不改旧生产表；当前尚无线上新增数据，不运行删表式回退。未来先在原PG私有schema验证015→016、重复apply/check、错误checksum/约束差异、事务失败/并发；通过后独立发布。仅启动授权不等于迁移应用授权，本轮未顺带迁移public。

回退保持旧API/前端；停止接受新v2任务并等待/取消及确认进程退出后恢复精确股票patch；保留新增表和归档、历史版本，不删除数据/恢复整库。不以回退本包为由停止原期货。

当前页面无新任务入口，故无可要求用户点击的新功能。允许后人工路径：本人登录选择所属盘固定数据版本→提交且刷新同键只一任务→跨盘不可见→取消后无迟到结果→版本/来源损坏明确失败；必须在真实PG/HTTP执行器接线及权限验证后才开放。既有预览仍按运行记录自行登录，不代填密码/密钥。

下一四包沿总包依赖：D4隔离Parquet依赖/真列式；D4有界子进程执行器与租约/取消/恢复及API/UI完整接线；015/016私有PG迁移/并发和来源/PIT资格验证；D3/R2固定数据自定义因子IC接线。随后R3/R4及必做LightGBM，不以本内部基础顶替。
