# 课程执行10：D4观察任务IPC与父级发布接线

**已实现待验证**。本轮三个内部工作包：可执行观察子程序与有界IPC；父级续租/独立核验/发布/失败收尾；显式持有租约的单次领取消费。仅新增两股票生产文件和一测试文件，1126既有保护SHA全部不变。没有生产挂载、迁移应用或服务重启；未把离线链路视作已上线。完整D4仍部分完成。

## 实际文件与数据流

- `backend/app/services/stock_research_v2/dataset_process.py`：`python -u -m app.services.stock_research_v2.dataset_process`读最多512KiB的DatasetClaim JSON；重复键、额外字段、过期/超300秒期限拒绝。产物根仅由父级环境指定，必须已存在且不是链接路径，不自动替代目录。使用原loopback/5433配置与NullPool独立连接，5秒连接/30秒命令边界，复用执行09真实归档构建。IPC仅返回不超过4096字节的观察引用或固定脱敏错误；独立305秒watchdog、不迁移、不拉取数据、不调用模型。
- `backend/app/services/stock_research_v2/dataset_worker.py`：父级异步写管道与原观察监督并行，避免输入管道阻塞逃过监督；继承原Python解释器，固定模块和工作目录，不接受用户命令。监督超时/失租结束并回收直接子进程。`execute_claim`每次续租/结果发布/失败/释放使用独立事务和5秒边界；先续租再运行，之后由原`finish_dataset`重新读取内容与来源，并在原claim fencing下提交completed。不能只信子进程给出的引用。取消后的迟到引用不能覆盖cancelled；父任务关闭记录worker_failed并继续抛出取消。数据库失败不返回伪成功，失败收尾失败则依赖原租约恢复，不无限重试。
- 同一新模块`run_dataset_once`：调用者必须明确提供已取得的WorkerLease；只续租→claim→commit→执行一个任务，不自行获取/抢占租约、不建立常驻循环、不接入main生命周期。无任务立即返回False，不轮询付费能力。
- `backend/tests/test_stock_dataset_execution_pipeline.py`：12个目标用例，真实临时归档与SQLite元数据检验父子协议处理函数/发布/取消/失败/释放；真实QA短命CLI验证非法输入和缺根退出，不访问PG。不使用假金融结果证明回测正确。

金融口径保持原观察JSON、原始Decimal字符串、固定来源/参数/版本、owner/JWT盘隔离；没有增加研究或可成交资格、没有金融公式变化、没有把JSON称作Parquet。子程序构建commit后才发引用；父级completed独立提交，二者之间故障可能留下私有归档但任务未完成，不能称为单一跨进程原子事务。原幂等归档可复用，同一任务不自动重试。运行期取消、进程终止或DB失败可能产生未登记文件；权限仍依赖登记，GC另包。

## 实际命令和结果

Python统一为`backend/.venv/Scripts/python.exe -X utf8`，工作目录为项目根。

```text
python -m pytest backend/tests/test_stock_dataset_execution_pipeline.py -q
python -m pytest backend/tests/test_stock_dataset_execution_pipeline.py backend/tests/test_stock_dataset_claim_execution.py backend/tests/test_stock_dataset_process_supervisor.py backend/tests/test_stock_research_worker_process.py backend/tests/test_gp_process.py backend/tests/test_stock_dataset_jobs_api.py backend/tests/test_stock_research_v2_jobs.py backend/tests/test_stock_research_v2_job_migration.py backend/tests/test_stock_research_job_service.py backend/tests/test_stock_research_archive_v2.py backend/tests/test_ai_trading_mode_isolation.py backend/tests/test_market_history_isolation.py -q
python scripts/check-stock-b33-preservation.py verify logs/stock-course-execution-09-current-inventory.json
python scripts/check-stock-b33-preservation.py snapshot logs/stock-course-execution-10-current-inventory.json
python scripts/check-stock-b33-preservation.py verify logs/stock-course-execution-10-current-inventory.json
```

第一红的巨大参数自动ID触发pytest路径错误，已用明确短ID修正，保留`logs/stock-course-execution-10-red.log`；纠正后10项因缺模块失败，`...-red-corrected.log`。首次实现10通过，`...-first.log`。追加单次队列包先红1项缺函数，另11通过，`...-queue-red.log`；首次全回归104通过/1失败，测试误用job_id字段，核对原契约后改为UUID(id)，未修改接口或放宽断言，`...-regression.log`。最终**105通过、0失败，180条既有utcnow弃用警告，16.09秒**，`...-regression-final.log`。

一次定向自审覆盖输入大小、过期、根目录、协议脱敏、受监督管道、取消/迟到发布、事务回滚及释放；没有再做全仓重写。成功归档链路测试使用注入的本地执行回调调用实际execute_payload/build_claim_dataset，不是完整真实PG跨进程成功验收。真实短命子程序仅验证拒绝路径与回收。

旧guard仅因声明新增2文件返回差异，`...-delta.log`；逐文件SHA和精确新增集合断言通过，`...-preservation.json`。证据辅助脚本首次因拼写utf8-sig失败，改为utf-8-sig重跑成功；未更改任何旧受保护文件。新清单1128原guard通过，`...-preservation-final.log`。AGENTS/master修改前备份在`...-backups.json`及对应bin，历史清单不覆盖。

## 兼容性、成本与剩余门禁

没有修改期货、共享认证/网关/布局/运行配置、旧股票API/引擎/任务、依赖或数据库结构。015/016/017依然未应用，新API/组件与执行器不挂生产，没有启动PG/Redis/前后端/connector/常驻worker，未恢复paused/user付费AI。外部数据和模型新增调用/费用均0；仅本地测试CPU、临时文件与短命QA进程。当前服务状态沿执行06停机证据，不假定在线或反复重启。

未执行：原PG真实迁移/跨进程成功/并发续租取消/崩溃恢复、在线HTTP、本人浏览器、真实数据资格认证、生产构建与发布回退、五年300股票性能、Parquet及LightGBM。监督具有时间/输出边界但尚无硬内存/任意子孙进程树隔离；现阶段禁止生产启用。父级结果校验同步文件IO仍可能占用事件循环；大数据量需移出主请求与验证锁时长。局部5秒超时不代替PG锁及隔离级别真实并发证明。

回退：新入口仍未挂载，无旧行为迁移；只撤销这两新增股票模块及未来精确调用，不能回滚整个仓库或删除归档。未来启用后先停止新接收、终止并确认新子进程结束，保留兼容015/016表/历史产物与旧v1；不停止期货。人工路径待资源/迁移门禁后：本人股票盘确认来源建集→运行→同版本观察结果；取消/源损坏/期限/断库无迟到completed，跨盘不可见。当前无公开入口，不要求用户测试尚未开放功能。

下一四包：硬内存及进程退出边界；真Parquet依赖锁定与有界分区；原PG私有迁移/续租取消/崩溃恢复及完整IPC成功验证；通过后接生产Flag/API/组件与生命周期。随后固定数据自定义IC、R3/R4回测/基准及必做LightGBM。不把新增worker模块数当用户可用进度。
