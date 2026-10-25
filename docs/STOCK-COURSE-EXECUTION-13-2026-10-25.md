# 执行13：固定claim的列式执行与父级发布接线

**已实现待验证**。沿执行12恢复后的D4继续三个内部切片：固定claim内JSON父归档与真实Parquet同事务登记；显式列式IPC与原有资源监督；父级核验列式结果后发布/取消/释放。没有挂生产API/页面、常驻生命周期或应用迁移，不能称完整生产worker或A股课程已可用。

## 修改和内部契约

修改`backend/app/services/stock_research_v2/`下`claim_execution.py`、`columnar.py`、`dataset_process.py`、`dataset_worker.py`、`process_supervisor.py`；新增同目录`columnar_process.py`。新增`backend/tests/test_stock_columnar_execution_pipeline.py`与`test_stock_columnar_process_supervisor.py`。原jobs.py、Claim/Job Schema、016/018 SQL及ORM、前端、期货、共享认证/网关/布局/运行配置均未改。

- `build_claim_dataset(factory, ArtifactStore, DatasetClaim, columnar_store=None)`默认仍返回原DatasetRef；显式ParquetStore时先检查018/引擎，再在同一调用方事务内登记父清单和列式分区，以claim.job_id作幂等键。初始与提交前fencing、固定sources校验均保留；失租/提交失败回滚两套登记，不返回成功receipt。返回ColumnarRef仍只表示登记成功，**不标job完成**。未登记内容文件可能留下，未实现GC。
- `execute_payload(..., columnar_root=None)`复用512KiB严格JSON输入/重复键拒绝/固定Claim/≤300秒deadline/已存在非链接root/原PG5433与NullPool；显式列式入口才读取`WENYUAN_STOCK_COLUMNAR_ROOT`。旧dataset_process入口不因环境里存在此变量自动改模式。新columnar_process仅调用固定`main(columnar=True)`，任意输入文本不能选择模块、函数、路径或执行工具。stdout仍为≤4096字节Schema结果/脱敏错误，watchdog305秒。
- `run_dataset_once(..., columnar_store=None)`仍只消费已显式获取的租约、单次领取，不启动循环或抢租约。列式模式018/根/锁版引擎未就绪时，不把pending领成running。显式列式launcher复用原Windows Job原子启动、512MiB提交内存/4进程/超时/取消回收；只在列式子进程环境设置OPENBLAS/OMP/MKL/NUMEXPR线程各1，不改父进程、旧JSON模式或期货环境。
- supervisor显式columnar=True才解析ColumnarRef，拒绝旧结果类型、额外控制字段及非observation_only资格；默认仍按DatasetRef解析。父级在发布事务内用当前Scope重新读列式登记，核验数据/来源/Parquet内容及`expected_idempotency_key=claim.job_id`，再交原finish_dataset双fencing，完成原DatasetRef结果并释放槽位。取消、损坏或错误任务receipt不能变completed。

这是同一固定数据的额外物理表示；旧任务/API的结果字段和manifest方法语义不变。没有新增“用户请求必须列式”的持久任务参数，旧JSON租约/旧completed任务**不能直接宣称列式就绪**。未来公开入口必须独立检查实际列式receipt、引擎/迁移/worker能力和资格；本轮仍保持未公开，不用旧available=true放行。

只保存规范化原观察，不计算新因子、不填缺值、不混复权、不改披露时点/财报对齐。数据继续observation_only，Parquet不是PIT/五年/回测资格证明，未改变v1结果或金融方法。

## 实际验证及失败记录

先读最新AGENTS/总包/D4/执行12恢复及主+补充SHA，只有root执行者。修改前1145项主保护与6项补充SHA通过，保存8项字节备份后登记范围。所有测试均离线，不启动原项目服务。

backend目录中进程局部`PYTHONPATH=(Resolve-Path ../logs/stock-columnar-qa).Path`的实际命令：

```text
.venv/Scripts/python.exe -X utf8 -m pytest tests/test_stock_columnar_execution_pipeline.py -q
.venv/Scripts/python.exe -X utf8 -m pytest tests/test_stock_columnar_execution_pipeline.py tests/test_stock_dataset_execution_pipeline.py tests/test_stock_dataset_claim_execution.py -q
.venv/Scripts/python.exe -X utf8 -m pytest tests/test_stock_columnar_execution_pipeline.py tests/test_stock_columnar_process_supervisor.py -q -s
.venv/Scripts/python.exe -X utf8 -m pytest <logs/stock-course-execution-13-test-files.log的实际文件清单> -q
```

首次9项红（未接参数/列式入口），`logs/stock-course-execution-13-red.log`；初实现26目标通过，`...-first.log`。一次自审发现直接execute_claim使用默认runner时没有携带columnar_store，且函数默认值提前绑定旧launcher；补用例先红1/10通过（`...-default-red.log`），改为调用时解析显式模式。红测试拦截任何真实启动，未连接停机PG；其中release_failed是预期测试失败时的本地清理日志，不是线上任务事故。

原生Job测试先因QA launcher漏传env失败，补齐显式env和QA载荷；随后未约束线程的实际Job子进程exit1（`...-native-accepted.log`、`...-native-diagnostic.log`），没有据此伪称已确认唯一底层原因。限定线程后的受控实验实际通过（`...-native-threads.log`），生产列式launcher线程策略用例先红“9而非1”（`...-thread-policy-red.log`），再显式落地。**没有提高512MiB/4进程上限或放宽关键断言**。QA诊断只记录阶段/异常类型，后续移除异常原文输出。

新增18项覆盖真实Parquet+登记与父发布、双登记回滚、018缺失不写/不领取、幂等、错误job绑定、取消/损坏不完成并释放、提交失败、默认入口选择、单次消费后空队列、根非法/真实CLI拒绝、受限原生Parquet短进程及协议越权拒绝。数据库链路使用既有SQLite测试factory；IPC成功通过注入该factory在进程内执行真实业务函数；**不是原PG跨进程成功验证**。真实短命原生进程只验证Parquet及IPC/资源边界，receipt为明确QA协议载荷，不作为已授权数据库结果。

最终**147受影响后端通过、0失败、0跳过，284条既有utcnow弃用警告，14.01秒**，`...-regression-accepted.log`。覆盖现有JSON/claim/任务API和目录ASGI/资格/窗口资源/迁移静态及新列式链；不是已部署HTTP验收。另在**默认无PyArrow的venv**执行旧JSON管线+监督28通过，`...-default-json.log`。最终原生4项通过，`...-native-final.log`，QA实际峰值提交内存100,315,136字节、上限536,870,912、进程上限4、结束active_processes=0；只证明这个小QA负载，不宣称全市场任务容量/五年性能。

默认环境命令（无target PYTHONPATH）：

```text
.venv/Scripts/python.exe -X utf8 -c "import importlib.util; assert importlib.util.find_spec('pyarrow') is None"
.venv/Scripts/python.exe -X utf8 -m pytest tests/test_stock_dataset_execution_pipeline.py tests/test_stock_dataset_process_supervisor.py -q
```

没有重跑前端/TSC/生产构建，本轮未修改它们。未执行原PG迁移应用/反射/并发/跨进程成功、生产生命周期、原HTTP/本人浏览器、真实历史数据/金融样本、五年性能、发布回退验收。没有用SQLite或短命QA CLI替代这些门禁。

## 保护、成本、回退与下一包

最新主清单`logs/stock-course-execution-13-final-inventory.json` **1146**，原1145中仅声明5股票文件变化，其它**1140旧SHA不变**，新增1股票入口模块，无删除。`...-supplemental-sha.json`在原6项完整不变基础上加入两个新测试，共8项，须与主清单一起核验。字节备份`...-backups.json`、精确差异`...-changes.diff`、保护断言`...-preservation.json`及`...-final-guard.log`保留，未用未知漂移重设基线。前缀`...`均为`logs/stock-course-execution-13`。

实际根目录命令：

```text
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b33-preservation.py verify logs/stock-course-execution-12-resume-accepted-inventory.json
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b33-preservation.py snapshot logs/stock-course-execution-13-final-inventory.json
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b33-preservation.py verify logs/stock-course-execution-13-final-inventory.json
```

原3001/8002/8110/5433/6380仍未监听，`...-runtime.json`。只启动并回收QA短命子进程，未拉起原PG/Redis/前后端/connector/常驻worker、应用015–018或恢复paused/user AI；未新安装依赖/下载数据/调用模型，新增外部费用0。02:00/07:05既有自动化保留，07:05提示已同步最新队列及执行12真实恢复事实，未重复创建。

回退只撤销五股票文件的列式可选分支及新入口引用，保留原JSON路径/已存父清单和列式产物。未来运行后须先停接新列式任务并确认本Job树结束再回退，不降成无资源约束运行，不删除表或旧结果。当前没有生产迁移/数据副作用需要回滚。

下一四包：①原PG私有迁移/并发/完整真实IPC（需新运行许可，不能自动启服务）；②股票独立capability/worker生命周期及Flag/API/页面接通，不能把旧JSON租约当列式能力；③固定数据PIT/历史池/所选自定义FactorRef依赖与真实IC/报告；④可成交回测基准/复现及必做本地LightGBM。未具备运行许可时继续可安全离线部分，保持未验收入口关闭，不能再把单个内部模块当完整系统。
