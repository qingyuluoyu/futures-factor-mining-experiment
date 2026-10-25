# 2026-10-19 05:38（北京时间）后台影子归档与状态链路检查点

## 完成状态

05:15续做已执行。本轮4包：005持久任务迁移、独立后台worker/生命周期/只读API、共享页面归档状态、真实PG/HTTP/浏览器/审查，均完成下述本机验证。**整个系统仍部分完成，不能当生产验收**；旧qfq历史、因子锚点、完整覆盖、AI和发布验收未因归档任务完成而完成。

先读用户AGENTS、18日完整性计划、02:20最新交付、模型/接口/配置/迁移/测试；保留前轮修改。无Git，无分支/提交，无新增依赖，无供应商HTTP或模型调用，未在05:15采集未收盘日线。原EOD和归档worker独立，没有重写原快照/目录/旧股期货历史或账户。

05:15的单次自动化`a-05-15`已履行并通过应用工具删除（deleteStatus=deleted），避免过期单次任务遗留；原`A股系统续做 02:05`及`A股系统续做 07:10`经实际配置读取仍ACTIVE，未停止未完成系统的续做。

## 实际修改文件

- 新增`backend/app/models/stock_archive_job.py`、`backend/migrations/20261019_005_stock_archive_jobs.sql`、`backend/scripts/migrate_stock_archive_jobs.py`、`backend/tests/test_stock_archive_jobs_migration.py`。
- 新增`backend/app/services/stock_archive_worker.py`、`backend/app/services/stock_archive_status.py`、`backend/tests/test_stock_archive_worker.py`、`backend/tests/test_stock_archive_status.py`。
- 修改`backend/app/main.py`、`backend/app/core/config.py`、`backend/app/api/stock_market.py`：独立生命周期、默认关flag、兼容只读状态字段。
- 修改`.env.example`（默认false）、`.env.runtime`（仅本地新增STOCK_DAILY_ARCHIVE_ENABLED=true，未改凭据）。
- 修改`frontend/src/lib/stock-eod-status-api.ts`及`.test.mjs`、`frontend/src/components/market/stock-eod-status.tsx`及`.test.mjs`，不改其他页面/期货图表。
- 新增`scripts/test-stock-archive-worker-local.py`、`scripts/test-stock-archive-worker-http-local.py`。
- 新计划`docs/superpowers/plans/2026-10-19-stock-archive-worker.md`、本记录、原总计划追加最新检查点。日志/JSON/截图见下表，均为本轮实际产物。

## 核心数据流与金融边界

`STOCK_DAILY_ARCHIVE_ENABLED`默认false；启动只check003/004/005，不隐式迁移，10秒就绪边界，不保存或公开完整settings。缺迁移/worker启动失败只关闭归档，不关闭原EOD或伪装已完成。

worker启动即查已有成功EOD，随后每300秒最多处理一个候选批次；只投影源ID/日期/版本，未到16:30排除今日，失败/no_data源不领取。来源+归档方法唯一，running领取独立事务提交；90秒deadline，执行60秒总界，SQL5秒/lock2秒。对锁定任务和源版本复验，调用004归档服务，影子观察和completed/输出ID同事务提交。

正常状态机：无记录→running/archiving→completed或failed或cancelled。一次attempt；失败/取消不自动重试、不覆盖旧归档。每tick最多10个过期running改failed/archive_expired。重启可领取未处理源、核实过期；**终态显式重试入口尚未实现**，不能宣称所有失败自动恢复。数据库不可达导致终态保存失败时日志明确、取消仍传播；未确认状态不伪造新终态，DB恢复后过期核查。

原EOD成功不因影子归档失败回滚；没有付费工具/模型/来源调用。原价/CNY/小数手/快照hash/来源证据继承004严格校验，不混qfq或补因子1/成交量0，不推断历史可得时点。归档成功仅指一批影子数据与任务持久化，不等于全市场历史完整或独立认证。

GET `/api/stock/market/sync-status`保留旧v1字段，新增`archive`对象：enabled/ready/worker/state/message、最近任务投影、shadow_only scope、0来源/模型。只新增1个LIMIT1查询，不读quotes/error_message；disabled/notready不读新表。仍需登录、no-store、追踪ID、总5秒/异常503。前端archive optional兼容旧服务器；存在坏对象拒绝，不当idle；严格验证单次attempt、32hex请求ID和状态/步骤/结束时间/结果ID/错误码组合。两处页面共用原刷新/取消，不新增轮询或写请求。

## 真实迁移、运行与结果

先私有PG与独立审查通过后，本地专用5433显式应用005：SHA256 `a3f59406504f788a67bfed1e5109b26a1305092e64c0974bf50898878f84cdb0`。独立ArchiveJobBase和两张新表，不改004/003已应用SQL、共享Base、期货或旧字段。

核实无running研究任务（9failed/14completed/7cancelled均终态）及本项目进程命令行后，只重启backend12652；现在backend**10784:8002**，frontend7268:3000、PG3532:5433、bridge14236:8110、原WSL keepalive32884/Redis6380均复用，原生6379未碰。下轮重新核对PID，不能照此直接kill。health200但market.live.ok仍false/virtual67键，不能说期货所有行情已通过。

真实worker首次检查只核对已有Oct16原价影子批次，无新来源请求/无重复5548行：新任务`30a7637b-78e7-4df4-ad8a-7da6764adc34`，request_id `2e125db8556b420d8f80a25748b6acff`，一次attempt、completed；来源EOD `14831b03-e618-4f9d-a89b-f6db88f3c62b`，输出仍前轮`f5ebaa36-febc-4789-a2aa-f4c265cf7ba5`。耗时约0.244秒是这一已有批次核对样本，不是性能SLO。

用户行情仍Oct16/5548/5558/缺10/未匹配19、原daily响应证据缺失，before_window且今天无任务。没有把05:15的归档时刻当作今日收盘，也没有切换用户K线/回测读路径。

## 实际命令与证据

前端命令在frontend目录执行，其他在仓库根目录。

| 命令/验证 | 实际结果 | 证据 |
|---|---|---|
| `backend/.venv/Scripts/python.exe -W error -m pytest backend/tests/test_stock_archive_worker.py backend/tests/test_stock_archive_status.py backend/tests/test_stock_archive_jobs_migration.py backend/tests/test_stock_eod_status.py -q` | **103 passed** | `logs/stock-archive-worker-focused-reviewed-20261019.log` |
| `backend/.venv/Scripts/python.exe -m pytest backend/tests -q --junitxml=logs/stock-archive-worker-backend-final-20261019.xml` | **2138 passed / 3既有skipped / 502既有warnings**，59.07秒 | 同前缀`.log`/`.xml` |
| `node --import ../logs/node-alias-register.mjs --test (Get-ChildItem -Recurse src -Filter *.test.mjs \| ForEach-Object FullName)` | **304 passed，0 skipped/fail** | `logs/stock-archive-worker-node-final-20261019.log` |
| `node node_modules/typescript/bin/tsc --noEmit --incremental false` | 退出0 | `logs/stock-archive-worker-typescript-final-20261019.log` |
| `backend/.venv/Scripts/python.exe scripts/test-stock-archive-worker-local.py` | **21真实PG检查通过**；schema已清理、公库选定聚合不变 | `logs/stock-archive-worker-pg-final-20261019.log`、`logs/stock-archive-worker-pg-20261018-213639-f341adb6.json` |
| `backend/.venv/Scripts/python.exe backend/scripts/migrate_stock_archive_jobs.py`及`--check` | 实际应用005，重复check applied=false/checked=true | `logs/stock-archive-jobs-migration-applied-20261019.log`及`-check-`日志 |
| 003/004/005三个原迁移脚本`--check` | SHA/结构均通过，applied=false | 实际终端，原001/002未修改，本轮未重新执行其check |
| `backend/.venv/Scripts/python.exe logs/check-running-stock-jobs.py`、核实进程、`pwsh -NoProfile -File scripts/run-local.ps1` | 安全重启本项目后端、其余复用 | `logs/stock-archive-worker-local-restart-20261019.log`、`logs/local-services.json` |
| `backend/.venv/Scripts/python.exe scripts/test-stock-archive-worker-http-local.py` | **13真实检查通过**：两个既有qa_用户、401/追踪/原快照/原归档ID/单次任务/读取不重复写 | `logs/stock-archive-worker-http-20261019.log`、`logs/stock-archive-worker-http-20261018-213401.json` |
| 独立QA浏览器工作台→行情→刷新状态 | 可见归档完成/开关/worker/Oct16来源日，保留before_window与缺口/缺证据 | `logs/stock-archive-worker-dashboard-20261019.png`、`logs/stock-archive-worker-market-20261019.png` |

真实PG覆盖003/004/005私有迁移check、六worker一次领取/原子完成、版本/字段校验失败、真实SQL 1/0造成归档回滚后失败任务持久化、过期不再执行、执行取消回滚、领取提交后退出取消真实cancelled、领取未提交不伪造终态且可下tick领取、完成已提交后取消不能覆盖completed、未收盘当日排除、公开状态投影和原EOD不改写。公库旧股/期货历史、EOD/目录JSON摘要、已有影子行量价聚合前后不变；证明范围不是全库真实性/资金认证。测试行情仅在私有schema合成并清理，不写入公库冒充真实数据。

## 失败、修复与审查

- worker先8红、状态先10红、005先40红转绿；前端原16项中5红，补实现后全绿。前端审查新增非法组合先红，再严格refine，最终304 Node通过。
- 005额外严格目录校验真实PG首次失败：asyncpg内部char返回b'r'/b'p'。先只读确认类型，再目录SQL显式`::text`，不放宽结构/状态断言；SQL文件SHA未变。失败报告`stock-archive-worker-pg-20261018-212251-132b33ca.json`保留且清理完成。新旧迁移相邻121项-W error实际通过，完整后端再次覆盖。
- 独立审查Important1：领取提交与退出在try外，取消漏记。新增1红→try覆盖全领取窗口、仅UPDATE真正存在running并returning，不插入未提交的伪终态；真实PG多种取消窗口通过。
- Important2：终态保存失败会把原CancelledError改RuntimeError并继续worker sleep。新增超时/数据库异常2红→记录脱敏日志但优先继续抛原取消，正常失败才RuntimeError。最终审查两个重要项和前端Minor均解除，无剩余Critical/Important；独立专项68及API8项通过。

没有删/跳过测试、吞异常当无数据、改旧关键断言制造通过。未执行：本轮完整发布build、下一真实交易日收盘/外部行情核验、付费AI、因子锚点历史迁移和人工发布验收；既有Windows standalone EPERM/主题hydration/502warnings保留为风险。

## 兼容、成本与回退

新增005是小空表/索引兼容扩展，已有004归档不回写，任务数据可被旧应用忽略；源码新默认flag关闭，仅本机开启。API新增字段兼容旧消费者，新前端兼容旧服务器缺字段。金融公式、单位、股期货资金及撮合规则未变。

回退：把本地新flag设false并受控重启后端、必要时回退新UI字段/代码；保留005/004表、任务和归档，不DROP、不恢复整库、不清Redis。关闭前核查running任务；正常stop尝试保存cancelled，若DB不可用日志指明未确认，恢复后按deadline过期核查。已提交completed不被取消覆盖。已应用005不可再改SQL；后续结构用新编号扩展迁移。终态重试尚无用户入口，管理员不要直接UPDATE状态或盲重跑来源。

成本：0新增上游/模型费用；每300秒检查、每进程最多一批、有限本地SQL/最多10000行转换核对；多实例由唯一键避免同来源执行重复。状态刷新仅3个有界元数据查询，无高频新轮询。未做容量/长期负载验收，不能把单次核对耗时当生产保证。

## 人工路径及下一轮四包

人工：进入A股工作台或行情，盘后状态下能看到影子归档开关/就绪/worker及Oct16来源任务；展开查ID/错误、刷新不应增加任务/观察数；切回期货确认原功能。用户人工验收仍待完成，本轮是隔离QA自动浏览器验证，不代替人工发布验收。

1. 终态显式安全重试/恢复：内部管理员边界、输入Schema、幂等请求、预算/次数上限、新任务版本与错误分类；确定性校验错误不自动重试，不允许任意SQL改状态。
2. 新编号股票因子观察/可得时点/复权锚点和版本化研究读契约，保留旧qfq/报告；不得把当前归档时间回填历史可得时间或切换不完整数据源。
3. 10缺价/19未匹配的受控来源标识对账及实际16:30后收盘验证，证据真实落库；07:10仍未收盘，不抓取当天daily当收盘、不猜价格。
4. 股票AI只读来源/权限/预算与报告回归、期货live/国际金银合约规格与币种/交易日历/授权及Windows发布回退。原系统整体未完成，02:05/07:10续做保持，下一轮先读本记录，不重复005和已有一批归档。
