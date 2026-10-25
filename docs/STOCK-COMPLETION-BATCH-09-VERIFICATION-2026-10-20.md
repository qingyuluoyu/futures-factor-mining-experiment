# B09 研究入口兼容与幂等对账交付

最新检查点：2026-10-20 07:08，上海。02:05续做中实施，04:40/07:10触发时继续同一批，不重复开工。**三包代码和本机第二层验证通过；R02与整个主计划仍部分完成，未完成生产构建/浏览器人工验收。**

## 1. 现状、边界与实际修改

此前当前股票研究UI已经使用持久任务接口，但旧 `/api/stock/factors/analysis`、`/api/stock/backtest` 仍直接加载数据库并在线程中计算。前端 `lib/stock-api.ts` 两个旧导出没有生产组件调用，旧CLI和测试仍有调用；不能无预告把默认200改成202或把旧结果伪装成任务。

### B09-1 同key对账不依赖执行器在线

- `backend/app/services/stock_research_jobs.py` 的create增加兼容可选 `accepting_new=True`。队列事务锁内先查本人/JWT盘/幂等键，按既有方法/流程版本校验参数hash；同请求返回原任务、异意图409。**之后**才检查是否接收新任务；离线新请求503且不插入，仍保留原并发/容量/日期/状态保护。
- `backend/app/api/stock_research_jobs.py` 不再在所有create请求前无条件因worker不健康拒绝；把服务器观测的接单能力传给service，不接受客户端伪造此参数。
- 已取消/结束的同key只返回原终态，不复活、不再次计算；不同用户/盘相同key不读取他人任务。已有任务对账不能视为新执行成功。
- 测试修改 `backend/tests/test_stock_research_job_service.py`、`test_stock_research_jobs_api.py`：先红2失败，后service/API 13通过，保留503新请求和409/404严格断言。

### B09-2 旧入口显式协商异步

- `backend/app/api/stock_factors.py` 的两个POST兼容增加请求头：`Prefer: respond-async`（不区分大小写、只支持这一完整值）和UUID `Idempotency-Key`。两个头须同时出现；非法协商、缺key或严格参数不合规422，不静默退回同步。
- 显式异步：原始JSON重新校验为现有严格CreateResearchJob（拒绝被旧宽松body忽略的未知字段），仅注入对应analysis/backtest kind与头部key；复用现有readiness、ResearchJobService、create/execute及commit链。202返回原任务视图、`Preference-Applied: respond-async`、相对 `Location: /api/stock/research/jobs/{id}`、request_id/method/no-store。
- 与直接 `/api/stock/research/jobs` 的同参数+key互认；共享单用户活动槽/总队列/运行预算/取消/结果。入口不拉行情、不计算、不调用模型；读取报告仍由原资源权限控制，不能靠伪造mode覆盖JWT。
- 未协商且兼容开关为true：仍返回旧200和原金融计算结果，增加 `X-Legacy-Research: deprecated-sync`、alternate Link、no-store；没有制造Sunset日期。没有把旧错误/成功偷偷改成202。
- POST使用AuthContext获取JWT盘，并复用Depends(get_session)的**同一请求数据库会话**；不额外获取第二个认证/提交连接。原只读因子目录接口不改。
- 新 `backend/tests/test_stock_research_legacy_adapter.py` 9项实际router+service测试；`test_stock_research_api.py` 旧成功/金融错误正常链保留，夹具显式提供AuthContext及未使用的请求会话stub，原真实行情loader和金融断言未替换。

### B09-3 可控退役与运行验证

- `backend/app/core/config.py`、`.env.example` 新 `STOCK_LEGACY_SYNC_ENABLED=true`，默认保持旧消费者兼容；仅运维明确设false时，未协商旧同步请求503+`X-Research-Error: legacy_sync_disabled`+迁移链接/追踪，**加载行情之前拒绝**；显式异步照常可用。真实.env没有改，本地该值仍true。
- availability兼容添加 `legacy_sync_enabled`、`legacy_async_adapter`；后者检查两个适配路由确实注册，不因仅有任务router就假报能力。现有前端Schema允许兼容额外字段，现有UI不需要改回旧端点。
- 新 `scripts/test-stock-completion-batch-09-postgres.py`；B02通用隔离脚本只增允许报告名b09，不改变原验证流程。真实本地PG/JWT/ASGI HTTP、私有schema/用户，测试源数据只在私有确定性fixture中，不当供应商认证。
- **67检查通过、isolated_schema_cleaned=true、公共业务写0、来源0、模型0**：匿名401、期货市场409、旧同步关闭503；跨入口并发同key只有一任务；429共享并发；离线同key202/意图冲突409/新key503；其他用户/盘404且不复用任务；取消和终态不重启；回测通过真实计算函数+认领/持久化/结果HTTP复开；原三个资金账户、订单/流水不变。

## 2. 复查发现和修复证据

- 独立只读复查发现P2：初版适配器在认证连接仍占用时，再从state.session_factory取连接；池饱和时两请求互等。复查用实际路由和有界pool夹具复现：容量2+两个旧适配请求均503，同容量直接/jobs均202。
- 采纳后先加反例 `test_async_adapter_reuses_injected_request_session_instead_of_nested_pool_checkout`，观察1失败；改为注入共享get_session，再跑受影响48全部通过。
- 又用**真实PG pool_size=1、max_overflow=0、pool_timeout=1**同时发两个JWT适配请求，同key均202；不是只靠mock证明连接释放。B09私有脚本恢复原session_factory和dependency override，finally dispose限额engine，清理schema。
- 第二次独立复核被Codex使用额度错误中断，没有结果；**不能把独立复核声称已签字通过**，未无限重试。主执行者已复查调用/事务和运行上述真实PG、完整回归。正式发布前保留独立复核和人工验收项。

## 3. 实际执行命令与结果

仓库根，Python虚拟环境3.12；Node/TSC在frontend目录：

```powershell
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_stock_research_jobs_api.py backend/tests/test_stock_research_job_service.py -q
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_stock_research_legacy_adapter.py backend/tests/test_stock_research_jobs_api.py backend/tests/test_stock_research_api.py backend/tests/test_stock_research_job_service.py -q
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests -q
backend/.venv/Scripts/python.exe -X utf8 scripts/test-stock-completion-batch-09-postgres.py
backend/.venv/Scripts/python.exe -X utf8 scripts/audit-product-readiness-local.py
./scripts/run-local.ps1 -TimeoutSeconds 60
node --import ../logs/node-alias-register.mjs --test (Get-ChildItem -Recurse src -Filter *.test.mjs | ForEach-Object FullName)
node node_modules/typescript/bin/tsc --noEmit --incremental false
```

|证据|实际结果|
|---|---|
|`logs/b09-reconcile-red.log`|2失败/11通过；先重现离线对账误拒绝|
|`logs/b09-reconcile-green.log`|13通过|
|`logs/b09-adapter-red.log`|新增异步/退役8失败|
|`logs/b09-adapter-first-green.log`|10失败/27通过；旧测试身份及配置夹具未对齐，原断言不放宽|
|`logs/b09-targeted-green.log`|47通过|
|`logs/b09-shared-session-red.log`|嵌套checkout反例1失败|
|`logs/b09-shared-session-green.log`|最终受影响48通过|
|`logs/b09-backend-full.log`|复查修正前2518通过/3既有skip，仅为历史记录|
|`logs/b09-backend-final.log`|修正后最终2519通过、3既有skip、668 warnings，exit0；包括既有异步mock/utcnow告警，未称消除|
|`logs/b09-node.log` / `logs/b09-tsc.log`|449通过、0失败/skip；TSC0|
|`logs/stock-completion-b09-pg-20261019-181515.json` / `logs/b09-pg-final.log`|67通过及清理，单连接并发反例通过|
|`logs/b09-runtime-http.log`|health200、匿名availability/paper401，login/factor-research/backtest页面200；两个POST OpenAPI实际有202/Prefer/Idempotency-Key|

没有删测试、放宽owner/盘/金融断言或用假结果替代真实应验的PG链路。旧计算成功测试仍计算原净值、IC/缺价等。没有新增数据库表/迁移/依赖，没有删除旧CLI和导出。

## 4. 服务、数据、成本、迁移与回退

- `logs/b09-before-reload.log`：本地PG READ ONLY，研究9failed/17completed/7cancelled、挖掘/主播0、AI1paused、EOD终态、归档2completed；无活动任务。核实记录backend PID19820的项目父进程、local_service脚本和8002监听后，仅停该backend并运行既有隐藏启动器。
- 07:06五服务就绪，新backend PID1532；07:07 OpenAPI/HTTP核实新接口已加载。未使用真实用户token冒充人工登录。页面200与健康200不代替交互/金融验收。
- 新开关本地legacy=true、catchup=false；没有重复诊断/恢复19日，没有下载20日未收盘行情。0受控来源/0付费模型调用、新依赖0；既有常驻服务流量不冒称计入探针。
- 07:07 READ ONLY新观测 `logs/full-code-audit-snapshot-20261019-230704.json`：19日仍partial5545/5558、缺13/未知19；原研究历史300/585392仍到16日，回放3证券，影子归档11093观测/5549证券/2日；五年仍over_limit。B09无金融公式/价量/复权/时点口径变更。
- 无新DDL；002/007旧数据与方法版本不变。回退B09代码时旧200消费者不受影响，但**使用新协商协议的客户端不能直接回退到会忽略Prefer的旧服务**，否则可能误触发同步计算。回退前暂停适配器客户端/网关拒绝协商请求，保留已建任务和原key，通过原/jobs对账；不要删任务、恢复自动启动DDL或重新提交新key。
- 兼容退役开关可恢复true，但这会重新开放旧同步资源路径；本轮没有自动关旧入口。该路径仍不具备全部新任务预算，因此**R02未全部完成**，不能以适配器上线宣称所有直接研究请求受队列保护。

## 5. 人工待验收、风险及下一五包

本轮浏览器安全限制未绕过、没有新生产build/部署或全站人工E2E。此前Windows standalone复制EPERM、P01迁移基线和S04持久确认等发布阻断仍保留。独立复查修正后复核未得到结论。

人工路径（未执行）：本人登录研究/回测→创建持久任务→查看状态/取消/复开报告；在测试环境用同key跨入口重复提交，必须同任务；执行器暂停时仅能对账，不能建新任务；用其他本人测试盘核实404；生产退役前迁移旧CLI/直接消费者，明确flag和客户端回退配套。

下一轮五包，仍按依赖和3—5包节奏：
1. GP固定真实输入与版本化种群/RNG/best_seen检查点；本地错误股票标签保留审计，不把无合格期货数据伪装成可挖。
2. GP逐代原子认领、暂停取消/断点重启及owner/market/mode隔离，真实PG并发/中断验证；不每tick首代、不只缓存generator。
3. 真期货研究数据/换月/金融方法→受控任务/IC分组/报告/UI；外部数据缺失先做安全依赖，不私换源。
4. R02剩余消费者迁移与旧同步关闭演练、修正后独立复核；S04持久用户确认/参数hash/同事务写幂等与P01全局迁移发布按独立详细计划推进。
5. D01/D03按预算核实13缺失/19未匹配，受控补采和原价/复权版本桥接；PIT/五年/基准/DSL/回放扩池/股票AI等不假标完成。

保留所有续做自动化，整体仍部分完成。
