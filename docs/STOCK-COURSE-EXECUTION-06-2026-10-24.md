# 课程执行06：D4任务HTTP内部接线

## 状态与边界

**已实现待验证**。承接执行05，不重写因子/回测或期货。本轮三个内部工作包：任务读取/能力查询、幂等提交/取消、响应状态机/失败回滚。新增router未生产挂载，未提供UI、未启动执行器，不能称用户可用任务链路或D4完成。当前新数据仍observation_only，不允许据此运行完整新IC/回测/ML。

本轮确认五个原端口均未接受连接：3001、8002、8110、5433、6380；证据`logs/stock-course-execution-06-runtime.json`，核验时间2026-10-24T06:32:24Z。原因未确认，不猜测数据损坏或代码缺陷；依用户约束不自动重启，也不另起数据库/服务绕过。10月23日启动成功是历史，不能作为当前在线证据。未读取/写生产库，未应用015/016/017、未恢复付费AI。

## 文件与链路

- `backend/app/api/stock_dataset_jobs.py`：独立股票router，复用原认证和stock market guard，Scope仅从AuthContext产生。GET `/api/stock/research/v2/jobs/availability`、`/jobs`、`/jobs/{id}`；POST `/datasets/jobs`、`/jobs/{id}/cancel`。新建需app.state.stock_v2_jobs_enabled严格True且有效worker/迁移/来源；默认关闭。关闭接收后仍可查询取消既有任务，无模型/数据库高权限工具入口。
- `backend/app/schemas/stock_dataset_jobs_api.py`：分页默认20最大100、offset最大100000、拒绝额外参数；空取消体拒绝owner/mode等附加字段。响应显式区分无大参数列表摘要与固定参数详情，限定dataset kind/方法/流程版本、时间时区、结果资格、pending/running/completed状态与次数/时间/结果关系，不能把伪completed/null当成功。
- `backend/tests/test_stock_dataset_jobs_api.py`：六项ASGI链路/响应边界测试，使用真实业务JobService+独立SQLite事务/临时归档，不使用mock结果冒充核心业务。人工注册的租约仅QA夹具，不是已运行生产worker。模拟超时/损坏响应仅用于失败边界测试；写响应损坏导致异常，依事务回滚后GET total=0。

请求超时8秒，独立request_id/no-store/nosniff头、结构化401/409/422/429/503/504、日志只记录请求/用户/盘/动作/耗时和异常类型；不回显异常文本/密钥/源数据。超时提示凭原幂等键查询，不自动重试写请求。首次创建返回202；重复同键返回原记录，响应头为当前请求ID，记录request_id保留首次请求ID。路由没有自行commit，原get_session事务负责提交/回滚，Schema验证在正常返回前执行。

尚未公开挂载，原stock_research_v2只读router及所有旧调用方保持不变。未来挂载写router前必须独立检验路由冲突、原认证事务、配置门禁、worker和本人UI；不能只include_router后宣称产品完成。

## 命令与验证

Python命令实际使用`backend/.venv/Scripts/python.exe -X utf8`：

```text
python -m pytest backend/tests/test_stock_dataset_jobs_api.py -q
python -m pytest backend/tests/test_stock_dataset_jobs_api.py backend/tests/test_stock_research_v2_jobs.py backend/tests/test_stock_research_v2_job_migration.py backend/tests/test_stock_dataset_catalog_api.py backend/tests/test_stock_dataset_qualification.py backend/tests/test_stock_research_panels_v2.py backend/tests/test_stock_research_archive_v2.py backend/tests/test_stock_point_in_time_v2.py backend/tests/test_stock_universe_v2.py backend/tests/test_stock_research_dataset_migration.py backend/tests/test_stock_custom_factors_api.py backend/tests/test_stock_custom_factors_migration.py backend/tests/test_stock_factor_expressions.py backend/tests/test_stock_provider_contract.py backend/tests/test_stock_research_artifact_store.py backend/tests/test_stock_metrics_v2.py backend/tests/test_stock_research_engine.py backend/tests/test_stock_research_regressions.py backend/tests/test_stock_research_dependencies.py backend/tests/test_stock_research_snapshots.py backend/tests/test_stock_report_artifacts.py backend/tests/test_stock_report_bookmarks.py backend/tests/test_ai_trading_mode_isolation.py backend/tests/test_market_history_isolation.py -q
python scripts/check-stock-b33-preservation.py verify logs/stock-course-execution-05-current-inventory.json
python scripts/check-stock-b33-preservation.py snapshot logs/stock-course-execution-06-current-inventory.json
python scripts/check-stock-b33-preservation.py verify logs/stock-course-execution-06-current-inventory.json
```

初红4失败为API模块不存在，`logs/stock-course-execution-06-api-red.log`；首次实现4通过，`...-api-first.log`。边界红1失败/5通过：不完整running响应被接受；新增响应运行记录校验后完整受影响**236通过，0失败**，308条既有datetime.utcnow弃用警告，exit0，`...-regression-final.log`。没有删/跳测试或放宽金融/权限断言。ASGI测试不等于原网络HTTP/PG或本人页面验证。

保护旧清单verify返回1仅两声明新增；精确断言1118旧保护SHA均不变，`...-preservation.json`。新清单1120文件verify exit0，`...-preservation-final.log`。测试另单列，不改期货/共享代码。AGENTS/master改前字节备份与SHA见`...-backups.json`，历史清单/备份未覆盖。

未执行：真实PG迁移/并发/跨进程回滚，原HTTP/JWT/浏览器、前端/TSC/构建、实际worker超时杀进程、Parquet/五年性能、供源真实性认证和ML训练。服务不可用时不制造这些验收结果。

## 自审、金融口径与回退

一次自审补上写结果Schema失败回滚测试、running记录校验；关闭新建后保留取消，避免Feature Flag关闭导致用户无法停止已有任务。未更改金融公式或旧研究资格，观察数据不能被升格。无迁移/回填/新增依赖/账户交易或数据模型外部调用，新增预算消耗0；未提交/建分支。

目前只增加两个生产保护文件、一个测试文件，无线上挂载，无需线上回滚；后续若公开，关闭新建Flag、保留读/取消、确认worker/任务退出，再恢复精确股票挂载patch，不删新表/归档、不重置数据库、不停止原期货。DB兼容迁移仍依执行05，仅增加空表，未经验证不应用public。

人工验证路径尚待接通：本人登录股票→所属JWT盘任务页→固定来源提交/同键刷新→查看摘要与固定参数→切换用户/盘不可见→关闭接收后仍可取消→损坏/超时只结构化失败无假完成。现在无生产入口可点击，不要求用户代测不存在功能。

下一四包：真Parquet隔离依赖与分区；有界子进程worker/取消恢复；015/016原PG私有schema迁移并发验证和真实源资格；任务前端/生产Flag安全接线。随后固定数据自定义IC、R3/R4及必做LightGBM。06:10自动化指针已更新到现课程总包，保留原每日06:10/线程/ACTIVE，不新增重复自动化。
