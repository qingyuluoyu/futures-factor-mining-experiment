# 课程执行11：股票任务硬资源边界

**已实现待验证**，D4整体仍部分完成。按执行10依赖推进三个内部工作包：硬提交内存/进程数限制；股票进程启动与监督接入；异常关闭/父进程退出/句柄回收。不修改期货或共享生产代码，不把资源测试当作完整课程验收。

## 白名单、实现与金融口径

新增`backend/app/services/stock_research_v2/windows_job_process.py`、`backend/tests/test_stock_dataset_windows_resources.py`；修改股票独立`backend/app/services/stock_research_v2/dataset_worker.py`。其他源码不变，AGENTS/master仅追加检查点。

Windows 10+使用标准库ctypes调用原生Job Object，无新增依赖。默认单任务进程树总提交内存512MiB、每进程同上、最多4个活动进程；内部预算校验允许128–2048MiB、1–4进程，bool/非法数额拒绝。该限制是提交内存，不等于RSS/工作集；512MiB尚未证明足以承载五年全量研究，不通过放宽次数或伪造数据掩盖超限。

采用CreateProcessW的PROC_THREAD_ATTRIBUTE_JOB_LIST在创建时原子绑定已设置限制的匿名Job，未采用创建后再附加的窗口。只继承stdin/stdout/devnull三个明确句柄，Job句柄不继承、不设置breakaway。Job设置kill-on-close，禁止降级到不受约束的Popen；不支持的平台或原生配置失败时执行失败，新公开入口继续关闭。不是通用沙箱：不限制网络/文件访问权限、磁盘容量或CPU速率；旧300秒监督、源文件容量和固定计算边界仍须同时保留。

参考官方契约：[JOB_LIST与句柄列表](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-updateprocthreadattribute)、[提交内存定义](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_extended_limit_information)、[Job生命周期与进程树](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)。没有为此修改运行配置或升级框架。

父级股票launcher改用受限进程适配器，保持原DatasetClaim/IPC及监督契约。管道使用有缓冲二进制流处理完整写入。terminate/kill作用于本Job全部进程；根进程退出时终止剩余子进程，避免子孙持有管道阻塞回收。close限时等待根进程和活动进程清零，释放Job/进程句柄及管道，幂等。显式终止失败仍由finally关闭Job并等待根进程；launcher即使监督stop抛出也进入Job关闭并收集发送任务异常，不泄漏未处理的发送Task。

没有改变因子公式、PIT/复权/原始精度、观察数据资格、账本、用户/JWT盘或任务幂等口径。内存不足当前沿原安全失败码处理，不声称UI已能细分所有资源失败。未挂新入口、未生成真实研究结果。资源适配器只操作自己创建的匿名Job，不枚举或终止其他服务。

## 实际验证

以下Python统一`backend/.venv/Scripts/python.exe -X utf8`，项目根执行：

```text
python -m pytest backend/tests/test_stock_dataset_windows_resources.py -q
python -m pytest backend/tests/test_stock_dataset_windows_resources.py backend/tests/test_stock_dataset_execution_pipeline.py -q
python -m pytest backend/tests/test_stock_dataset_windows_resources.py::test_launcher_closes_native_job_even_when_supervisor_stop_raises -q
python -m pytest backend/tests/test_stock_dataset_windows_resources.py backend/tests/test_stock_dataset_execution_pipeline.py backend/tests/test_stock_dataset_claim_execution.py backend/tests/test_stock_dataset_process_supervisor.py backend/tests/test_stock_research_worker_process.py backend/tests/test_gp_process.py backend/tests/test_stock_dataset_jobs_api.py backend/tests/test_stock_research_v2_jobs.py backend/tests/test_stock_research_v2_job_migration.py backend/tests/test_stock_research_job_service.py backend/tests/test_stock_research_archive_v2.py backend/tests/test_ai_trading_mode_isolation.py backend/tests/test_market_history_isolation.py -q
python scripts/check-stock-b33-preservation.py verify logs/stock-course-execution-10-current-inventory.json
python scripts/check-stock-b33-preservation.py snapshot logs/stock-course-execution-11-final-inventory.json
python scripts/check-stock-b33-preservation.py verify logs/stock-course-execution-11-final-inventory.json
```

初红7缺模块，`logs/stock-course-execution-11-resources-red.log`。首次6通过/1失败，`...-resources-first.log`；原用例误以为Python进程树仅2个，本机venv redirector导致4个。调整为与真实部署兼容的2–4个边界，保留真实子进程PID/原生句柄终止断言，没有跳过测试或放宽内存约束。接入包先红1缺launcher导入/9通过，`...-integration-red.log`；首次目标22通过，`...-first.log`。

一次自审补充原生终止失败路径，先红1/10通过，`...-cleanup-red.log`，修复close后全回归116通过，`...-regression-final.log`。同次自审核对launcher外层finally发现监督stop异常仍可能绕过close，新增目标先红1，`...-launch-cleanup-red.log`，随后修复并重跑受影响集合。最终**117通过、0失败、0跳过，188条既有utcnow弃用警告，27.69秒**，`...-regression-final-v2.log`；此前116记录保留，不能当最后状态。

12个新增用例包括真实128MiB Job拒绝256MiB bytearray分配且峰值未越界、创建开始即在Job内、预算非法不启动、真实子孙进程终止、监督失租回收、QA父进程os._exit(77)后Job子进程退出、原生属性配置失败不启动、股票launcher失败关闭Job、显式终止API故障仍关闭/回收、监督stop故障仍关闭Job。仅QA短命进程；不是PG跨进程成功建集或真实证券数据质量证明。

只读socket检查3001/8002/8110/5433/6380本轮均未监听，`...-runtime.json`；未推断关闭原因或启动任何服务。未执行原PG、在线HTTP/浏览器、新迁移应用、完整生产构建/发布回退、真实五年性能与本人验收。

## 保护、兼容、成本与回退

执行10清单1128中，只有声明的dataset_worker.py变化，其他1127 SHA不变；新增一个股票生产模块。字节备份`...-backups.json`和bin；最终精确差异`...-worker-final.diff`、断言`...-preservation-verified.json`。新最终清单1129为`logs/stock-course-execution-11-final-inventory.json`，原guard verify通过，`...-preservation-verified.log`。较早`...-current-inventory.json`为自审修复前中间清单，保留证据，**后续必须使用final-inventory**，不得覆盖旧baseline。

无Git提交/分支、数据库结构/迁移/依赖/旧API变更，无源或模型新增调用，外部费用0。没有恢复paid AI、前后端/PG/Redis/connector/常驻worker。015/016/017仍未应用，v2组件/API未生产挂载。

回退只精确撤销本轮股票launcher接入、保留原观察归档和表；若撤掉资源边界，必须维持v2新任务关闭，不能回退成无约束上线。不得用整体备份覆盖期货或其他修改。未来启用后需先停止新任务接收，确认本Job树退出再移除调用；不触碰其他系统进程。

人工路径待D4完整接通：本人股票盘确认数据→归档任务→取消/超时/资源失败无迟到completed且任务槽释放；跨盘不可见。现阶段不提供未接通页面让用户“验收”。原PG并发、父子数据事务恢复、真实来源资格、孤儿文件GC、真Parquet和ML仍未闭合；不能称系统完成。

下一四包：真Parquet引擎独立锁版/许可证/环境兼容与有界分区；固定列式产物登记读取/缺口资格；原PG私有迁移及完整IPC成功/续租取消/崩溃恢复；门禁通过后接生产Flag/API/组件与生命周期。再推进自定义因子IC、R3/R4可成交回测/基准和必做本地LightGBM。
