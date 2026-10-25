# 执行12在建状态：用户优先整改可见界面

**最新恢复指针：** UI第四轮后已恢复本包，原锁版引擎下载/隔离安装成功，真实Parquet与登记/来源读取/018 runner离线目标及回归87通过；见`docs/STOCK-COURSE-EXECUTION-12-RESUME-2026-10-25.md`。下文“未安装/没有绿测试/没有runner”为中断时的历史事实，不能当当前状态。真实PG/迁移/生产任务接线/本人仍未验证，新入口未挂，尚不能称D4完成；最新1145清单与补充SHA见恢复记录accepted版本。

**实施中，未完成验证**。用户中断下载/列式工作后要求先分析登录背景与tick移植，随后授权新的可见界面计划。此记录防止续做把以下文件遗漏或误报完成。

已新增但未接生产：`backend/requirements-stock-columnar.txt`、`backend/app/schemas/stock_columnar.py`、`backend/app/models/stock_columnar.py`、`backend/app/services/stock_research_v2/columnar_store.py`、`columnar.py`、`backend/migrations/20261024_018_stock_columnar_datasets.sql`、`backend/tests/test_stock_columnar_store.py`、`test_stock_columnar_registry.py`。UI前清单`logs/stock-ux-20261024-before-inventory.json`已纳入其中5个被原guard追踪的源码/迁移；其他文件同样保留，不覆盖。

初红4存储/3登记测试因缺模块失败已记录，后续源码写入后没有成功安装PyArrow，没有运行绿测试。官方PyPI已核实26.0.0/Apache-2.0/CPython3.12 Windows wheel及SHA，依赖审计见`logs/stock-course-execution-12-dependency-audit.json`；OSV连接失败，不能标扫描通过。uv独立target下载长时间未完成，仅停止本任务对应uv进程；直接分段下载一次SSL失败、一次110秒有界预算超时，原日志保留。`logs/stock-columnar-qa`未安装有效引擎，不把JSON/mock当作真实Parquet验证。

无018迁移runner/实PG验证/任务集成/API挂载/本人验收；没有将列式能力标enabled。现有代码仍需先补迁移静态/实库校验和实际Parquet链路测试，再讨论部署。零数据/模型调用，未改旧期货或共享配置。后续优先顺序以`docs/superpowers/plans/2026-10-24-stock-visible-workspace-remediation.md`和最新UI交付为准，不重复盲下载或扩大数据采集。
