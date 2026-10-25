# 股票影子归档受控恢复：2026-10-19 07:27 检查点

## 状态与边界

本轮三个工作包已实现并通过本地隔离链路验证：006兼容扩展、管理员内部恢复CLI、真实PG/HTTP及审查。**全系统仍部分完成，不是正式上线验收通过。** 当前没有真实失败的公库005任务，故未对公库执行恢复写入；没有伪造公库失败任务作为验证证据。

先读了用户AGENTS、原扩展计划、05:38交付、直接相关模型/服务/认证/迁移/测试；保留原修改。新计划为`docs/superpowers/plans/2026-10-19-stock-archive-recovery.md`。本轮没有Git提交/分支、依赖更新、服务重启、行情下载或模型调用。

## 实际文件

- `backend/app/models/stock_archive_recovery.py`：独立RecoveryBase与恢复审计模型，不进入原共享Base。
- `backend/migrations/20261019_006_stock_archive_recovery.sql`：新版本表/恢复表、双唯一、次数1–2、状态/结果/时间约束和有界查询索引。
- `backend/scripts/migrate_stock_archive_recovery.py`：事务迁移、锁/超时、SHA与严格结构校验，check不建表。
- `backend/app/schemas/stock_archive_recovery.py`：UUID、64小写hex、严格8–160字符理由、禁止额外参数。
- `backend/app/services/stock_archive_recovery.py`：权限、来源重校验、只读预检、幂等领取、事务归档、取消/失败与过期显式对账。
- `scripts/recover-stock-archive.py`：默认只读、必须显式apply、管理员access JWT固定环境变量接入、仅本机5433、脱敏输出。
- `backend/tests/test_stock_archive_recovery.py`、`backend/tests/test_stock_archive_recovery_migration.py`：15项服务/CLI契约与63项迁移测试。
- `scripts/test-stock-archive-recovery-local.py`、`scripts/test-stock-archive-recovery-http-local.py`：真实私有PG与原服务同源HTTP/实际CLI验证。
- 本交付、上述新计划以及`docs/superpowers/plans/2026-10-17-stock-market-expansion.md`续做索引。

## 数据流与金融口径

1. 操作员经原认证取得access JWT，只从`STOCK_ARCHIVE_RECOVERY_TOKEN`读取；不接受命令行token或裸admin UUID冒充认证。数据库再次校验active/admin；冻结、普通用户、伪造角色claim、refresh/过期token被拒绝。
2. 显式指定原005任务、来源预期版本、幂等UUID和理由。仅当前归档方法版本且failed/cancelled可恢复；running即使已超时也不由该入口改原005。
3. 当前EOD版本必须与原005及显式预期一致，日期匹配，再经004完整hash/价量/标识/单位/收盘时点校验。确定性坏快照在扣次数前拒绝，不允许更换来源或自由SQL。
4. 锁原005行序列化领取；同操作员同幂等键同请求只复用，不再执行；异请求409。每个原任务所有管理员合计最多两条恢复记录，不重置原attempts=1。
5. 领取running独立短事务提交；执行事务中锁恢复行和来源，将004影子归档与006 completed一起提交。失败/取消另存终态；未提交领取不伪造失败记录，晚到取消不覆盖已提交completed。
6. 过期同key只报告expired，不自动重跑；显式新key可将已超时旧恢复记录对账为failed，再在剩余次数内执行。最后一次已过期时，先提交该终态再返回recovery_limit，绝不创建第三条。无后台自动恢复/自动付费重试。
7. 期限90秒、执行60秒、SQL5秒/锁2秒；CLI外围30秒，因此CLI超时会先取消执行并尝试持久化终态。异常输出与日志不包含理由、token、原SQL异常或连接凭据，仅固定错误码/类型与request_id。

本轮没有改变价格、成交量、复权、财报、账户或撮合口径；仍为原价Decimal影子表。归档时刻不等于历史数据可得时刻，未知availability仍为空；不补factor=1/volume=0，不截断小数手。原005/EOD/旧股期货历史/用户读路径不回写。旧585392条/300只qfq仍未完成历史证据与锚点认证。

## 实际执行与结果

|实际命令（仓库根目录）|结果及证据|
|---|---|
|`backend/.venv/Scripts/python.exe -W error -m pytest backend/tests/test_stock_archive_recovery.py -q`|先11个缺模块红；最终15 passed。CLI无token在settings/DB之前拒绝。|
|`backend/.venv/Scripts/python.exe -W error -m pytest backend/tests/test_stock_archive_recovery_migration.py backend/tests/test_stock_archive_jobs_migration.py backend/tests/test_stock_daily_archive_migration.py -q`|148 passed（006的63项含严格漂移/事务/check无DDL）；独立agent实施，root又执行合并专项。|
|`backend/.venv/Scripts/python.exe -W error -m pytest backend/tests/test_stock_archive_recovery.py backend/tests/test_stock_archive_recovery_migration.py backend/tests/test_stock_archive_worker.py backend/tests/test_stock_archive_jobs_migration.py backend/tests/test_stock_daily_archive.py backend/tests/test_stock_daily_archive_migration.py backend/tests/test_stock_daily_archive_cli.py -q`|**221 passed，3.06秒，-W error**；`logs/stock-archive-recovery-focused-final-20261019.log`。|
|`backend/.venv/Scripts/python.exe -m pytest backend/tests -q --junitxml=logs/stock-archive-recovery-backend-final-20261019.xml`|**2216 passed，3既有skip，502既有warnings，60.35秒，exit0**；同名前缀log/xml。未删除测试、扩大skip或压制警告。|
|`backend/.venv/Scripts/python.exe -W error scripts/test-stock-archive-recovery-local.py`|**45项真实隔离PG检查通过**；`logs/stock-archive-recovery-pg-final-20261019.log`，JSON `logs/stock-archive-recovery-pg-20261018-232545-41ae01e8.json`；私有schema已清理，选定公库聚合/摘要前后相同。|
|`backend/.venv/Scripts/python.exe -W error backend/scripts/migrate_stock_archive_recovery.py`|专用本地5433显式apply成功；`logs/stock-archive-recovery-migration-apply-20261019.log`，仅新增空006表和版本记录。|
|`backend/.venv/Scripts/python.exe -W error backend/scripts/migrate_stock_archive_recovery.py --check`|重复只检查成功，applied=false；`logs/stock-archive-recovery-migration-check-20261019.log`。|
|`backend/.venv/Scripts/python.exe -W error scripts/test-stock-archive-recovery-http-local.py`|**13项通过**，原3000同源两QA用户真实登录、实际CLI非admin拒绝、no-store/追踪、无公库写；`logs/stock-archive-recovery-http-final-20261019.log`、`logs/stock-archive-recovery-http-20261018-232506.json`。|

45项PG包括：003–006 apply/check、真实JWT解析/过期/refresh/冻结、三个无权限身份、真正READ ONLY dry-run、六并发同key只一条、异body冲突、跨管理员总上限、原005终态不变、SELECT1/0后影子事务回滚、执行取消/领取提交前后取消/提交后取消、过期显示与显式恢复、过期与完成提交的锁竞争、最后次数过期对账、当日未收盘拒绝、实际CLI私有schema dry-run/apply/角色伪称与裸UUID拒绝。测试中故障/合成行情仅在私有schema，不进入用户数据；核心SQL和迁移未用mock替代。

失败与修复真实保留：

- PG初两次脚本分别因私有enum未建、session提交换连接丢search_path失败；已改私有enum和固定connection生命周期，无public fallback，finally清理。第三次因原worker正确拒绝创建当日未收盘测试任务失败，修正为私有异常输入fixture。不是生产功能失败被隐藏。
- 独立审查发现P2：过期候选在旧执行完成提交后仍用旧running对象更新，触发结果约束。真实PG commit屏障与`pg_stat_activity`确认锁等待，先红`IntegrityError`，后改过期候选`FOR UPDATE`并`populate_existing`，锁后读取completed再拒绝；非过期running仍立即409，避免不必要锁等待。红日志`logs/stock-archive-recovery-expiry-race-red-20261019.log`，绿日志`logs/stock-archive-recovery-expiry-race-green-second-20261019.log`。
- 审查指出最后一次崩溃可能停留running；先红断言，后让过期终态先提交再返回次数上限409。日志`logs/stock-archive-recovery-final-expiry-red-20261019.log`与`...-green-20261019.log`。仍不自动执行第三次。
- 第一HTTP脚本误读状态字段为顶层covered_count，实际是coverage.quoted；依据现有服务契约修正测试，未改API或放宽关键覆盖断言。原失败报告`logs/stock-archive-recovery-http-20261018-232445.json`，最终13通过报告另存。
- 独立复审重新跑78专项通过，核对两组PG红绿与全部三元_claim调用方，无剩余确认的重要项。

## 当前原服务与真实数据

07:25实查：3000 listener7268，8002 listener10784，PG5433 listener3532，桥8110 listener14236；未启停。`/health` HTTP200/status=ok，**market.live.ok=false，virtual.ok=true**；不能把HTTP200说成所有期货行情可用。

HTTP及PG实查：原005任务1条、004批次1条/5548行、006恢复0条；Oct16快照目录5558，报价5548，缺10、隔离未知19，原daily响应证据false。没有Oct19今天收盘任务；07:25尚未收盘，本轮没有请求当日daily。原qfq旧历史只覆盖300只，不能称全市场历史覆盖。

两用户正常页面入口没有变化，未覆盖原用户会话。没有前端代码改动，所以本轮未重新跑Node/tsc/浏览器截图/生产构建；05:38的304 Node与tsc仅为上轮证据，不冒充本轮执行。模型成功、真实盘后自动采集、全历史真实性、金银/期货live、Windows发布产物和人工上线验收未执行或未通过。

## 兼容、迁移与回退

006 SHA256=`2840347f4e7caa5304d4b14fdb60f7fb0ce536777de14242364c0ddd2f8a69aa`；004=`b2f7c69d76d48c715d1572f9094a41563ecd7bf11cf607b26890cb332a729f07`、005=`a3f59406504f788a67bfed1e5109b26a1305092e64c0974bf50898878f84cdb0`与原值一致。006不锁旧大历史表、不历史回填、不改旧默认/字段；未来应用此迁移后才能启用内部apply。当前后台worker不依赖006，因此原服务无需重启。

回退：停用新恢复CLI，保留006及审计；不DROP、不改原005终态、不删已成功的004归档，不切换用户历史读路径。已运行的恢复先取消并检查持久终态；崩溃running按deadline报告expired，需要管理员显式apply对账，不自动重跑。旧应用忽略006即可继续；若结构漂移新建后续前向修复迁移，禁止修改已应用006。私有schema迁移/重复检查/故障回滚已验证，公库破坏性回退未执行。

## 人工验证入口、成本和下一轮

内部操作员先通过正常认证取得真实admin access JWT并在受控环境设置`STOCK_ARCHIVE_RECOVERY_TOKEN`，不要在聊天或命令参数中发送token。使用真实failed/cancelled任务的UUID与原数据版本，先执行：

```powershell
backend/.venv/Scripts/python.exe scripts/recover-stock-archive.py --source-job-id $JobId --expected-data-version $ExpectedVersion --idempotency-key $RecoveryKey --reason '核查存储故障恢复后由管理员显式执行'
```

检查dry_run来源/数量；确认后相同参数追加`--apply`。成功后检查004关联与006 completed，同时原005 failed/cancelled仍保留；同key只复用原结果、不同body409、最多两次。当前公库只有completed原任务，不应为了演示手改其状态；普通QA账户实际CLI必须403。用户可打开原`http://localhost:3000/market`/工作台看仍然真实的盘后覆盖披露，但本轮无新普通用户恢复入口。

成本：0上游请求、0付费模型、0真实交易；新增少量本地审计存储及数据库校验，不增加后台调用频率。计划内0预算没有超支。02:05/07:10续做已通过工具查看并保留，没有因本轮测试通过停止长期修复。

下一轮四包：

1. 恢复审计的授权只读状态与操作员查询，把原任务失败与后续恢复结果分开显示；仍不暴露reason/内部异常，不开放未经验证的恢复按钮。
2. 股票独立复权因子/可得时点/锚点版本契约与兼容迁移，先隔离PG与小样本校验，禁止用归档时间推历史可得时间或重写旧qfq。
3. 10缺价/19未匹配受控对账、覆盖预检与下一真实收盘采集证据，按有限预算，不在收盘前造当日daily。
4. 股票AI任务/历史报告及自然语言草稿确认链路的固定评测和异常/权限验证；真实付费调用需明确预算。期货live、国际金银规格/授权与Windows发布回退继续列为未完成验收风险。
