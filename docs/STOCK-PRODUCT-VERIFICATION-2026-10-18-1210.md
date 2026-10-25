# 12:10续做：研究、资金、工作台与盘后行情真实检查点

## 状态和运行实例

**部分完成，不是全系统上线验收。** 五个工作包已落盘；本轮分别验证确定性金融口径、数据库并发、真实HTTP和部分浏览器交互。原用户页面没有被修改。

- 原服务仍为前端3000/PID11940、后端8002/PID19872，数据库为专用PG5433。8002仍是09:05启动的旧进程。
- 停止旧后端并隐藏重启的命令被执行环境策略拦截，没有执行；没有改用其他杀进程方式绕过。已向用户请求手动重启8002，尚未收到确认。
- 为不影响期货撮合和原服务，验收临时启动8003：真实项目路由、真实PG/Redis、真实研究worker；**没有启动第二套期货调度器、模拟撮合器、自动数据采集器或自动建表**。HTTP校验结束后已通过该工具会话中断关闭；8003无监听。
- 3001隔离前端启动命令也被策略拦截，未运行。浏览器使用原3000的热更新源码，**因此截图行情覆盖仍为旧8002的299只，不是5548只新EOD覆盖**。新EOD接口通过8003真实HTTP验证，不能把它称为原页面已生效。
- `.env.runtime`已保存`STOCK_RESEARCH_JOBS_ENABLED=true`、`STOCK_EOD_ENABLED=true`、`STOCK_EOD_SYNC_ENABLED=true`；旧进程不会自动重读这些环境变量。重启并通过迁移检查后才生效。正式示例的两个新EOD旗标仍默认false。
- 一次性`a-12-10`已实际触发。02:05、07:10续做保持活动，尚未达到停止条件。没有提交或创建分支。

**17:29追加探测：** 3000、8002、8003均未发现监听，原PID也未返回；12点轮验收时的运行状态不能当作当前服务仍在线的证据。停止原因未确认，本轮被拦截的重启命令没有执行。下次应先核实本机服务状态再运行启动/验收，不盲目复用旧PID。

## 五个工作包及金融口径

### 1. 研究财报选择、覆盖预检与恢复参数

- 共享财报选择器：同公告日取较新报告期，晚公告的旧报告期不使面板倒退；新期NULL不拿旧期补；冲突拒绝；公告当天不作为此前可得信息。旧同步API与新快照采用同一选择逻辑。
- 方法版本升级为`stock-research-2026-10-18.v3`。快照继续对全部原始行做指纹，不仅对被选中的财报做指纹。
- 已存在任务的幂等摘要按其保存的方法/流程版本验证；同键同原参数保持原ID和旧版本，不因升级重算旧报告；异参数409。
- 新认证接口`GET /api/stock/research/coverage`：只读RR聚合、SQL/HTTP超时、最近61个**真实观测日**建议区间、缺价与面板规模披露，日期/未来区间422，失败503而非空数组。
- 前端取消旧预检、防跨用户/盘串响应；无数据、超限或预检失败禁止新提交；恢复任务一次性恢复日期、种类、因子、权重、费用及预处理参数，不被后续轮询覆盖。
- 单测和真实HTTP已验证。新覆盖/参数恢复浏览器链路仍待8002重启验证。

### 2. 期货自定义模拟资金与撮合并发

- 新增`POST /api/paper/account/deposit`，输入为十进制金额字符串和UUID幂等键。无月/日额度，只保留正数、两位小数和数据库Numeric(18,2)可存储边界。
- JWT决定用户和盘；PG统一顺序：用户/盘advisory事务锁→刷新账户行锁→刷新订单行锁→复核订单状态。首次开户也在锁内重查。
- 充值、流水同事务，同键重试不重复入账，异金额409；不清空冻结、持仓、盈亏、旧领取记录。新入金统计包含自定义流水。
- 修复撮合器旧ORM余额覆盖充值、撤单后陈旧订单仍成交、同单重复成交；跨账户批次稳定排序，收齐行情后再锁资金，不持资金锁等待Redis行情。
- 前端移除月度领取入口，保留旧claim接口兼容；未知结果保存原幂等键重试，切用户/盘隔离；20秒超时。
- 真实PG验证：12并发同键仅一次入账；4撮合+8充值重叠守恒，只有一次成交、一笔手续费、一份持仓。真实HTTP8次重试仅一次入账，用户/盘/A股账户隔离通过。只动QA用户资金，未改真实用户余额。
- 新资金入口完整浏览器验证仍待8002重启；旧8002尚无deposit路由，不能假称页面充值成功。

### 3. 股票工作台、六卡板块与Tick式图表

- 工作台不再把回放账户创建当作市场总览：目录/行情覆盖、交易日、可比样本涨跌、行业排行、成分股票搜索、个股详情使用同一真实行情链路。
- 板块桌面六列，小屏两/三列。没有复制Tick另一套后台，也没有新增依赖。
- 详情复用已迁入的日/周/月、MA/BOLL/VOL/MACD/RSI/KDJ、量能对比、视窗和分隔线等；指标窗口不足显示空缺，不制造分时、分钟或异动数据。
- 新K线响应带`price_adjustment/source/volume_unit`；有原qfq历史时继续只读该序列，不把未复权EOD追加在qfq尾部。没有qfq历史才读明确标识的EOD原始序列。只有一日就显示一日，不伪造完整历史。
- 页面新增真实盘后来源、缺行情/目录未匹配数、可展开代码、版本及采集时间；新字段可选，旧响应不会凭空出现零缺口。
- 浏览器已实际验证：工作台搜索600519→详情→RSI/KDJ开关；板块六卡一行。由于旧后端未重启，这些浏览器验证使用原qfq行情，不证明新EOD页面已接通。

### 4. 授权渠道盘后全市场快照与每日增量任务

- 仅使用用户现有配置的Teajoin兼容账号。业务来源调用本轮共2次：一次daily、一次stock_basic；首次同步复用已下载daily，没有重复下载全市场历史。
- 实际2026-10-16来源daily **5567**条、has_more=false；目录stock_basic仍是**5558**只，匹配**5548**只；**10只缺当日行情、19条代码不在目录**。保存为`partially_completed`，不能称全覆盖。
- 10只缺价：000016、002388、002717、002731、300773、301314、600293、601059、601198、688496。
- 19条目录未匹配保留在可追溯原始快照，但市场可用价和K线投影均排除；以后仅补目录也不会默默认证旧unknown数据。已知上市前价格直接拒绝。
- 行情未复权；量为手，成交额由千元转元；涨跌使用供应商除权昨收参考价，不把原始两日收盘直接相除，也没有制造一根“昨收K线”。单位依据[日线数据官方字段文档](https://tushare.pro/document/2?doc_id=27)，兼容供应商授权范围仍需运营确认。
- 独立EodBase/版本迁移/运行批次与JSONB快照；不写旧qfq研究表。带供应商、标准标识、时区、频率、单位、采集时点、方法、目录/数据版本、缺口和使用边界。
- worker北京时间16:30后每5分钟检查；按日期持久领取，一日最多3个上游请求（日历/目录/daily），每次20秒、总执行90秒、无自动重试。休市由来源日历确认；启动不批量补历史。
- 原生异步HTTP可取消，不在超时后留下线程继续下一笔收费请求；逐块限制8MiB解压响应并校验字段唯一性。失败/取消不提交迟到结果，不覆盖此前有效快照；过期任务不自动续费。
- K线由数据库JSONPath投影单证券，避免返回上百份全市场JSON到应用层。错误503、request_id、无缓存，不用空行情掩盖失败。
- 真实PG并发/回滚、真实HTTP数据来源和单位、重复CLI零来源调用均通过。自动worker生命周期以专项测试验证；**真实收盘时点运行和原8002的自动任务尚未验收**。

### 5. 自然语言转可确认配置草稿

- AI交易、量化创建和历史回测原表单增加“用语言填写配置（规则解析）”。明确不是大模型生成，没有模型调用费用。
- 规则解析→版本化严格Zod Schema→预览字段/澄清→用户确认填表→原创建按钮；不会直接创建、下单或启动，任务应用草稿时`auto_start=false`。
- 支持实际目录合约、中文金额、日期/周期/方向/整数手数，以及SMA双均线、N根突破、MACD、KDJ、BOLL。真实模型列表由当前用户读取，不猜model_id。
- 模糊主力、EMA、套利网格、组合条件、非法日期、超引擎边界参数要求澄清；不让后端静默clamp或把“分钟N日”冒充“N根K线”。
- 浏览器实际验证：`rb2701，日线，双均线，快线5，慢线20，只做多，2手，资金十万元，2026年7月1日至2026年9月30日`→预览→只填表，日期/金额/手数精确；量化15分钟版本应用后“创建后立即开始”从勾选变为未勾选。最终取消，没有创建任务或发出订单。
- AI模型说明入口有测试，尚未做新付费模型调用及AI执行端到端验证；任意自然语言策略组合不在本轮支持范围。

## 实际修改文件

以下均相对仓库根；当前无.git，app/src按09:05源码副本逐文件SHA对比确认。

后端：

```text
backend/app/api/paper.py
backend/app/api/stock.py
backend/app/api/stock_market.py
backend/app/api/stock_research_jobs.py
backend/app/core/config.py
backend/app/main.py
backend/app/models/stock_eod.py
backend/app/schemas/paper.py
backend/app/schemas/stock_research_jobs.py
backend/app/services/paper/account_svc.py
backend/app/services/paper/matching/matcher.py
backend/app/services/stock_eod.py
backend/app/services/stock_eod_sync.py
backend/app/services/stock_factors/coverage.py
backend/app/services/stock_factors/engine.py
backend/app/services/stock_factors/financial_selection.py
backend/app/services/stock_factors/snapshots.py
backend/app/services/stock_market.py
backend/app/services/stock_research_jobs.py
backend/migrations/20261018_003_stock_eod.sql
backend/scripts/migrate_stock_eod.py
backend/tests/test_paper_custom_funds.py
backend/tests/test_paper_matching_funds.py
backend/tests/test_stock_eod.py
backend/tests/test_stock_eod_integration.py
backend/tests/test_stock_eod_lifecycle.py
backend/tests/test_stock_eod_migration.py
backend/tests/test_stock_eod_source.py
backend/tests/test_stock_financial_selection.py
backend/tests/test_stock_research_coverage.py
backend/tests/test_stock_research_job_service.py
```

前端：

```text
frontend/src/app/(main)/assets/page.tsx
frontend/src/app/(main)/dashboard/page.tsx
frontend/src/components/ai-trading/form/create-quant-dialog.tsx
frontend/src/components/ai-trading/form/create-task-dialog.tsx
frontend/src/components/backtest/backtest-form.tsx
frontend/src/components/dashboard/stock-dashboard.tsx
frontend/src/components/dashboard/stock-dashboard.test.mjs
frontend/src/components/factor-research/factor-research-workspace.tsx
frontend/src/components/factor-research/factor-research-page.test.mjs
frontend/src/components/factor-research/research-coverage-panel.tsx
frontend/src/components/factor-research/research-coverage-panel.test.mjs
frontend/src/components/market/stock-board-browser.tsx
frontend/src/components/market/stock-board-browser.test.mjs
frontend/src/components/market/stock-quote-detail.tsx
frontend/src/components/market/stock-quote-detail.test.mjs
frontend/src/components/portfolio/virtual-funds-panel.tsx
frontend/src/components/portfolio/virtual-funds-panel.test.mjs
frontend/src/components/strategy-draft/task-language-draft-panel.tsx
frontend/src/components/strategy-draft/task-language-draft-panel.test.mjs
frontend/src/components/strategy-draft/task-language-forms.test.mjs
frontend/src/lib/paper-api.ts
frontend/src/lib/paper-api.test.mjs
frontend/src/lib/stock-api.ts
frontend/src/lib/stock-api.test.mjs
frontend/src/lib/stock-market-api.ts
frontend/src/lib/stock-market-api.test.mjs
frontend/src/lib/stock-research-coverage.ts
frontend/src/lib/stock-research-coverage.test.mjs
frontend/src/lib/task-language-draft.ts
frontend/src/lib/task-language-draft.test.mjs
```

脚本/配置/记录：`.env.example`、本地`.env.runtime`、`scripts/sync-stock-eod.py`、`scripts/test-paper-custom-funds-local.py`、`scripts/test-stock-eod-local.py`、`scripts/test-stock-product-local.py`、`scripts/test-stock-expansion-local.py`、`scripts/test-stock-research-jobs-local.py`、本文件和两份计划检查点。日志中的临时来源缓存、QA启动器、源代码/构建副本及测试报告不是部署代码，不含需公开的密钥。

## 验证证据与失败记录

在仓库根执行Python命令；Node命令在frontend目录。

|实际命令|结果|
|---|---|
|`backend/.venv/Scripts/python.exe -m pytest backend/tests -q --junitxml=logs/stock-alignment-0905-baseline.xml`|基线1645通过、3既有跳过、226警告|
|`backend/.venv/Scripts/python.exe -m pytest backend/tests -q --junitxml=logs/stock-product-1210-final.xml`|1756通过、3既有跳过、502警告；之后K线错误追踪又新增1项回归并单独验证，最终复验见下方追加|
|`backend/.venv/Scripts/python.exe -m pytest backend/tests -q --junitxml=logs/stock-product-1210-release-check.xml`|最终1757通过、3既有跳过、502警告，60.05秒；含K线错误追踪新增回归|
|`node --import ../logs/node-alias-register.mjs --test (Get-ChildItem -Recurse src -Filter *.test.mjs \| ForEach-Object FullName)`|285通过，0失败/跳过|
|`node node_modules/typescript/bin/tsc --noEmit --incremental false`|退出0|
|隔离源码副本内`node node_modules/next/dist/bin/next build`|退出0、路由生成，但standalone追踪仍出现Windows EPERM symlink警告，不是可部署产物验收|
|`backend/.venv/Scripts/python.exe backend/scripts/migrate_stock_eod.py`|首次applied=true；重复applied=false|
|`backend/.venv/Scripts/python.exe backend/scripts/migrate_stock_eod.py --check`|版本/checksum/严格PG结构检查通过|
|`backend/.venv/Scripts/python.exe scripts/sync-stock-eod.py --date 2026-10-16 --cached-daily logs/stock-eod-source-20261016.json`|首次部分完成；重复already_recorded、source_calls=0|
|`backend/.venv/Scripts/python.exe scripts/test-stock-eod-local.py`|主流程重跑17项PG通过，临时schema删除、public数据及序列不变|
|`backend/.venv/Scripts/python.exe scripts/test-paper-custom-funds-local.py`|主流程重跑17项PG通过，临时schema与QA资源清理|
|`$env:QA_BASE_URL='http://127.0.0.1:8003'; backend/.venv/Scripts/python.exe scripts/test-stock-expansion-local.py`|19项真实HTTP通过|
|同一QA_BASE_URL执行`... scripts/test-stock-research-jobs-local.py`|23项真实HTTP/真实研究进程通过|
|同一QA_BASE_URL执行`... scripts/test-stock-product-local.py`|14项真实HTTP通过|

- 原1648个基线用例ID在1759个复验用例中无遗漏；没有删除、跳过或放宽关键断言。更新旧HTTP脚本的方法版本精确断言v2→v3；增加可选QA_BASE_URL，默认仍为3000。
- 明确红→绿：缺失EOD模块/SQL投影/迁移/生命周期；重复源字段；上市前行情；原生HTTP取消；K线失败500缺trace→503带request_id；资金并发旧ORM覆盖/撤单误成交/同单重复成交；旧幂等键升级冲突；财报择期等。
- 两次PG初稿安全检查失败：枚举search_path、regclass序列显示方式；修正测试隔离方式后真实重跑，失败时finally也已清理，未写public业务数据。
- QA研究HTTP首次失败因临时启动器未把worker写入app.state，补齐验收装配后23项通过。不是跳过可用性断言。
- 产品HTTP初稿误以为2020无本地数据、000004在上市目录；真实数据推翻这些假设。改为按实际最早日期选空窗、使用真实存在的000006，再全部通过。多次测试充值仅作用于明确QA账户，非真实用户。
- 最初取消源红测中两个分支误走了真实网址但仅带合成无效测试token，未使用用户凭证；随后已改为原生异步MockTransport边界，取消、超额和重复字段均本地测试。业务来源真实账号调用仍为上述2次，无自动重试。
- 原18项回放HTTP本轮未重新运行；保留上轮证据，不能当本轮已重验。新代码的后端全套回放相关单测已运行。
- 警告包括原有`datetime.utcnow`弃用和旧AsyncMock未await。新增路径调用旧fixture后警告数上升，未关闭警告；本轮不顺便大范围改旧测试。

## 迁移、备份与回退

- 源码基线：`logs/stock-research-alignment-backup-20261018-0905`。
- 迁移前PG备份：`logs/before-stock-eod-20261018-1210.dump`，17618393字节；只用于灾难恢复，不作为常规回滚覆盖用户后续数据。
- 新迁移`20261018_003_stock_eod`已首次/重复/check真实PG验证，checksum=`32c748cd87746a1117457dafeb8adaabd473557057dadaad74d9f15ba70910fe`。没有编辑已应用的回放/研究迁移。
- EOD真实数据版本=`2b85dbcca6e90c0899ceaa22208a6f3e9361f843e0c07e1c2f12811bc89204bd`；目录版本=`2f726d9bc510d80e9384e145015d8b638f65630362f76e990d8b63201747f532`；批次ID=`14831b03-e618-4f9d-a89b-f6db88f3c62b`。
- 回退：关闭EOD两个旗标、研究任务旗标并正常停止相应worker，再回退应用；保留新增表和不可变报告/充值流水，不删表、不重置资金、不恢复整库。旧claim接口仍可用；回退旧资金服务前必须停止相关撮合写者，避免重新引入已修并发问题。
- EOD失败日期没有无限重试入口。后续补数须独立受控版本化修复，不手工把失败状态重置来绕过预算。
- 无新增依赖；无模型调用。来源额度/费用以供应商实际计费为准，不把“最多3请求/日”伪称免费或固定金额。

## 人工验收与剩余风险

1. 先由用户正常重启8002，确认新进程启动、迁移检查通过；不要再使用旧PID为新源码已生效证据。
2. 原3000登录→A股→工作台/行情：应显示5548/5558、来源5567、缺10/未匹配19、实际交易日；折叠查看代码、版本/采集时间。000006无qfq历史应只见真实EOD一根；600519仍是原qfq历史。
3. 期货→个人/资产→输入自定义金额；刷新核对余额/流水，不清冻结和盈亏；超时只用原键重试。只做模拟，不接实盘账户。
4. 因子研究：使用真实最近覆盖区间；选回测/费用后新建→历史报告恢复→刷新验证参数/种类不丢；过大窗口明确阻止，缺持仓价仍拒绝。
5. 期货AI交易/量化/历史回测：语言解析→预览→确认填表；检查“创建后立即开始”关闭。若只是验收填表不要按最终创建/开始，以免划拨模拟资金或触发模型成本。

已保存截图：`logs/stock-six-cards-20261018-1210.png`、`logs/stock-workspace-kline-20261018-1210.png`、`logs/task-language-draft-20261018-1210.png`、`logs/quant-language-draft-20261018-1210.png`。截图是原3000/旧8002组合，不代表新EOD界面验收。

已知阻断/待验证：8002尚未重启；19个源代码与目录冲突、10个缺价；只有原约300股的qfq历史，全市场盘后快照不能替代全市场历史研究覆盖；PIT修订/历史行业/供应商转授权未认证；真实收盘worker需观察；AI模型额度和端到端未复测；Windows standalone EPERM；原主题boot脚本导致SSR的data-theme/class hydration警告（浏览器已定位RootLayout，非本轮新图表异常），未擅自扩展修复。

## 下一轮四包

1. 8002重启后补完整浏览器/代理HTTP/回放18项回归，验证覆盖恢复、入金和EOD来源展示，观察一次盘后运行。
2. 受控来源对账：19代码/10缺价的身份、上市/退市和来源版本；建立有预算、可恢复的失败/漏日补数，先确定授权再扩充历史，不全库盲导。
3. 股票只读AI与报告引用链路：Schema工具、用户权限、数据版本、最大步骤/token/费用、取消与真实固定评测；不开放模型直接交易。
4. 发布验收：standalone产物、主题hydration、健康与错误可观测、迁移/应用回退演练和人工验收；再评估国际贵金属/外汇独立模拟市场，当前没有因讨论而擅自接入。
