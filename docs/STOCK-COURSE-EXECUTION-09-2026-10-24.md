# 课程执行09：D4固定claim观察构建

**已实现待验证**。三个内部子包：固定claim与真实归档构建、确定性幂等结果登记、失效/损坏/提交失败不发布。新增`backend/app/services/stock_research_v2/claim_execution.py`与`backend/tests/test_stock_dataset_claim_execution.py`；原股票、期货及共享文件不变。不是完整worker：没有可执行IPC子程序/父级租约循环/内存边界/部署入口；API和组件仍未挂生产，015/016/017未应用，Parquet/ML未实现。

## 数据流与一致性

`build_claim_dataset(session_factory,store,claim)`重验DatasetClaim和016结构，先按owner/JWT盘/worker与claim token/deadline/固定参数检查fencing，失效立即rollback返回None，尚不读取文件或建集。ArchiveService复用现有来源权限/指纹/精度/锚点校验，任务源元数据与固定快照必须一致；以job_id作本任务构建幂等键，原request_id贯穿，实际写规范JSON观察manifest和归档登记，再完整读取核验。

返回前第二次fencing，失效则rollback；commit成功后才返回DatasetRef和记录observation_registered_not_job_completed。不调用finish_dataset、不把任务标completed；未来父进程仍需在原租约内独立验证归档并原子发布结果。没有把任务UUID当dataset_id、没有自动换最新来源或修复损坏文件。

事务为本函数新建独立会话所有，BaseException包括取消均rollback后继续抛出；不在长文件IO期间取得任务全局advisory写锁，避免把父级续租直接挡在同一锁上。归档锁沿已有用户/盘策略。最后检查与commit不是“绝无竞态”的证明；取消后即便形成私有观察登记，也必须由父级finish fencing阻止任务完成。PG并发/隔离级别仍需实测。

数据库失败或迟到失效时，不返回引用、不授权未登记文件；已原子生成但回滚的内容寻址文件保留，不删除可能共用的源/文件。孤儿文件容量清理仍需按登记、Hash及任务归属独立实现，不把遗留文件当有效数据，也不声称存储GC已完成。

## 实际验证

以下Python实际统一为`backend/.venv/Scripts/python.exe -X utf8`：

```text
python -m pytest backend/tests/test_stock_dataset_claim_execution.py -q
python -m pytest backend/tests/test_stock_dataset_claim_execution.py backend/tests/test_stock_dataset_process_supervisor.py backend/tests/test_stock_research_worker_process.py backend/tests/test_gp_process.py backend/tests/test_stock_dataset_jobs_api.py backend/tests/test_stock_research_v2_jobs.py backend/tests/test_stock_research_v2_job_migration.py backend/tests/test_stock_research_job_service.py backend/tests/test_stock_research_archive_v2.py backend/tests/test_ai_trading_mode_isolation.py backend/tests/test_market_history_isolation.py -q
python scripts/check-stock-b33-preservation.py verify logs/stock-course-execution-08-current-inventory.json
python scripts/check-stock-b33-preservation.py snapshot logs/stock-course-execution-09-current-inventory.json
python scripts/check-stock-b33-preservation.py verify logs/stock-course-execution-09-current-inventory.json
```

初红3项缺模块，`logs/stock-course-execution-09-claim-red.log`；首次3通过，`...-claim-first.log`。一次自审新增迟到fence/提交失败回滚两测试，最终受影响**93通过、0失败**，152条既有utcnow弃用警告，exit0，`...-regression-final.log`。包括旧股票子进程和GP过程回归；未修改其代码。5个新用例构建真实临时文件并核验内容/DB登记，包含取消/伪造token不写文件、损坏源不建集、相同claim复用同一结果但任务仍running。迟到fence和commit失败通过故障注入覆盖，不是实PG并发认证。

数据为显式qa-only SQLite夹具，不进入生产，不代表真实证券数据/API授权/PIT或复权认证。原close高精度字符串在rollback后仍保持一致，未伪造新价量或用默认值替代失败。

旧清单verify返回1仅一声明新增生产文件，精确白名单断言1125旧SHA不变，`...-preservation.json`；完整新清单1126原guard verify exit0，`...-preservation-final.log`，测试单列。文档指针改前字节备份`...-backups.json`，旧baseline不覆盖。

## 成本、门禁、回退与后续

没有新金融公式、资格升级、依赖/外部源/模型调用、迁移、账户交易或提交建分支，新增外部费用0。未启动PG/Redis/前后端/connector/worker、未恢复付费AI；原停机证据不变，不再重复提醒。测试仅运行隔离QA和之前明确短命子进程回归，不用它冒充生产运行。

未执行：PG真实迁移/并发/恢复、在线HTTP/本人浏览器、完整生产构建/发布回退、实际IPC与父级worker/Parquet/五年性能/ML。本函数同步文件IO仍需执行08进程监督和硬资源控制包承载，不能从普通HTTP直接调用。当前无公开引用，不影响旧用户；未来回退先关闭新接收、确认任务/子进程退出，移除精确股票调用，保留兼容表/产物，不删源数据或停止期货。

人工验收待完整执行器接通：本人股票盘确认固定来源→任务归档→父级发布同版本结果；取消/超时/源损坏/提交失败无迟到completed；跨盘不可见。现在页面没有该执行能力，不要求用户验证不存在的入口。

下一四包：IPC观察子程序与父级lease/发布编排；硬内存/进程资源和真Parquet；015/016私有PG迁移并发/来源资格；生产Flag/任务API与组件安全接线，然后固定数据IC/R3/R4/必做LightGBM。15:30自动化已更新当前总包和真实运行边界，保留日程/ACTIVE/线程。
