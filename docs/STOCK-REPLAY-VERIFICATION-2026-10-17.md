# A 股日线回放与 K 线交付记录

历史记录：研究五项失败、图表及目录状态已有后续修复，最新真实结果见 `docs/STOCK-MARKET-EXPANSION-VERIFICATION-2026-10-18.md`。保留下文当时的失败证据，不将旧结果冒充当前验证。

2026-10-17。按用户选择实施“日线回放撮合，可自行输入模拟资金”，不接真实账户。**本轮图表与回放切片已实现并完成本机链路验证；整个双市场项目仍为部分完成，未通过生产上线验收。**

## 四个工作包

1. 股票标的及图表偏好按用户和登录盘独立保存；期货选择 / 广播拒绝股票代码。股票查询分页、错误重试和历史日期接通，期货三处历史查询限定 futures。未接通的股票 AI / 策略入口不挂载期货业务。
2. 借鉴 tick，复用已有 lightweight-charts、主题及指标方法：近端蜡烛、日周月、MA、成交量、MACD、悬停、缩放、复位和放大。行情标记前复权 / 手，回放标记不复权 / 股；历史截断及不完整首周期明确提示。
3. 授权原始 daily / adj_factor / stk_limit / trade_cal 经证券、字段、日期、单位、非有限值、OHLC、涨跌停和完整日历校验，写入不可变 SHA256 快照。独立 ReplayBase 四表只由版本迁移创建，旧行情、估值、财务、期货资金表不修改。
4. 用户及 JWT 登录盘 → 账户行锁 → Schema / 幂等摘要 / revision → Decimal 撮合 → 同事务现金、持仓、订单、游标、幂等响应 → ReplayView → 工作台 / 交易 / 持仓 / 委托 / 资产。回放图仅到游标、最多250根。资金可自填100至10亿元；新账户不清空旧历史。

全部回放调用 `/api/stock/replay`，不调用期货 `/api/paper`。跨用户 / 登录盘404；同键重复不重复执行，不同参数复用键或旧版本409。待处理最多100笔，始终展示全部待处理及最近200笔历史，防止老冻结委托无法撤单。核心操作带 X-Request-ID；日志不记密钥或持仓内容，SQL隐藏绑定参数。

验收中还修复了原登录恢复自取消：不再因自身 hydrating 变化丢弃 /me；在途请求去重、绑定捕获凭据，旧成功 / 失败不能覆盖新登录。替换令牌原子清除旧用户和 loaded，重新鉴权；恢复整链20秒超时、最多一次刷新。旧 getMeApi / request 契约保留。

## 金融口径与数据

- `stock-replay-v1`：普通沪深主板60xxxx / 00xxxx、百股、禁卖空、T+1。收盘提交，下一交易日开盘判断成交，推进后按当日原始收盘估值。FIFO成本包含买入费用；“次日可卖”不是当日即时可卖。
- 默认模拟佣金万三、最低5元、滑点千分之一，最多参与当日成交量10%；不做部分成交和盘口排队。该容量是事后日线约束，不是开盘时已知成交量，不能作为严格开盘可成交性证明；不会把未来成交量交给回放时点的下单用户。开盘买入涨停 / 卖出跌停保守拒单；限价不用日内高低反推成交路径。卖出费用不能使现金变负或占用其他冻结。
- 分批卖出按比例分摊成本到分，尾差保留到剩余批次，最终清仓取尽剩余成本；已复现并修复买300股、分三次卖100股造成累计已实现盈亏差一分的问题。现金差额与累计盈亏精确对平。
- 过户费2022-04-29起双边0.00001、此前0.00002；卖出印花税2023-08-28起0.0005、此前0.001。日期依据[政府转发的中国结算公告](https://jrj.wuhan.gov.cn/ynzx_57/xwzx/202204/t20220429_1964160.shtml)、[财政部公告](https://m.mof.gov.cn/czxw/202308/t20230827_3904226.htm)。仅支持2018年起，价格 / 金额到分，Decimal四舍五入。佣金是账户模拟假设，不是券商账单。
- 缺数据不当作停牌；因子变化代表未支持公司行动，409停止且不改账户，不伪造分红送转。当前 stock_basic 不提供历史证券身份时点保证，不能宣称无幸存者偏差。
- **成功快照：000001、600036、600519各28交易日，2026-09-01至2026-10-16，共84根原始日线。** 扩展2025-11-03起区间的批量导入两次遇供应商 TushareError，均未提交部分快照；单次诊断 daily 返回231行不代表全部源已验证。可选范围以已入库版本为准。

## 实际修改文件

- 后端接线：`backend/app/api/stock.py`、`backend/app/services/kline_history.py`、`backend/app/core/config.py`、`backend/app/core/database.py`、`backend/app/main.py`、`.env.example`。
- 回放实现：`backend/app/models/stock_replay.py`、`backend/app/schemas/stock_replay.py`、`backend/app/api/stock_replay.py`、`backend/app/services/stock_replay_data.py`、`backend/app/services/stock_replay_engine.py`、`backend/app/services/stock_replay.py`。
- 迁移 / 工具：`backend/migrations/20261017_001_stock_replay.sql`、`backend/scripts/migrate_stock_replay.py`、`scripts/import-stock-replay-data.py`、`scripts/test-stock-replay-local.py`。
- 后端测试：`backend/tests/test_market_history_isolation.py`、`test_stock_market_api.py`、`test_stock_replay_data.py`、`test_stock_replay_migration.py`、`test_stock_replay_engine.py`、`test_stock_replay_service.py`、`test_stock_replay_api.py`、`test_stock_replay_integration.py`（后七项同一 tests 目录）。
- 前端状态：`frontend/src/stores/stock-market.ts`及测试、`frontend/src/stores/app.ts`、`frontend/src/stores/auth.ts`、`frontend/src/hooks/sync.ts`、`frontend/src/hooks/auth.ts`。
- 图表组件：`frontend/src/components/market/stock-chart-data.ts`及测试、`stock-kline-chart.tsx`、`stock-market-view.tsx`、`stock-universe.tsx`、`contract-search-dialog.tsx`（后四项同 market 目录）。
- 前端接口：`frontend/src/lib/stock-api.ts`、`market-routes.ts`、`stock-replay-api.ts`、`auth-session.ts`及各自 `.test.mjs`，`frontend/src/lib/api.ts`及 `.test.mjs`。
- 回放组件：`frontend/src/components/trading/stock-replay-workspace.tsx`、`stock-replay-state.ts`及测试；`frontend/src/components/layout/stock-feature-boundary.tsx`、`top-navbar.tsx`。
- 页面：`frontend/src/app/layout.tsx`、`frontend/src/app/(auth)/login/page.tsx`、`frontend/src/app/(main)/layout.tsx`，该 main 下 `market/page.tsx`、`trading/page.tsx`、`positions/page.tsx`、`orders/page.tsx`、`assets/page.tsx`、`dashboard/page.tsx`、`profile/page.tsx`。
- 文档：本记录、两份本轮实施计划及原功能测试报告的后续状态链接。本机私有 `.env.runtime` 旗标已启用，公开示例默认false；不公开密钥。未改tick、未新增依赖、未提交或创建分支。

## 实际验证

| 命令 / 检查 | 真实结果 |
|---|---|
| PG17 `pg_dump.exe -h 127.0.0.1 -p 5433 ... -Fc`，凭据仅经进程环境 | 初次连接拒绝；恢复专属5433实例后成功，备份17,485,117字节，未动5432。 |
| `backend/.venv/Scripts/python.exe backend/scripts/migrate_stock_replay.py`，重复，再 `--check` | 首次applied=true，重复false，最终结构及checksum通过；版本20261017_001_stock_replay。 |
| `backend/.venv/Scripts/python.exe scripts/import-stock-replay-data.py --codes 000001,600036,600519 --start-date 20260901 --end-date 20261016` | 三个真实快照成功，扩展失败未覆盖它们。 |
| backend `.venv/Scripts/python.exe -m pytest tests/test_stock_replay_engine.py tests/test_stock_replay_service.py tests/test_stock_replay_api.py tests/test_stock_replay_integration.py tests/test_stock_replay_data.py tests/test_stock_replay_migration.py -q` | 81通过。覆盖费用、分批FIFO尾差、T+1、限价、冻结、幂等、缺数据 / 公司行动回滚、标准化；SQLite测试执行真实ORM，不替代PG锁测试。 |
| backend `.venv/Scripts/python.exe -m pytest -q --tb=short --junitxml=../logs/stock-replay-full-suite.xml` | **1432通过、5失败、3既有跳过、20警告**；失败均为原研究金融回归，未删除 / 放宽断言。日志 `logs/stock-replay-full-suite.log`。 |
| frontend `node --test` 九份相关 `.test.mjs` | **33通过，0失败 / 跳过**，`logs/stock-replay-node.log`。九份为stock-market、stock-chart-data、market-routes、stock-api、api、websocket、stock-replay-api、stock-replay-state、auth-session。 |
| frontend `node node_modules/typescript/bin/tsc --noEmit --incremental false` | 通过。 |
| 隔离副本 `logs/stock-replay-build` 的 `node node_modules/next/dist/bin/next build` | 通过；副本不含.env文件，复用依赖，不影响dev服务。日志 `logs/stock-replay-build.log`。 |
| `backend/.venv/Scripts/python.exe scripts/test-stock-replay-local.py` | **18项真实HTTP / PG检查全通过**：自填1万，原始开盘、费用、现金、净值独立对照，并发下单 / 推进幂等，权限 / 盘隔离、撤单、裸卖空、分页与大小写搜索、期货资金不变。`logs/stock-replay-http-results.json`。 |
| 本机HTTP健康 | 3000/login、8002/health、8110/health均200；误用8002/api/health返回404。后端进程ok但market.ok=false，休市live行情不能当作全健康。 |

浏览器：独立QA用户自填五万元；买100股→冻结→推进成交→次日可卖100→卖100→推进无持仓→新单撤销并释放冻结；持仓 / 委托 / 资产联动。股票→期货恢复rb2701，返回股票恢复600036，回放账户历史和刷新恢复正常。另建双股票账户，人工选2026-10-14起点，图表26根都不晚于游标。此前逐项验证日周月、指标开关、放大 / ESC / 复位、深浅主题。股票个人页不挂期货保证金功能。

截图 `logs/stock-replay-verified-20261017.png`：1280×900桌面断点、五万元、双标的、真实历史图表。仍有既有深色主题hydration警告；未声称浏览器零告警。独立复审发现并修复并发读一致性、老pending被挤掉、卖出费用负现金和会话错配，最终审查范围无剩余P1/P2；不是整个旧产品生产审计。

## 兼容、回退、成本

顺序：备份→迁移→check→有界导入→开启STOCK_REPLAY_ENABLED→重启→QA验收。默认关闭不读取未迁移新表，availability disabled、操作503，不回退到期货账户。旧接口字段、期货uq_code_date写入及live / virtual枚举保留，两种登录盘下A股都只是回放。

无旧表删改或历史覆盖。回退关闭旗标并重启，保留新表 / 历史，**不整库恢复覆盖用户新数据**。目录无Git；图表前端原副本在 `logs/stock-alignment-source-backup/frontend-src`，不能声称有完整Git回滚。数据库备份 `logs/before-stock-replay-20261017.dump`。

成本为本机计算、新表和约17.5MB备份；成功首批15次数据调用，另有有界扩展失败及一次诊断读取。供应商费用按用户套餐，未核算金额。**本轮无模型调用，无真实资金 / 经纪商交易副作用，只操作QA新账户。**

## 未完成与人工路径

- 五项研究回测仍失败：净值时序、缺失因子、持仓缺价非有限值、联合中性化、频率 / 权重校验。其输出不能视为正式可用；新回放不调用该计算链。
- 股票AI看盘 / 自动交易尚未接通，继续安全封闭；此前模型429 TPM限流未验证恢复，本轮不重复调用。旧期货自动任务时段、个人信息占位等问题仍见原测试报告，不能作为本轮通过项。
- 公司行动、停牌识别、其他板块、盘中撮合、全历史时点证券状态未实现；当前仅验证三证券快照。不宣称全A股或可实盘。
- 未做生产部署、性能容量、正式监控 / 告警验收、真实账户接入及完整移动端回归。全量不全绿，不能批准上线。

人工：打开 http://127.0.0.1:3000/trading → 登录自己的账号 → A股 → 新建账户 → 自填金额 / 快照 / 有效历史交易日 → 买100股 → 推进 → 查成交 / 持仓 / 资产 → 卖出或撤单 → 切期货再返回。用户原账户未被QA测试创建或下单。

下一轮三包：修复五项研究金融回归；补授权历史覆盖及公司行动 / 停牌；接通有权限、预算和终止条件的股票AI工作区并回归。仍不接真实账户。
