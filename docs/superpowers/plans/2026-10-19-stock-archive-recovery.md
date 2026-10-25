# 股票影子归档受控恢复 Implementation Plan

> 执行executing-plans/test-driven-development/dispatching-parallel-agents/verification-before-completion。保留已有修改，无Git，不提交/分支。

**Goal:** 落实05:38交付下一包1：失败/取消归档由真实管理员显式、幂等、有次数上限地恢复，不改原005终态、不盲目重试或重调供应商。

**Architecture:** 新006独立恢复审计表，原005保留一次执行记录；内部CLI默认只读预检，显式apply创建恢复记录running，执行004归档及completed同事务；失败/取消终态另存，原数据不回写。管理员JWT使用原认证路径，不能只输入admin用户ID冒充授权。不开新普通用户按钮/HTTP写入口、不影响原worker读取路径。

**Tech Stack:** 原PG/SQLAlchemy/Pydantic/pytest，无新依赖，0来源/模型预算；07:10不采集今天未收盘daily。

## 包1：006兼容扩展（agent）

新增`backend/app/models/stock_archive_recovery.py`、`backend/migrations/20261019_006_stock_archive_recovery.sql`、`backend/scripts/migrate_stock_archive_recovery.py`、`backend/tests/test_stock_archive_recovery_migration.py`。独立RecoveryBase、StockArchiveRecoveryVersion表stock_archive_recovery_schema_versions(version64/checksumCHAR64)、StockArchiveRecovery表stock_archive_recoveries。

字段：id UUID PK、source_job_id UUID、source_batch_id UUID、source_data_version CHAR64、method_version VARCHAR64、operator_id UUID、idempotency_key UUID、request_hash CHAR64、request_id VARCHAR32、reason VARCHAR160、attempt_number INTEGER、status VARCHAR16 running/completed/failed/cancelled、step VARCHAR32 recovering/completed/failed/cancelled、started_at/updated_at/deadline_at TIMESTAMPTZ、finished_at nullable、archive_batch_id nullableUUID、error_code nullableVARCHAR64、error_message nullableVARCHAR300。其他nonnull。唯一operator_id+idempotency_key，唯一source_job_id+attempt_number；attempt_number1..2，deadline>started、updated>=started、finished null或>=started；status/step配对、running无finished/archive/error，completed有finished/archive无error，failed/cancelled有finished/无archive且error_code非空。索引source_job_id+started_at、status+deadline_at。不跨外部metadata FK、不改004/005 SQL/model/Base，严格迁移SHA/结构/默认/约束/索引/PG目录有效性；借鉴005的::text修复。

- [x] 红绿及相邻回归；check不创建缺表，事务锁/超时；agent源文件冻结后root真实PG。agent未公库apply/启停/访问密钥；root验证后显式006兼容扩展。

## 包2：管理员预检与幂等恢复（root）

新增`backend/app/schemas/stock_archive_recovery.py`、`backend/app/services/stock_archive_recovery.py`、`backend/tests/test_stock_archive_recovery.py`、`scripts/recover-stock-archive.py`及CLI测试。Schema：source_job_id UUID、expected_data_version64小写hex、idempotency_key UUID、reason8..160 trim严格extra forbid，不接受自由SQL/来源地址/凭据。

`inspect_recovery(session, request, *, operator_id, now)`：核实DB中active admin；仅原005 failed/cancelled（running即使过期也拒绝，交后台期限核查）；核实来源ID、来源当前版本=原任务版本=显式预期、方法版本，重新prepare_archive严格价量/原hash/时点，确定性坏数据不可重试。只读预检不扣次数、不造终态。返回数量/版本/风险/已有记录状态，不返回原reason/用户凭据。

`run_recovery(factory, request, *, operator_id, now=None)`：短事务锁原005行并核实admin/current source，幂等键同用户同输入复用，异输入409，不重复执行已运行/完成/失败的同key；source_job最多2条恢复记录，跨管理员/进程同一source_job锁序列化。领取running/90秒deadline提交后60秒内执行，事务锁恢复行+源版本，调用archive_eod；新影子与恢复completed同事务。失败/取消另存终态，严格取消语义包含claim提交退出/保存失败。不UPDATE原005或原EOD/目录/旧历史/账户，不自动retry。

CLI：仅local5433，必填全部Schema参数，JWT仅从固定环境变量STOCK_ARCHIVE_RECOVERY_TOKEN读取，不经命令行/日志；使用原get_auth_context解码access token+账户状态及role，禁止refresh token/冻结/非admin。默认READ ONLY dry-run；显式--apply才check003/004/005/006及写，30秒外围/SQL5秒/锁2秒，不隐式迁移。无token在DB访问前拒绝，失败只输出固定代码/错误类型，不打印SQL参数/reason/token。仅内部管理员入口，本轮不伪装为普通用户功能。

- [x] Schema/认证/权限/源状态/版本/时点/幂等/次数/取消先红后绿；普通用户不能恢复全局任务，历史任务不可被新状态覆盖。

## 包3：真实PG、受限公开验证与交付

新`scripts/test-stock-archive-recovery-local.py`：私有schema克隆users（独立ID非序列）、迁移003/004/005/006；真实admin/nonadmin/frozen/token、并发同key/不同key、上限2、事务失败/取消/期限/源版本变造、原005终态与原EOD/公共股期货数据聚合不变、清理。核心SQL不能mock。

- [x] 专项/完整后端、独立审查、真实私有PG通过后本地公库显式006扩展check（空表），不人为造失败任务试写生产。
- [x] 真实既有qa_账户access JWT在CLI拒绝非管理员（不打印token/凭据），无token拒绝，当前正常HTTP仍可用；不伪造公库失败记录作为成功证明。
- [x] 新API/前端未改，不重跑无关UI/截图；记录未执行真实公库失败恢复/因子锚点/收盘/AI/发布边界。回退停CLI并保留006及恢复审计，不删表、不改原workerflag，本轮无需重启。
- [x] 保留02:05/07:10续做，整体部分完成；完整证据与下一四包见`docs/STOCK-ARCHIVE-RECOVERY-VERIFICATION-2026-10-19.md`。

## 07:27执行补充

复审发现的过期/完成提交竞争已用真实PG屏障先红后绿修复：只有过期候选才锁恢复行并populate_existing，锁后重判completed，不让普通running查询阻塞执行。最后一次过期记录在显式apply时先持久failed，再commit后返回recovery_limit；保持总次数2，无第三次。原_claim内部契约为(record,claimed,rejection)，deferred拒绝不进入失败保存。

最终2216后端通过/3既有skip/502warnings、专项221-W error、真实PG45/HTTP13；006 SHA=`2840347f4e7caa5304d4b14fdb60f7fb0ce536777de14242364c0ddd2f8a69aa`，本地公库仅空恢复表。源码/真实命令/失败修复/迁移回退/未验收边界均记录完整交付；0上游0模型，不把本轮内部恢复等同全系统完成。
