# A股板块、tick指标、开放注册与研究修复检查点

2026-10-18 02:38（北京时间），02:05续做轮次。**本轮四个工作包已实现并通过本机分层验证；整个产品仍为部分完成，不批准生产上线。** 继续保留02:05、07:10两项续做自动化，不标记整个系统完成。不提交、不建分支；工作目录无Git元数据。

## 本轮实现及数据流

1. 开放注册：默认 `REGISTRATION_POLICY=open`，有效开关开启、每日限额0、剩余名额null，不被旧数据库名额阻断；`managed`明确保留原配置行为。两入口不再强迫勾选不存在的条款或提供手机号，仍经Schema、bcrypt、唯一约束和事务入库。并发重复一份201、一份409，提交异常rollback；其他数据库错误不冒充重复。注册凭据先经绑定的/me握手，再一次写入会话，失败明确“账号已创建，请登录”，不重复注册。管理端显示实际策略和旧配置的区别。
2. 行情入口：默认行业卡片→带分页/搜索/排序/数据筛选的成员→个股K线、估值、已披露财务、数据覆盖。返回保留当前成员筛选；全局搜索通过股票深链打开详情。新接口均鉴权、有数量上限，行情库错误503而非空列表，业务异常409，缺证券404；记录request_id、用户、操作及耗时。代理仅白名单透传追踪/方法/业务错误响应头，no-store，不转发上游cookie。
3. tick图表：保持目标项目lightweight-charts，不改tick参考工程或替换框架。股票独立实现VOL/均量、MACD、RSI6/14/24、KDJ、BOLL、前1–20同周期量能比较、视窗/十字线/放大/Esc；保留日周月及用户/JWT模式隔离偏好。没有分钟或异动源时明确禁用，不制造假线。个股顶部和图表使用同一校验后序列；异常/重复日线整链拒绝。旧日线查询补上海当前日期上界，未来根不能进入详情和指标。
4. 研究修复：固定份额逐日估值、按真实成交额扣成本、自融资求解、旧持仓漂移换手与卖出、前一交易日信号、联合OLS中性化、严格缺因子/缺价/缺市值、几何年化和严格日期/枚举。原五项金融强回归完全未改。研究页新增共用起止日期及加载错误重试，真正接入两API；失败清除旧结果，展示方法版本和完整风险。无回撤卡玛、缺失卖出因子值为null，不填假值。

## 目录、金融口径与数据边界

- 授权目录同步成功：300→5558条，110行业；仅更新 `stock_instruments` 元数据，未批量下载行情、删除旧证券或改变用户/资金/回放快照。批量500、单事务upsert，重复执行不增加重复证券。
- 同步证据 `logs/stock-catalog-import-20261018.log`，供应商DaoShare，数据集stock_basic，采集时间2026-10-18 02:09:59+08:00，标准化SHA256 `5b89bdab5a67a28dcad81955d0eb3877ba1a002f3c2c2af57100ac52ef181ff0`。**该次旧客户端未保留has_more，5558仅为实际已导入数，未独立认证为全部A股。** 后续CLI已新增分页元数据：has_more=true拒绝且不开DB；缺元数据标supplier_not_certified；false仅为供应商声明无后续，非独立完整认证。未为检查元数据再次调用上游。
- 最新日线2026-10-16：当日有效报价299/5558，只覆盖已有历史样本；本地总日线585392、估值202377、财务10328，目录扩充没有扩大这些历史覆盖。
- 板块涨跌为可比较样本等权平均，不是行业指数，不代表所有成分股。行业为当前stock_basic分类，非历史成分。历史有价、当前缺价使用stale_quote并显示最后真实日期；完全无历史才是“仅目录”。缺价不等于停牌。
- 图表价格人民币元、前复权，量为手（100股）；估值市值亿元、换手率及财务比率百分数。当前详情财务只取公告日和报告期不晚于上海当前日期的最多8期，缺公告日不展示；不能据此宣称财报修订时点已认证。回放继续使用独立不复权/股数据，不混入前复权图表报价。
- 股票图表方法 `stock-chart-1.0.0`：MACD按tick首价EMA初始化，BOLL样本标准差；RSI采用已披露的Wilder首段SMA，平盘50，与tick零初始方法不完全相同。未修改期货共用指标函数。
- 研究方法 `stock-research-2026-10-17.v2`、状态research_only。前复权连续份额的比例成本研究不等于百股/T+1/最低佣金/印花税/涨跌停撮合。公告仅有日期，信号保守使用publish_date严格早于信号日。方法详情在 `backend/app/services/stock_factors/METHOD.md`。

## 实际文件

相对本轮开始源码备份核对，39个应用源文件变更（本列表不是要求重新覆盖其他改动）：

```text
backend/app/main.py
backend/app/api/auth.py
backend/app/api/stock.py
backend/app/api/stock_factors.py
backend/app/api/stock_market.py
backend/app/core/config.py
backend/app/data/tushare_client.py
backend/app/schemas/auth.py
backend/app/services/registration_svc.py
backend/app/services/stock_catalog.py
backend/app/services/stock_market.py
backend/app/services/stock_factors/engine.py
backend/app/services/stock_factors/METHOD.md
frontend/src/lib/admin-api.ts
frontend/src/lib/stock-api.ts
frontend/src/lib/stock-market-api.ts
frontend/src/lib/stock-market-api.test.mjs
frontend/src/lib/stock-proxy-response.ts
frontend/src/lib/stock-proxy-response.test.mjs
frontend/src/stores/stock-market.ts
frontend/src/stores/stock-market.test.mjs
frontend/src/app/(auth)/register/page.tsx
frontend/src/app/(main)/factor-research/page.tsx
frontend/src/app/(main)/market/page.tsx
frontend/src/app/admin/settings/page.tsx
frontend/src/app/api/stock/[...path]/route.ts
frontend/src/components/factor-research/factor-research-page.test.mjs
frontend/src/components/landing/auth/auth-register-form.tsx
frontend/src/components/landing/auth/registration-forms.test.mjs
frontend/src/components/market/contract-search-dialog.tsx
frontend/src/components/market/stock-board-browser.tsx
frontend/src/components/market/stock-chart-data.ts
frontend/src/components/market/stock-chart-data.test.mjs
frontend/src/components/market/stock-kline-chart.tsx
frontend/src/components/market/stock-market-view.tsx
frontend/src/components/market/stock-quote-data.ts
frontend/src/components/market/stock-quote-data.test.mjs
frontend/src/components/market/stock-quote-detail.tsx
frontend/src/components/market/stock-universe.tsx
```

另有 `.env.example`、`scripts/import-stock-catalog.py`、`scripts/test-stock-expansion-local.py`，新增后端测试registration_open、stock_catalog、stock_catalog_pagination、tushare_pagination、stock_market_overview、stock_market_board_api、stock_research_engine、stock_research_api，及本记录和原计划检查点。验收中发现新板块测试与旧 `test_stock_market_api.py` 同名冲突，已从任务工具历史恢复原六项用例并将新增测试独立命名，不删除旧断言。逐个测试ID与上轮XML比较，1440个旧用例无遗漏。

## 实际命令与验证结果

均在工作目录执行，除标明frontend/隔离目录。RED阶段确认缺实现、未来根、错误OHLC、过期行情误标、分页截断和缺追踪头等真实失败，随后修复，未放宽原金融断言。

| 命令/检查 | 最新真实结果 |
| --- | --- |
| `backend/.venv/Scripts/python.exe -m pytest backend/tests -q` | **1533通过，3既有跳过，152警告，44.96秒**；`logs/stock-expansion-full-pytest-20261018.log`。警告主要为既有utcnow弃用和旧测试AsyncMock未await，不将其宣称零告警。 |
| `backend/.venv/Scripts/python.exe -m pytest backend/tests --collect-only -q`，与上轮XML按ID比对 | 当前1536用例，旧1440个无遗漏；原五项回归文件SHA256仍为E60DED2E8C784D63E158310245F958DDD8B6570DF2FEB00228338913EEE44266。 |
| frontend所有`*.test.mjs`：`node --import ../logs/node-alias-register.mjs --test $tests` | **196通过，0失败/跳过**；`logs/stock-expansion-node-20261018.log`。首次直接Node全量175通过/1模块加载失败，原因是原期货测试的@路径未解析；最终使用只做路径解析的Node原生loader运行全部用例，未改断言或跳过该模块。 |
| frontend `node node_modules/typescript/bin/tsc --noEmit --incremental false` | 退出0。 |
| frontend源码复制到`logs/stock-expansion-build-20261018`，排除.env*/node_modules/.next，依赖junction复用；`node node_modules/next/dist/bin/next build` | 最终含研究日期UI和追踪代理的版本构建退出0；`logs/stock-expansion-build-20261018.log`。不覆盖运行中的dev构建。 |
| `backend/.venv/Scripts/python.exe scripts/import-stock-catalog.py` | 一次成功目录同步5558条；无历史下载。同步前pg_dump退出0，17509086字节。 |
| `backend/.venv/Scripts/python.exe scripts/test-stock-expansion-local.py` | **19项真实HTTP/PG全通过**：开放策略、并发注册201/409、bcrypt/空联系方式、登录/me、分页/搜索/权限、目录幂等事务回滚检查、真实财务/研究、缺价409、非法枚举422、期货资金不变。日志和结果JSON在logs/stock-expansion-http-20261018.log及stock-expansion-http-results-20261018.json。 |
| `backend/.venv/Scripts/python.exe scripts/test-stock-replay-local.py` | **18项真实HTTP/PG全通过**：自填资金、并发幂等、原始开盘/费用/权益、T+1、撤单、跨用户及JWT模式404、期货资金不变；`logs/stock-expansion-replay-http-20261018.log`。 |
| `backend/.venv/Scripts/python.exe backend/scripts/migrate_stock_replay.py --check` | checked=true、applied=false，版本20261017_001_stock_replay，checksum与已应用记录一致；未改结构。 |
| 浏览器与接口追踪 | 真实浏览器验证直接注册后/me进入正确QA用户、板块→银行42成员→平安银行、返回保留板块、兰州银行无假价、tcl搜索3条→TCL科技、股票切期货恢复rb2701。VOL/MACD/RSI/KDJ/BOLL同开、周/月、量比20同周期、放大/Esc均实际交互。新QA用户默认偏好不继承原QA图表配置。 |
| HTTP健康及脱敏 | 3000/login、8002/health、8110/health均200；boards/details经过3000代理仍带X-Request-ID。仅状态码不能证明实时行情健康。39个修改源文件扫描实际runtime密钥值，匹配0。 |

第一次真实HTTP测试因测试脚本误用 `/api/users/me` 失败，修正为实际 `/api/auth/me`；随后因真实缺价拒算而停，调查后保留409强断言、另外验证完整覆盖区间的200，不伪装所有日期都成功。上述表为修复后的最终结果。

### 真实缺价和正常路径证据

`ret_20d`，2026-01-01→09-30，top_n5组合回测200且净值有限；同区间因子检验409，缺价为300442在02-06/09/10/11/12/13六个观测交易日。现有库无停牌和导入审计，不能判断原因，不填前价也不删除未来缺价成员。只读调查在 `logs/stock-research-coverage-probe-20261018.json`。

2026-07-13→09-11已有300样本45观测日无缺价，检验200：horizon20有4个IC观测、23个净值点，末值0.91810792；这个短窗只验证正常链路，不能作为策略有效性证据。浏览器已亲自执行该区间检验和20股月调仓回测，展示方法/风险和真实交易明细；改广区间后出现明确缺价提示且旧曲线清除。证据 `logs/stock-research-http-probe-20261018.json`、截图 `logs/stock-research-verified-20261018.png`。全系统不是“所有股票所有日期均可回测”。

独立只读复审发现并闭环未来根、顶部/图表不一致、has_more丢失、历史误报仅目录四项问题；最终该审查范围未留阻断P1/P2。此结论不是完整旧产品安全审计。

## 兼容、迁移、回退、成本

无新数据库结构迁移、无新依赖、无真实账户或资金接入；原期货接口/账户/状态不变。新市场接口是增量；原数据客户端query仍返回list，新query_page供目录读取分页状态。研究成功字段保留并增方法/风险，错误从旧伪成功变明确4xx，前端已接；这是有意修正，不将旧错曲线与v2比较为同方法结果。

源码备份 `logs/stock-market-expansion-backup/frontend-src`、`backend-app`，目录同步前数据库备份 `logs/before-stock-catalog-20261018.dump`。回退先逐文件对照本轮备份，勿覆盖后续用户修改；新增目录可保留，旧Schema兼容；不要整库恢复覆盖新增用户或后续交易。切回旧注册策略可显式配置managed并重启，原数据库开关和限额未删除。回放旗标及已应用迁移不变，不删除快照/账户。

本轮无模型调用，无真实交易副作用。目录同步使用一次有界供应商请求；供应商实际额度扣费金额未取得账单，不能报0元。其余为本地计算、QA数据、日志、源码/构建副本和17.5MB数据库备份。测试仅创建隔离qa_用户与新回放账户，不操作原用户资金；临时浏览器标签已关闭，原127.0.0.1标签未用于测试。

## 尚未完成和下一轮四包

1. 数据工程：有界增量补充需要的日线/估值/公告/未复权回放范围，加入供应商完整性、停牌/公司行动状态与版本审计；先明确缺价原因，不盲目全市场重导。当前仅三证券28交易日原始回放快照，目录5558不代表覆盖5558。
2. 研究任务与历史报告：现有研究仍为HTTP内执行、未形成持久化异步任务/幂等结果/取消与权限隔离历史报告的完整链路。下一包按版本迁移和任务状态机打通，不把当前页面结果当已保存报告。
3. 股票AI看盘：安全接通预算/步骤/工具白名单/超时取消/结构化输出/来源版本，先只读研究；原模型429 TPM问题未重测，不能宣称AI全部可用。不直接照抄期货保证金或交易工具。
4. AI模拟交易及发布验证：仅接独立回放账户，补能力边界、工具权限与幂等；随后执行完整端到端、容量、监控/告警、移动端和部署回退验收。当前无生产部署、完整移动/分隔线拖动人工回归及容量验证。既有深色主题html属性hydration告警仍在，已记录，未扩大范围改主题架构。

人工复核：打开行情→A股→默认板块→银行→股票；查看图表指标、估值/公告日期和覆盖，返回验证板块；全局搜索tcl；研究选择20日收益率、2026-07-13→09-11分别检验/回测，再用广区间检查缺价拒算。回放继续从交易页新建独立模拟账户、自填资金，仅用可用快照。确认期货切换仍保留期货合约与资金。截图 `logs/stock-boards-20261018.png`、`logs/stock-kline-verified-20261018.png`。
