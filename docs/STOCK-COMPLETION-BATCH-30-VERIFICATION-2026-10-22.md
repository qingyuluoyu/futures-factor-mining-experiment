# B30 股票报告收藏检查点

## 状态与范围

本轮四工作包完成代码、私有真实PG/HTTP、完整离线回归和本机预览加载验证。它只解决**股票历史报告持久收藏/取消和仅收藏检索**，不是策略收藏认证，更不是股票AI/交易开放。整站仍部分完成，未通过正式发布/回退和全部人工验收，不停止续做自动化。

冻结期货生产代码、B29共享展示修改、认证、网关、布局和运行配置文件。本轮仅3个既有股票生产文件修改，2个既有股票测试文件扩展，其余新增独立股票实现/迁移/验证。无新依赖、Git提交或分支，没有恢复自动收费AI。

## 文件与完整数据流

- `backend/app/models/stock_report_bookmark.py`：独立BookmarkBase、stock_report_bookmarks和stock_report_bookmark_schema_versions，不污染旧ResearchJobBase迁移元数据。收藏仅job_id引用原不可变所属研究任务，FK RESTRICT，时间为TIMESTAMPTZ，不冗余可能不一致的owner/mode。
- `backend/app/schemas/stock_report_bookmarks.py`：StrictBool、extra=forbid，响应job_id/market/bookmarked/created_at保持一致。`backend/app/services/stock_report_bookmarks.py`：版本checksum/表存在门禁，原row本人和JWT盘权限后GET；PUT锁原任务串行化，期望状态幂等、仅已完成且有数据版本和结束时间可收藏，取消可重复。只改引用表，不改研究结果或创建任务。
- `backend/app/api/stock_research_reports.py`：在原已挂载股票router增加availability、分页bookmarks及单报告GET/PUT。沿原市场权限、事务/超时/回滚、request_id、安全头和日志封装，不改main。列表limit1..50、offset0..10000，kind和六状态筛选，单CTE count+page一致快照、按收藏时间/id倒序，defer大JSON，无N+1。
- `backend/migrations/20261022_014_stock_report_bookmarks.sql`、`backend/scripts/migrate_stock_report_bookmarks.py`：版本化兼容新增；默认只读check，显式apply，loopback PG5433限制、事务/advisory lock及锁/语句超时；校验结构/默认时间/索引/FK/checksum。
- `frontend/src/lib/stock-research-api.ts`：新严格收藏契约、本人任务ID及desired-state回执匹配、旧服务404能力降级；503不冒充未收藏。原分页契约保留。
- `frontend/src/components/factor-research/research-report-bookmark.tsx`：先读、显式保存/取消，同帧双击单写；网络未知先显式查询确认，不自动重写；token/revision/市场/任务/卸载/控制器隔离。`stock-research-history.tsx`：独立能力检查失败不阻断原报告，只收藏+类型/状态/分页组合；详情、导出、恢复、对照保留。组件异步结束使用最新onChanged，防旧筛选闭包回写。
- 新后端`test_stock_report_bookmarks.py`、`test_stock_report_bookmarks_migration.py`、前端`research-report-bookmark.test.mjs`，扩展原`stock-research-api.test.mjs`及`stock-research-history.test.mjs`；新`scripts/verify-stock-report-bookmarks-local.py`、`scripts/check-stock-b30-preservation.py`。计划和本记录及主计划/冻结计划指针同步。

## 金融和权限口径

未改变金融公式、方法版本、数据来源、单位、交易日历、复权锚点、税费或历史结果。收藏只是当前用户/JWT盘对已完成报告的引用，不表示策略可靠或可实盘，不将旧qfq研究改说成原价撮合。不增加业务次数限制；保留权限、并发和单请求安全边界。其他用户/盘访问404，期货上下文409，非法bool/extra/分页422。

当前21日盘后仍5548/5558、10缺/20未知，旧历史/PIT/五年仍未认证。22日清晨尚未收盘，本轮没有抓取或构造22日收盘价，也没有重导旧历史。

## 实际命令和结果

根目录（项目venv）：

```powershell
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_stock_report_bookmarks.py backend/tests/test_stock_research_jobs_api.py backend/tests/test_stock_report_artifacts.py -q
backend/.venv/Scripts/python.exe -X utf8 scripts/verify-stock-report-bookmarks-local.py
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests -q -rs
backend/.venv/Scripts/python.exe -X utf8 backend/scripts/migrate_stock_report_bookmarks.py --check
backend/.venv/Scripts/python.exe -X utf8 backend/scripts/migrate_stock_report_bookmarks.py --apply
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b30-preservation.py verify logs/futures-preservation-b30-start.json
backend/.venv/Scripts/python.exe -X utf8 scripts/verify-ux-runtime.py
```

frontend目录：

```powershell
$files=@(Get-ChildItem src -Recurse -Filter '*.test.mjs' | ForEach-Object FullName)
node --experimental-strip-types --import ../logs/node-alias-register.mjs --test @files
node node_modules/typescript/bin/tsc --noEmit --incremental false
$env:WENYUAN_BUILD_DIR='.next-stock-b30-final'
$env:WENYUAN_PREVIEW_BUILD='1'
$env:NEXT_PUBLIC_CHAT_SAME_ORIGIN='1'
$env:BACKEND_URL='http://127.0.0.1:8002'
npm run build
```

- 目标后端34通过，`logs/stock-b30-backend-target.log`。独立只读复查使用项目venv另外执行两个收藏测试文件9通过；其首次系统Python因缺passlib收集失败，改用项目环境解决，未安装依赖。
- 全后端 **2809通过、3跳过、757警告**，`logs/stock-b30-backend-full.log`。三项既有真实供应商测试未设置TYPESAFE_TEST_KEY而跳过，没有为报绿删除/新增跳过或放宽断言；警告包括既有弃用和未await mock，不冒称全部链路均测试。
- 全前端 **635通过、0失败/跳过**，`logs/stock-b30-node-final.log`；TSC exit0，`stock-b30-tsc-final.log`；最终独立production build exit0，`stock-b30-build-final.log`。npm报告既有pnpm专属配置识别警告，无依赖升级。Next临时修改tsconfig include已用apply_patch移除新目录，再由原writeConfigurationDefaults恢复v3及Windows尾换行，原SHA256 `05636e5b2998cb7ee216821014240e016a2fb8f4ceca4ef9c9a80b8ed2b42250`一致。
- 真实私有PG **51检查、3份响应**，`logs/stock-b30-private-pg.log/json`：未迁移拒绝、部分DDL失败事务回滚、重复apply、只读check、默认时间/索引/checksum破坏拒绝及前向修复、5并发收藏唯一时间/4并发取消、事务回滚、本人/盘/期货/严格输入、23报告20+3分页、单请求3条SQL（表存在+版本+count/page）、旧报告/AI摘要不变、schema清理通过。只有私有隔离QA报告元数据，不伪造价格收益，不冒充真实研究计算或真实生产JWT；原股票router和研究就绪校验实际执行。
- 上述3份真实PG/ASGI响应通过最终客户端解析，`logs/stock-b30-real-client.log`；这是离线fetch适配的契约验证，不是浏览器真实用户写链路。
- 运行HTTP **9检查通过**，`logs/stock-b30-runtime-final.log`：页面/资源、匿名AI/通知鉴权、原main worker；health/200不等于全产品验收。

## 先红后绿与独立复查

API缺路由、版本marker存在但收藏表缺失能力误报、客户端未实现、UI缺入口均保存真实红绿日志。私有PG第一轮发现迁移validator漏验timestamp默认，`stock-b30-pg-red.log`，修复后51绿。未知写结果查询确认后必须刷新仅收藏列表，`stock-b30-reconcile-red.log`先红后修。

独立只读复查用历史页harness内存复现P2：收藏保存未完时切换类型，旧onChanged调用旧load丢kind，使当前列表隐藏/总数待核对。根代理新增含保存与未知查询两路径的反例，`logs/stock-b30-callback-red.log`确认initial不等于latest-analysis；仅组件latest ref包含onChanged并在原valid守卫后调用当前回调，完整635绿。复查独立重跑组件/历史/客户端 **44通过**并确认阻断关闭，没有写文件或调用付费服务。

## 保护、真实公开库与服务操作

开工核对B29原1048文件。B30最终 **1051冻结文件零增删改**，`logs/stock-b30-preservation-final.log`；原B29库存另外重算仅5个声明既有股票文件改变、9个声明新增，`logs/stock-b30-source-audit.json`，不以扩大白名单代替审计。审计第一次误以为新增只有5个app文件，断言指出还含已声明迁移/CLI/guard/probe；核实原inventory真实范围后按9项逐一明确匹配，未忽略任何漂移。

修改前三个股票生产源字节副本`logs/stock-b30-before-*.bin`，SHA与原B29库存一致，备份/当前SHA写入source-audit。无Git仓库，不虚构一键checkout回退。

公开库先check预期拒绝未迁移，`stock-b30-public-precheck.log`；私有验证通过后public apply和再次check通过，`stock-b30-public-apply.log`、`stock-b30-public-check.log`。实际新增两张表/一个版本标记，版本`20261022_014_stock_report_bookmarks`，SQL SHA256 `ad8a8a0736ad30abc44dcb8013f5277369fa5637ab68092f2f5945ae2ae16293`。**整轮不是public零写**。本人收藏未代写，最终新表0行；原研究任务和AI任务全行摘要前后相同，`stock-b30-public-before.json/after.json`。

重启前只读确认研究pending/running0、归档任务4completed无活动、付费AI/主播活动0、原1AI任务paused/user。按8002监听21688、PID文件父38628及精确full_local_backend路径核实自有进程后加载新代码，当前启动父2300/监听31848。初次8秒健康探测尚未监听，命令返回失败；后续读取同进程启动完成并实测正常，未重复启动或修改配置掩盖。原main研究/EOD/归档worker true，GP false不变，release_verified=false。

自有3001原15388精确Next命令核实后切到37852，使用`.next-stock-b30-final`和原spawn specification；旧`.next-stock-b29-reviewed`保留。PG/Redis/Sina及非项目服务未停止，未创建/恢复收费任务。

## 本人浏览器与费用

已登录本人股票会话：3001刷新新版→历史报告→原1个已完成报告详情读取成功，收藏先读为未收藏、导出JSON/CSV/恢复配置仍可见→勾仅收藏真实0条→取消勾选恢复原1条且详情保留。没有点击收藏/取消、订单/资金、创建研究/AI按钮，没有读取localStorage/JWT或模型密钥。列表截图`logs/stock-b30-history-preview.png`；滚动后的`logs/stock-b30-bookmark-detail-preview.png`实际可见收藏/查询、原导出与恢复入口。现有侧栏宽和窄视口使详情位于下方，截图不等于完整报告视觉/无障碍验收。

本轮直接模型/Teajoin调用0，未自动重试上次普通对话；原请求仍provider_rate_limited/cost_state=unknown，不能宣称AI对话已恢复。页面和原后台自身可能调用新闻/行情/语音，间接调用费用未独立计量，不虚报为零。

## 迁移、回退、人工复验与风险

仅兼容新增，无原表ALTER/历史回填/删除；DDL和版本在一事务，超时/失败回滚。私有schema中真实演练部分DDL失败、无版本残留及重入，默认只读check和结构前向修复已验证。正式公开库故障/回退演练未执行。

回退先核对source-audit当前SHA及后续执行者，避免覆盖新修改；用三份已验证字节备份生成精确反向patch恢复三个股票源，保留新增文件/兼容表/收藏数据和版本标记，不DROP真实数据；按自有PID核实加载原backend和旧b29-reviewed前端。旧前端不访问新端点，新前端面对旧backend404隐藏能力；如表结构受损，关写入口并显式前向修复，不能只重写版本marker或重跑SQL覆盖错误表。任何运行研究/归档/付费任务存在时不强停服务。

人工复验：本人登录`http://127.0.0.1:3001/history`，打开已完成股票报告，确认先读后点击“收藏报告”，等待确认，再勾“仅收藏报告”；刷新应保留，取消收藏后列表移除但详情不丢。切换类型/页期间保存不得回旧筛选；网络异常先重新查询，不能连续重写。自行测试前知道这会改变本人收藏。生产JWT保存/刷新重登录完整写人工验收仍待用户执行，私有写链路已通过。

## 下一四包

1. 历史可信数据：沿原真实版本/PIT/复权锚点/财报可获得时间预检，核实授权及补采预算后再做受控缺口对账；不盲重导，不以同源一致认证全部历史。
2. 股票只读Agent：工具白名单/Schema/引用/总预算/持久请求幂等及固定评测；外部429未解除不反复试模型，不借报告收藏开放交易。
3. 研究/回放可信输入：版本目录、五年数据和基准资格、安全扩池，保持旧有效qfq与raw隔离，补足金融异常及真实私有PG链路。
4. 共享消息/音频及正式发布另案：明确market/mode/legacy_unknown和兼容迁移，冻结期货下先独立设计/双市场回归；核心端到端、容量、安全、回退和人工验收全通过才宣告系统完成。
