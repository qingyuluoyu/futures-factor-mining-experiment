# 国际金银核验与完整本地项目启动：真实交付记录

日期：2026-10-18，北京时间18:31检查点。承接 `STOCK-PRODUCT-VERIFICATION-2026-10-18-1210.md`，不替代前轮记录。

## 完成状态

**部分完成。** 原项目前端、后端、专用PG、原WSL Redis和行情桥已启动；本轮修复和关键原链路完成自动化与浏览器检查。国际XAU/USD、XAG/USD继续使用用户授权的Teajoin渠道核验，已有真实原始数据和离线审计代码，但**没有开放国际金银行情入口、撮合、AI交易或回测**。不是把AU/AG改名，也不接真实资金账户。没有达到全产品生产验收条件，不停止既有续做自动化。

本轮四包：本地启动与依赖恢复；WS订阅恢复；国际来源审计与币种安全；真实回归及交付。

## 1. 本地启动与故障根因

- 开始时3000/8002/8110/5433停止。PG17专用目录 `F:/qihuo-pg-data` 自动恢复后正常，未初始化、删WAL、重置或恢复覆盖数据库。
- Redis不是无额度，也不是要换6379：原Ubuntu-24.04的6380在WSL内部PONG，Windows间歇连接失败；发行版在命令退出后关闭，Redis进程随之消失。受控`wsl --exec sleep infinity`保活后原Redis稳定；127.0.0.1和172.30.175.142观测到同一run_id。只将忽略配置 `.env.runtime` 的REDIS_HOST改为127.0.0.1，仍用原6380和原DB，没有清缓存/配额，没有改绑定、防火墙或安全策略。IPv6 ::1不可用，未配置给应用。
- 新的版本化启动入口 `scripts/run-local.ps1` 顺序检查Redis、PG、后端、桥、前端；锁定并发启动，验证端口所有者，不杀其他进程；隐藏窗口，保存PID和独立日志；就绪后记录实际监听PID而非Python包装PID。
- 真正新启后端时发现PowerShell额外继承句柄使外层调用一直等后台退出。仅重定向stdin仍失败；现由短暂Python引导进程使用`close_fds=True`、独立文件输出和DEVNULL启动后台进程。PowerShell5/7真实无害子进程测试通过，最终真正重启后端及重复复用启动器均退出0。没有使用ExecutionPolicy Bypass。
- 启动器只表示服务就绪，不将缺行情伪称健康：`/health`为200/status=ok，但`market.ok=false`、`live.ok=false`（0个报价键），`virtual.ok=true`（67个键）；行情桥200、Redis=true、last_poll_ok=true、休市轮询。CTP heartbeat缺失。**不能宣称市场行情模拟盘/所有期货功能全通过。**

### 当前进程与入口

18:28复用启动后 `logs/local-services.json`：

|服务|实际PID|端口/说明|
|---|---:|---|
|Next开发服务|7268|127.0.0.1:3000|
|FastAPI|27376|127.0.0.1:8002，加载本轮最新代码|
|PG17|3532|127.0.0.1:5433，原专用集群|
|WSL保活|32884|原Ubuntu-24.04/Redis6380|
|Sina桥|14236|8110，保留原实现绑定方式|

原生6379 PID6244未使用或停止。临时集成测试6381已清理。PID仅是本检查点，续做时须重新核实。

本地入口：[项目交易页](http://127.0.0.1:3000/trading)。`http://localhost:3000`的login、market、trading、factor-research均真实HTTP200。开发服务不是生产部署产物。原用户localhost浏览器会话未改写；隔离QA使用127.0.0.1和既有qa_账号。

## 2. WS订阅恢复

- 三个原有订阅循环统一为有界IO/清理超时、1/2/4/8/16/30秒封顶退避、重建PubSub及重订阅。成功读取后重置退避；错误消息不重连健康连接。
- 连接失败和消息失败区分，仅记录错误类型，不输出消息正文和堆栈；消息处理异常30秒限频。取消及时传播，关闭连接。
- 初始故障产生123,422,984字节错误日志，已停止故障进程并保留证据，未删除日志掩盖问题。
- 独立6381真实Redis测试在核对INFO process_id为自有PID后才断开3个PubSub socket；本次实测1.047秒后新连接重订阅，三频道的按用户/数据盘路由通过。坏JSON不改变连接；取消后连接和pattern数归零。自有临时PID2224正常退出0，无强制终止，6381无监听、临时目录删除。未操作项目6380或系统6379。
- Redis PubSub本身不重放断线期间消息；此修复不等于持久化Outbox/消息补偿系统。

## 3. 国际金银来源与金融边界

用户确认不换源。本轮国际核验共4个授权业务请求，均一次，无重试：

|请求|真实结果|
|---|---|
|fx_obasic：FXCM/METAL|0行，has_more=false|
|fx_daily：XAUUSD.FXCM，20260701–20261016|86行，9004字节，has_more=false|
|fx_daily：XAGUSD.FXCM，同区间|96行，9167字节，has_more=false|
|fx_obasic：FXCM，不限分类|69行，包含金银，has_more=false|

最后一次查询已解释空目录：该渠道将金银分类为COMMODITY，而参考文档使用METAL。**不能继续把“金银元数据不存在”当当前结论。**

- 两份日线实际均截至2026-10-15，数值结构通过：正且有限的bid/ask OHLC、边界、同标识、无重复、收盘买卖价不倒挂。各含24个周末日期，其中含周六；与目录Sun22:00–Fri20:45及参考文档GMT日期口径的关系未认证，不能静默删周末或猜日界。
- 行情exchange字段全部null。目录存在min/max单位与pip_cost，但pip为空；不能由这些值推导盎司/手、点值、保证金、隔夜费或可交易CFD规则，也不能冒充COMEX交割合约。
- `tick_qty`只记录为**报价笔数**，不是成交量或手数。USD是标识中的报价币种，不等于完整合约规格已经认证。
- 原探测未记录准确采集时间，因此`collected_at=null`并记录collection_time_unverified；审计时间与文件修改时间不冒充采集时间。原始响应SHA及目录SHA可追溯；授权账号访问不等于已确认公开展示/再分发授权。
- 标识白名单归一化与离线审计已实现；所有当前报告能力为false，状态pending_certification。离线CLI对待认证退出2，坏数据退出3；这不是通过认证。没有写国际数据入核心PG/Redis，没有调用模型。

缓存与报告（位于忽略的logs，不含密钥）：

- `XAUUSD.FXCM-daily-20261018.json`，SHA256 `5f33b16074a38ed814ee735f1ddcd0cea3372774d19a155e50ff8a2083b5a8c1`。
- `XAGUSD.FXCM-daily-20261018.json`，SHA256 `9f3ba149bdafbb5f65538a3d7549f15d35a20c1f144eb4f41d4fbd482f651e4b`。
- `international-metals-directory-unfiltered-20261018.json`，SHA256 `aa68ce51a34ae24cca080384e26c6535f5311300e56e2ce03c13810c592ee7b0`。
- 主流程独立复核：`XAUUSD.FXCM-audit-root-review.json`、`XAGUSD.FXCM-audit-root-review.json`，均退出2待认证。再次审计只读本地缓存，不重新请求上游。

参考官方字段文档：[fx_obasic](https://tushare.pro/document/2?doc_id=178)、[fx_daily](https://tushare.pro/document/2?doc_id=179)。这些文档不替代Teajoin本身的质量、授权和时点认证；Teajoin `/q` 本次直读405，未绕过或无限重试。

### 人民币账户防护与错误契约

- 禁止XAUUSD、XAG/USD、FXCM及其他经纪后缀别名落入国内期货DEFAULT规格。手动单、保证金估算、AI资本估算、AI新建/更新、历史回测输入Schema拒绝；内部下单/任务/回测/规格解析也有防御。
- 已修复GET规格查询原本会500的路径：现在认证后返回422及明确中文原因，未先创建用户设置；沪金AU乘数1000、沪银AG乘数15保持原值。
- 本轮涉及的paper/ai-trading/backtest路由增加请求追踪，保留已有业务request_id及权限响应头；422/401保持原错误结构，不记录输入、Token或密码。回测代理透传X-Request-ID并no-store，不转发私有头。
- 真实HTTP31检查确认：错误请求不改变模拟余额、冻结、盈亏、流水、订单、AI任务、股票账户或回测配额；不会触发付费模型。

## 实际文件

新增：

- `scripts/run-local.ps1`、`backend/scripts/local_service.py`。
- `backend/app/services/international_metals.py`、`scripts/audit-international-metals.py`。
- `backend/app/data/instrument_scope.py`、`backend/app/schemas/instrument_scope.py`、`backend/app/core/financial_request_route.py`。
- `backend/tests/test_local_startup_script.py`、`backend/tests/test_ws_subscriber_recovery.py`、`backend/tests/test_international_metals.py`、`backend/tests/test_international_metals_domestic_guard.py`、`backend/tests/test_financial_request_route.py`。
- `scripts/test-ws-recovery-local.py`、`scripts/test-international-metals-local.py`。
- 本交付记录及`docs/superpowers/plans/2026-10-18-international-metals-startup.md`；原扩展计划追加检查点。

修改：

- `backend/app/api/ws.py`、`backend/app/api/paper.py`、`backend/app/api/ai_trading.py`、`backend/app/api/backtest.py`。
- `backend/app/schemas/paper.py`、`backend/app/schemas/ai_trading.py`、`backend/app/schemas/backtest.py`。
- `backend/app/services/paper/specs.py`、`backend/app/services/paper/trading_svc.py`、`backend/app/services/backtest/runner.py`、`backend/app/services/ai_trading/task/task_svc.py`。
- `frontend/src/app/api/backtest/run/route.ts`。
- 忽略的`.env.runtime`只改Redis主机，其他此前配置保留。

## 实际验证

以下从仓库根目录执行，前端两条在frontend目录执行；使用原backend/.venv，没有安装依赖。

|命令/证据|本轮结果|
|---|---|
|`pwsh -NoProfile -File scripts/run-local.ps1`|真正启动后端和重复复用均退出0，实际listener PID27376不变；`logs/metals-startup-detached-launcher.log`、`metals-startup-idempotent-launcher.log`|
|`backend/.venv/Scripts/python.exe -m pytest backend/tests -q --junitxml=logs/international-startup-backend-tests-complete.xml`|**1874 passed / 3既有skipped / 502 warnings，62.23秒**；无新增跳过|
|`node --import ../logs/node-alias-register.mjs --test (Get-ChildItem -Recurse src -Filter *.test.mjs \| ForEach-Object FullName)`|**285通过，0失败/跳过**，`international-startup-frontend-tests-final.log`|
|`node node_modules/typescript/bin/tsc --noEmit`|退出0；`international-startup-typecheck-final.log`为空错误输出|
|`backend/.venv/Scripts/python.exe -W error scripts/test-ws-recovery-local.py`|真实6381断线恢复/路由/清理通过，退出0；`metals-startup-ws-real-integration.log`|
|`backend/.venv/Scripts/python.exe scripts/test-international-metals-local.py`|真实3000→8002→PG/Redis **31/31**；`metals-startup-http-guard-final.log`|
|`backend/.venv/Scripts/python.exe scripts/test-stock-expansion-local.py`|新后端真实**19/19**；`metals-startup-final-stock-expansion-http.log`|
|`backend/.venv/Scripts/python.exe scripts/test-stock-research-jobs-local.py`|新后端真实**23/23**，持久任务、权限、取消、报告；`metals-startup-final-stock-research-jobs-http.log`|
|`backend/.venv/Scripts/python.exe scripts/test-stock-replay-local.py`|新后端真实**18/18**，原价下一开盘、T+1、费用、幂等/权限；`metals-startup-final-stock-replay-http.log`|
|`backend/.venv/Scripts/python.exe scripts/test-stock-product-local.py`|新后端真实**14/14**，自定义资金8并发重放、隔离、覆盖、EOD/复权边界；`metals-startup-final-stock-product-http.log`|
|三个`backend/scripts/migrate_stock_{replay,research_jobs,eod}.py --check`|原3版本/校验和/结构通过，applied=false；本轮无迁移DDL|

红绿及真实失败：

- 原订阅恢复先15红，最终专项及相关68通过；审计47、原EOD12共59通过；最初币种守卫33红后绿。
- 规格路径追加测试真实2失败（含HTTP500），修复后34通过；请求追踪首次1失败、保留业务trace又2失败，修复后合计38通过（`-W error`）。没有删除/放宽关键断言。
- 真后台启动捕获句柄测试先3失败/4通过，仅stdin仍1失败/6通过，最终8通过；真实项目启动也已再验证，不只有mock测试。
- 一次前端Node命令漏原项目别名loader导致`@/lib`无法解析；使用既有loader后285通过，未改业务代码/测试断言绕过。
- 一次批量HTTP复验把研究脚本写成不存在的`test-stock-research-local.py`而退出2；纠正为已有`test-stock-research-jobs-local.py`后23通过，并继续18/14通过。该次失败未宣称通过。
- 两次早期临时Redis测试以native Redis3的flags识别订阅不正确而超时；改用真实sub+psub计数后通过，各轮自有进程均清理。临时测试未干扰原Redis。
- 国际来源离线复核命令（XAG同理替换symbol、payload、output）：`backend/.venv/Scripts/python.exe scripts/audit-international-metals.py --symbol XAU/USD --payload logs/XAUUSD.FXCM-daily-20261018.json --catalog logs/international-metals-directory-unfiltered-20261018.json --start 2026-07-01 --end 2026-10-18 --output logs/XAUUSD.FXCM-audit-root-review.json`，真实退出2，待认证。输出文件不得已存在。

### 浏览器检查与人工路径

隔离QA真实登录7×24模拟；切换A股/期货均保持各自页面。已见A股5558目录、5548行情、缺10/未知19的盘后提示，板块→银行42只→平安银行详情可用；真实打开MA、VOL、MACD、RSI、KDJ、BOLL，显示前复权和截至日期，未接分钟明确禁用。期货交易/资产页面可见，无真实资金接入；没有在用户账户提交交易或资金操作。

当前截图：`logs/metals-startup-stock-dashboard.png`、`metals-startup-stock-boards.png`、`metals-startup-stock-chart.png`、`metals-startup-futures-trading.png`。这些是本轮运行中的原3000/最新代码组合，不再是上轮未重启8002的旧截图。浏览器原localhost标签未改写，其刷新失败的缓存页可由用户手动刷新；未用其他控制面绕过标签访问限制。

人工复验：

1. 打开项目交易页，用自己的账号选择市场行情模拟/7×24；两者都是模拟，不是实盘授权。
2. A股→行情，检查真实覆盖提示→板块→个股；勾选指标、调整视窗，研究页先看历史覆盖再新建，历史报告查看当前用户结果。
3. 期货→资产→充值模拟资金，自定义正金额两位小数；仅在明确要修改自己模拟余额时确认。接口已由隔离QA验证，不要求用户重新转账。
4. 不尝试将XAU/USD手填国内CNY任务；当前422是防护，不是国际功能已可用。沪金/沪银仍是国内产品。

## 数据、兼容、迁移与回退

- 无新增数据库结构、无依赖/框架升级、无分支或提交。原三迁移校验通过：replay `283abed7563dad2d0f08bad1a2da1cc874562dd358fe4eb58c396103e91ab223`；research `893d47e49179300cc2749c38db297ef4ac9db99d9f574537304c7d32492efb02`；EOD `32c748cd87746a1117457dafeb8adaabd473557057dadaad74d9f15ba70910fe`。
- 原A股行情/回放、期货规格/资金、任务契约保留；新增拒绝美元金银套用CNY是有意的安全收紧。请求追踪为兼容响应头；不改变既有业务错误结构。
- QA产生自己的回放账户、报告、模拟资金流水，未清理或覆写生产/原用户数据。实测入金只加qa_账户，不是实际支付。新测试结果保留，未用“恢复整库”抹去验证。
- 回退应用无需删表/清缓存：保存当前源码后按本记录文件级恢复上一轮版本；保留PG和三迁移、QA数据、来源缓存。**不要回退掉WS退避和CNY币种守卫以开放未认证国际交易。** 如果仅停用国际准备，保持入口关闭即可。配置回退主机只在重新验证同一6380服务可达后执行，不切到6379。停止本轮服务前先查实际listener及活跃研究任务，不按旧PID盲杀。
- 旧main.py仍有历史自动建表/ALTER启动逻辑，是既有发布风险；本轮未扩展或以新DDL依赖它，三新增业务迁移均显式check。以后需要独立迁移治理，不在此轮顺便重写。

## 成本与剩余风险

- 国际核验4次Teajoin请求，0模型调用、无批量历史重导；离线审计不联网。常驻Sina桥保留原轮询成本，WSL保活增加发行版常驻资源。
- 既有EOD worker本轮09:59UTC记录2026-10-18休市：no_data/market_closed/attempts=1。源码预算下只需一次trade_cal；重启后同日已有记录不重复下载。2026-10-16快照仍partially_completed，未制造周日行情。下一个真实收盘增量仍待观察。
- 金银规格/时区/日历/采集时间、来源字段缺失、历史可得时点、公开使用授权未认证。认证前不开放核心行情计算、美元模拟账户或回测；不能从现有CNY资金账直接挪用余额。
- 市场行情模拟盘健康缺报价/心跳；本轮验证了7×24下原链路，不等于所有期货实时功能完整可用。
- 原全市场目录5558、盘后5548，不是全市场历史研究覆盖；10缺价、19未匹配代码仍在。原约300股qfq历史/PIT修订、历史行业口径仍有限制。
- 股票只读AI及模型固定评测/付费端到端本轮未执行；没有因用户已有额度就无限调用。
- 本轮未跑新生产build/发布/压测，前轮Windows standalone EPERM和主题hydration风险仍未消除，不能宣称上线。3既有跳过、502 warnings已保留，不是假装零风险。

## 下一轮四包

1. **同源认证**：向现Teajoin核实COMMODITY分类、GMT日界/周六记录、exchange/pip空值、可得时点和展示授权；每次取样明确预算并记录真实采集时间/哈希，不重复全库下载。
2. **国际金银只读垂直入口**：仅认证通过后以版本化导入/Feature Flag，在期货导航展示独立国际分组、bid/ask及报价笔数，明确不是AU/AG。未通过继续隐藏，不填假价。
3. **独立USD日线模拟**：已认证价格单位、乘数、点值、买卖价差、保证金/融资和日历后才做账户/流水/幂等/下一交易日撮合迁移和权限测试；人民币账户不共账、不默算汇率。
4. **旧阻断和发布**：市场行情模拟盘渠道/心跳核对、19身份冲突/10缺价与增量补漏；继续股票AI预算评测及standalone/hydration、回退和人工验收。完成上述前不报告全系统完整。
