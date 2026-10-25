# 执行16：019私有机器学习实验历史登记

状态：**已实现待验证**。本包新增019兼容迁移、本人/盘实验登记与模型历史只读服务；原PG、正式任务/HTTP/本人浏览器及发布链仍未验收。没有启动服务、应用迁移、下载依赖、调用金融供应商或付费模型。

## 实际文件与数据流

本包只新增：

- `backend/migrations/20261025_019_stock_ml_registry.sql`
- `backend/app/models/stock_ml_registry.py`
- `backend/app/schemas/stock_ml_registry.py`
- `backend/app/services/stock_research_v2/model_registry.py`
- `backend/scripts/migrate_stock_ml_registry.py`
- `backend/tests/test_stock_ml_registry.py`
- `backend/tests/test_stock_ml_registry_migration.py`
- 本文档。

没有修改015–018、旧训练/预测/成交核心、期货及共享认证/配置。API/UI由本轮其它独占工作包接入，本文只记录019服务及其离线验证。

内部入口 `ModelRegistryService(ArchiveService).register_experiment(*,experiment_sha256,idempotency_key,request_id)`：复用归档服务的当前Scope、Session、ArtifactStore和owner/mode事务锁；核验报告规范JSON/SHA、固定DatasetRef本人归属及实际来源文件、参数冻结、显式时序fold、实际原生模型SHA/锁版运行环境/manifest、预测SHA及完整测试交易日。全部1–5fold经核验后用一个019记录一次flush；事务由调用方commit/rollback，失败不返回部分登记。相同owner/盘/幂等键同SHA复用，不同SHA返回409。登记只保存有界JSON元数据与内容地址，不拷贝模型文本或训练矩阵进表。

只读契约：`available()->bool`、`list(*,limit=20,offset=0)->ModelPage`、`get(UUID)->ModelDetail`、`read_report(UUID)->ModelReport`。分页最大100、offset最大100000。Summary包括id、DatasetRef、ready/quarantined、qa_only/research_only、UTC创建时间、fold数量、特征名称和始终false的holdout标记；Detail另有固定参数、各fold的TimeSplit/ModelManifest（运行版本、特征重要性、训练知识截止）、报告SHA和限制。

list用单个count/page快照查询019，加一次批量join015 Dataset/Artifact验证本人/盘、版本、observation_only资格及ready状态；get使用相同元数据授权。不会逐模型展开数据来源、Parquet或导入native库。数据库登记改变归属/版本/隔离后立即拒绝对应读取，不能以本地SHA代替DB授权。read_report先按登记ID和015权限验证，再核报告大小/SHA及与登记snapshot一致；不评价、不写状态或消费事件。登记大小超过8MiB时在文件读取前明确409 `model_report_requires_export`，元数据仍可读。较大内部报告的同键重试仍可验证复用，不受HTTP交互预览限制。隔离记录可看元数据，报告读取409。

## 金融口径和一次自审

没有改变金融公式、训练参数、现有数据资格或历史结果。研究来源仍为未认证观察数据，只能登记QA模型；SHA只表示内容一致，不能认证样本真实、PIT正确或最终测试未见。本包没有最终测试封存/重叠消费账本，不能因复制Dataset/改参数/客户端sample SHA获得独立样本外认证。模型/报告继续 `holdout_registry_verified=false`，限制明确列出来源、历史池、经济标签及可实盘资格未验收。

自审红绿修复：JobError继承ValueError，身份404原先被通用异常转换成409，已保留专门异常分支；即使重新计算fold SHA也不能把嵌套holdout改true，不能删除整测试交易日后重新计算预测SHA伪装完整结果；固定版本报告/窗口严格字段集合拒绝夹带native模型文本；8MiB预览限制不再破坏大内部报告重复登记幂等性。没有通过放宽关键断言、删测试或跳过核心native训练来通过。

## 实际命令与证据

仓库根目录，进程局部绝对overlay，不改默认期货venv：

```powershell
$env:PYTHONPATH = (Resolve-Path logs/stock-ml-qa).Path + ';' + (Resolve-Path backend).Path
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_stock_ml_registry.py backend/tests/test_stock_ml_registry_migration.py -q
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_stock_ml_registry.py backend/tests/test_stock_ml_registry_migration.py backend/tests/test_stock_research_archive_v2.py backend/tests/test_stock_catalog_pagination.py backend/tests/test_stock_ml_dataset.py backend/tests/test_stock_ml_split.py backend/tests/test_stock_ml_train.py backend/tests/test_stock_ml_predict.py -q
```

最新目标：**10通过，0失败/跳过，7.00秒**，`logs/stock-ml-registry-16-final-targeted.log`；包含真实LightGBM4.7.0两fold训练、实际原生产物保存/登记/读取、跨用户/盘、参数冲突、损坏模型/预测、事务回滚、资格伪造、元数据批量读取、超预算预览及迁移静态检查。SQLite注入Session仅验证离线业务，不冒充原PG并发或生产事务验收。

范围回归最新 **51通过，0失败/跳过，17.50秒，64条既有utcnow弃用警告**；证据使用 `logs/stock-ml-registry-16-verified-regression.log`，覆盖上述8个实际测试文件。较早`...-regression.log`因误写不存在的`test_stock_dataset_catalog.py`而无测试运行，未当成功证据；更早50和51项日志保留，最新以verified为准。SQL静态测试核对完整ORM约束、DDL只新增独立表/索引、检查模式默认只读、015前置校验、019全结构/校验和/Advisory锁及超时、原loopback PG5433限制、无public硬编码、--help无需PG/native。

未执行：原PG5433实际check/apply/并发/故障恢复、生产资源监督中的正式登记任务、原HTTP/本人浏览器、真实市场数据/PIT/日历/历史池/经济标签认证、完整五年/性能、期货在线回归、发布/回退演练。没有付费模型或外部数据调用，新增外部费用0；本地QA有有限CPU/临时磁盘成本。GET不训练、不调用模型、不新增测试消费记录。

## 迁移兼容、人工路径与下一包

019仅新增`stock_ml_experiments`、`stock_ml_registry_schema_versions`及scope索引。runner复用原连接工厂，默认只读check；显式apply也不自动修复/应用015，检查015checksum后在019专用事务锁内执行有界新增DDL，核全部列/类型/default/主键/唯一/check/FK/index/已验证约束再登记checksum。没有启动自动应用，未获本轮运行许可不要执行apply。

回退：关闭新模型历史能力/调用方，保持旧训练/期货/015–018和019历史表、模型内容文件不变；不DROP已有登记，不删除原生产物。新增表在已有019数据时保留，后续迁移采用前向修复。临时QA失败仍可能留无登记内容地址文件，不授权公开读取，GC另包处理。

人工可在离线测试中复跑真实两foldQA，检查Detail参数/重要性/知识截止/UTC创建时间，再检查report原始evaluation为null、holdout始终false、其它用户及live盘404，修改报告文件后409。正式用户页面和原PG路径由根的联合包给出，不把当前SQLite/测试响应称本人验收。

下一轮四包：019原PG迁移/并发/异常恢复；固定数据与训练接016有界持久任务及能力；真实来源/经济标签与最终测试封存消费审计；原HTTP/本人页面/期货兼容及发布回退验收。



## 最新补充：原生解析协议修复（接受证据）

根集成审查指出原`load_experiment_model`/`restore_ranker`仅验证SHA、Scope和运行版本，不代表实际native模型可解析。本包随后真实复现：将已有两foldQA报告第一个模型文本改为`not-a-lightgbm-model`，同步原生manifest的model_sha、报告内manifest、saved_model内容地址及实验报告SHA后，旧019路径确实接受。红证据`logs/stock-ml-registry-16-native-parser-red.log`，**1失败，DID NOT RAISE**；不以“内容自洽”当作真实模型认证。

冻结前8文件已字节备份至`logs/stock-ml-registry-16-native-parse-backup/`，清单`...-backup.json`。本次仅修改019的`model_registry.py`、`test_stock_ml_registry.py`及本文。注册路径在既有SHA/Runtime验证后，调用锁版LightGBM4.7.0的真实`Booster(model_str=...)`进行解析，检查原生特征数、明确f000/f001等列名、每轮1模型、num_trees/current_iteration与persisted trees_used完全一致、dump_model的objective实际为lambdarank、原生保存参数num_threads实际为2。每模型原文仍受既有8MiB预算、每实验最多5fold约束；逐fold解析，不训练/推理，不增加无界native循环或公共POST。native解析异常只返回脱敏409，不返回原模型文本。

实测`Booster(model_str=...,params={'num_threads':2})`在当前版本告警“Ignoring params argument”，因此最终实现不传被忽略参数，不声称其生效，而核原生保存的线程参数。真实最佳验证前缀重载后以`current_iteration==num_trees==trees_used`校验，不能假设重载Python对象保留best_iteration标记。`list/get/read_report`路径没有新增native导入/解析，原批量015授权和8MiB交互预算保持。

目标测试另外验证了同步重hash的错列名、假树数、线程参数改8、目标改regression都被拒绝，数据库仍无部分登记；实际两fold正常模型可登记。线程预算伪造红证据`logs/stock-ml-registry-16-native-threads-red.log`保留。首次解析目标绿`...-native-parser-green.log`含上述被忽略参数告警，仅作历史；最终接受证据不含该告警。

最终实际命令（同上绝对overlay）：

```powershell
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_stock_ml_registry.py backend/tests/test_stock_ml_registry_migration.py backend/tests/test_stock_ml_registry_api.py backend/tests/test_stock_ml_registry_mount.py -q
```

**21通过、0失败/跳过，11.59秒，63条既有utcnow警告**，`logs/stock-ml-registry-16-native-parser-accepted-api.log`；包含019目标、真实ASGI/JWT盘隔离和挂载回归。此前10目标/51范围是native协议修复前历史，不能作为最新代码的全部回归声称。本轮按根要求仅重跑019/API相关目标，没有无理由重跑全库。

状态仍**已实现待验证**。本地native解析不等于正式ResourceJob任务资源门禁验收，更不等于原PG/生产worker/真实数据/测试封存或本人页面验收。本补充没有启动服务/应用迁移/修改旧训练核和依赖，没有外部调用和付费费用。精确回退只能基于本补充字节备份与patch核对用户后续修改；不回退到允许自洽伪native的未修协议后开放新注册。
