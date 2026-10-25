# 课程执行08：D4观察子进程监督前置

**已实现待验证**。顺序承接执行07，本轮三个内部子包为有界观察结果协议、真实子进程取消/超时回收、异常与续租失败脱敏。仅新增`backend/app/services/stock_research_v2/process_supervisor.py`和`backend/tests/test_stock_dataset_process_supervisor.py`。不是已实现完整worker，不是已启动服务；没有数据集执行子程序、DB租约编排、Parquet或生产挂载。D4整体仍实施中。

监督函数输入仅内部已拥有的Popen句柄、续租回调、有限0–300秒单次预算（拒bool/NaN/Inf/0）；复用旧股票supervise_process/stop_process只读能力，未修改其文件或期货GP。返回仅经过DatasetRef严格校验的observation_only引用，最多4096字节消息；未知字段、非法UUID/Hash、可成交资格、额外snapshot控制消息均拒绝。引用结构通过不是内容/权限/真实数据认证，未来父级仍必须调用finish_dataset核验归档及DB fencing，不能直接标completed。

有效续租必须返回严格True，失去租约返回None并回收子进程；超时504、不自动重启/重排。父任务取消传播CancelledError，但先回收拥有的子进程。成功也先停止/等待子进程并关闭管道再返回，防止发送结果后后台继续运行。来自执行器或续租回调的任意错误文本不透传，只返回允许的固定错误。没有通用用户命令/文件/交易工具入口。

测试启动的仅明确QA短命`python -c`进程（打印合成UUID协议或sleep），不连接数据库/网络，不启动项目worker/PG/Redis/前后端/connector；不是绕运行门禁。每条关键路径断言poll不为空，取消路径断言管道关闭。测试不涉及伪金融行情，未将QA引用登记为生产数据。

## 验证与一次自审

实际Python统一`backend/.venv/Scripts/python.exe -X utf8`：

```text
python -m pytest backend/tests/test_stock_dataset_process_supervisor.py -q
python -m pytest backend/tests/test_stock_dataset_process_supervisor.py -k heartbeat_failure -q
python -m pytest backend/tests/test_stock_dataset_process_supervisor.py backend/tests/test_stock_research_worker_process.py backend/tests/test_gp_process.py backend/tests/test_stock_dataset_jobs_api.py backend/tests/test_stock_research_v2_jobs.py backend/tests/test_stock_research_v2_job_migration.py backend/tests/test_stock_research_job_service.py backend/tests/test_stock_research_archive_v2.py backend/tests/test_ai_trading_mode_isolation.py backend/tests/test_market_history_isolation.py -q
python scripts/check-stock-b33-preservation.py verify logs/stock-course-execution-07-current-inventory.json
python scripts/check-stock-b33-preservation.py snapshot logs/stock-course-execution-08-current-inventory.json
python scripts/check-stock-b33-preservation.py verify logs/stock-course-execution-08-current-inventory.json
```

初红15失败为缺模块，`logs/stock-course-execution-08-process-red.log`；首次15通过，`...-process-first.log`。一次自审新增续租JobError泄漏反例，真实红1失败，`...-heartbeat-red.log`，改为固定错误后最终**88通过、0失败**，132条既有utcnow警告，exit0，`...-regression-final.log`。中间75和27项结果单列first/process-regression-final日志，不当额外独立覆盖数累加。

1124旧保护文件SHA均不变，一声明新增生产文件；测试另单列，`...-preservation.json`。旧清单verify返回1仅新增，完整新清单1125 verify exit0，`...-preservation-final.log`。文档指针修改前字节备份`...-backups.json`；历史baseline不覆盖。执行者核对仅root活动，无并行执行文件冲突。

## 风险、回退和下一包

无金融公式/数据/资格变化，无新增依赖/源调用/模型调用/迁移/账户写入/提交建分支，新增外部费用0。没有自动恢复服务或付费AI，执行06停机证据保持最新，不重复通知同一停机状态。

未执行PG/在线HTTP/本人浏览器/迁移应用与发布回退、真实数据集子程序或worker、Parquet/五年性能/ML。监督函数限制时间与输出，不等于内存限额或进程树隔离；继承旧单子进程监督机制，不认证任意派生孙进程管道场景。真正worker还需限制子程序不得派生后代、配置存储根/独立内存边界、缩短DB事务、固定来源验证、父级lease/结果发布和真实PG并发。未通过前不挂生产入口、不创建可接收任务的虚假租约。

当前文件无生产调用，回退无运行副作用；未来先关新接收并确认拥有的进程退出，再移除精确股票调用，保留表/产物，不停期货。人工路径待完整worker接通后：确认任务→取消/超时/重启→无迟到完成和孤儿进程；当前不能要求用户测试不存在的页面执行能力。

下一四包：真Parquet隔离依赖与分区；真实观察数据子程序及父级租约/结果编排；015/016私有PG迁移并发和来源资格；API/组件/Flag安全生产接线，再固定数据IC/R3/R4/必做LightGBM。01:00自动化已改用当前课程总包，保留原日程/ACTIVE/线程，不新增重复。
