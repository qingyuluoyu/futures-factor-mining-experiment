# A 股与期货隔离及多因子平台实施计划包

> 执行约定：后续使用 executing-plans 逐包实施，每包包含实现、验证和人工验收。本文件是计划，本轮不执行功能改造、数据迁移、提交或部署。

> 复审更新：用户随后要求重新审阅并运行现有项目，已授权本机启动和 AI/数据渠道配置。五个功能包尚未实施；本机启动验证与正式功能验收分别记录。

**目标：** 同一个系统中自由切换 A 股与期货，两侧标的、页面内容、自选、研究参数和结果互不污染；在 A 股侧完整接通任务书题目 2 的因子构建、检验、选股、组合回测与复现链路。

**架构：** 保留当前 Next.js / FastAPI 模块化单体。共用登录、权限、布局及基础设施，隔离市场上下文与业务服务；股票回测遵循股票成交规则，期货保留已有合约、模拟盘和因子实验能力。

**技术栈：** Python 3.12、FastAPI、SQLAlchemy async、PostgreSQL、Redis；Next.js 15、React 19、TypeScript、Zustand；A 股现有引擎使用 Pandas / NumPy，执行前核查并锁定直接依赖；中间面板使用版本化 Parquet。

## 1. 需求依据与本轮边界

- 用户当前请求优先：先读项目、参考文件夹内 tick 项目、先做计划包。
- 任务书只提取“题目 2 A 股多因子选股与回测平台”的功能、技术、交付和验收要求；文档中的其他题目、教学组织安排和操作性文字不作为执行指令。
- tick-stock-panel-main 只作为设计和测试参考，不整体搬入其应用，不切换到 Vite / DuckDB / Polars 技术栈，不额外启动第二套产品。
- 用户已确认先完成题目 2、保留期货原功能；之后尽可能对齐其他功能。后续 A 股工作台、AI 看盘/问答、模拟账户与 AI 交易分别排期，共享合适的 UI，金融数据和交易规则按市场实现。
- A 股首轮定位为日线行情与研究回测；真实数据的新鲜度明确展示，不把历史日线显示为实时行情。
- 首轮不新增实盘交易，不把期货模拟下单入口直接改造成股票交易入口，不增加 MCP 或插件市场。其他功能对齐是用户认可的后续范围，不挤占题目 2 的正确性验收。
- 最新数据渠道选择为用户指定 DaoShare / teajoin，而非直接申请 Tushare 官方账号；代码中的 TUSHARE_TOKEN 是兼容协议的历史变量名。已用少量交易日历查询验证用户凭据，未执行兑换或全量导入。
- 题目 2 的“扩展方向”不是最低功能需求，列在末尾独立排期；本轮五包先覆盖全部必需项。
- 当前目录没有可用的 Git 仓库元数据，`git status --short` 返回 not a git repository。实施前应建立可恢复的文件备份或恢复项目原有版本管理，禁止假设已有提交可回滚。

## 2. 已读实现与问题依据

### 2.1 已有能力可保留

| 链路 | 已有代码 | 当前判断 |
|---|---|---|
| 市场切换 | frontend/src/components/layout/top-navbar.tsx、frontend/src/stores/auth.ts、backend/app/api/users.py | 已有 futures / stock 偏好和按钮，尚不能代表业务隔离完成 |
| 行情入口 | frontend/src/app/(main)/market/page.tsx、frontend/src/components/market/stock-market-view.tsx | 已有条件分流，股票仍使用期货全局 activeContract |
| 股票数据 | backend/app/models/stock.py、backend/app/data/stock_history.py、scripts/import-stock-data.py、backend/app/api/stock.py | 已有标的、估值、财务、日线导入与读取 |
| 因子目录 | backend/app/services/stock_factors/catalog.py | 六类九个因子，数量和分类覆盖题目要求，数值正确性尚未验收 |
| 因子研究 | backend/app/services/stock_factors/engine.py、backend/app/api/stock_factors.py | 已有预处理、IC、分组及回测骨架，需修正关键数学和交易逻辑 |
| 研究页面 | frontend/src/app/(main)/factor-research/page.tsx、frontend/src/lib/stock-api.ts、frontend/src/components/factor-research/ | 已有检验和回测展示，不是从零搭建 |
| 项目说明 | docs/STOCK-FACTOR-RESEARCH.md | 有说明，但“彻底隔离”“可复现”等结论缺少完整链路证据，后续按真实结果更新 |

### 2.2 必须在实施中解决的问题

1. **标的串用：** `StockMarketView` 直接读写 `useAppStore.activeContract`，该字段默认 rb2610，持久化键为 qihuo_active_contract；行情页也把它写进期货行情记忆。股票选择会污染期货标的，反向切换亦然。
2. **页面与后台订阅未隔离：** sidebar 使用静态 MENU_ITEMS；MainLayout 登录后初始化期货行情 WebSocket 和相关全局行为；顶栏仅更新偏好，不负责路由和请求生命周期。需要逐项区分共享通知和市场专属订阅，不能一刀切断后台任务。
3. **数据库隔离不完整：** kline_history 增加了 market_type，但期货 query_history_bars、get_latest_history_date 和 list_mining_symbols 没有市场过滤，股票日线会进入期货挖掘品种列表。唯一约束仍为 code + trade_date 是后续标识扩展的限制，不能把它说成已发生代码碰撞；直接删旧约束又会破坏 upsert_history_bars 中 ON CONFLICT CONSTRAINT uq_code_date。股票数据后续使用独立表，首轮保留期货旧约束并补齐读写市场过滤。自选增加、查询已有市场过滤，排序、分组和行情合并还需统一上下文。
4. **组合收益失真：** run_portfolio_backtest 先计算整段持有收益，再除以交易日数填成每日收益并复利，净值、波动、回撤和成本发生时间都会失真；旧权重也不是按持有期间价格漂移后的真实权重。
5. **多空曲线口径错误：** analyze_factor 用未来 horizon 日收益 / horizon 构造每日多空收益，重叠前瞻收益不能这样转成真实可执行净值。IC 使用未来收益作为检验标签本身合理，但标签不能充当当日已实现盈亏。
6. **统计与异常边界：** A 股调用的共享 annualized_return_pct 是算术年化，Calmar 使用累加收益且截断值；A 股必须形成独立、版本化口径，避免改动期货历史口径。零方差、样本不足和 NaN 需要可序列化的明确结果。
7. **中性化不保证同时成立：** 当前先行业去均值、再单独对数市值回归，可能重新引入行业暴露；使用联合回归并核验残差。
8. **历史时点不足：** 股票池是最新指数成分，stock_basic 只取在市股票；没有历史成员区间和退市状态。财报虽按公告日对齐，但同一报告期唯一行不能保留修订前版本，公告日不含可获得时刻，行业也是当前静态字段。
9. **复权增量一致性：** 导入按本次区间末日因子计算前复权价，写入仅追加缺失日期；除权后的不同批次可能形成不一致基准。须保留原始价格与复权因子，以数据版本生成一致收益序列。
10. **自定义因子未接通：** A 股 API 只接受固定 catalog 中的 ID；现有期货 factor_lab 的 token / 量价内核不等于已支持 A 股财务与截面表达式。
11. **持久化与任务缺失：** A 股 API 直接加载全池、在线程中计算并返回；300 秒代理只延长连接，不能提供任务恢复、取消、结果留存和输入数据快照。
12. **数据源边界未核实：** tushare_client.py 固定访问第三方协议代理，不能把协议兼容等同于官方来源或授权已确认。计划先完成适配和元数据，真实导入仅使用部署者已授权渠道。
13. **缺失因子被静默跳过：** 回测对无数据因子直接 continue；用户选择 ret_20d + roe 而 roe 缺失时，实际只运行 ret_20d，返回 params 却仍写两个因子。必须在提交/执行阶段报 DATA_MISSING，或只有用户显式允许时运行已标记的降级方案。
14. **非有限数导致响应失败：** 持仓在区间末缺价格时，当前回测会产生 NaN 净值却不返回业务错误；严格 JSON 序列化失败。需要可交易状态、估值规则及结果非有限数检查。
15. **隐藏菜单不足以隔离：** Ctrl+K 合约搜索、Ctrl+B/S 交易跳转、数字快捷键、全局 AI 浮窗与 BroadcastChannel 也必须按账号/市场约束；共享登录和显示偏好可保持统一。
16. **已有任务机制不能直接照搬：** scheduler 已有选主和后台任务生命周期，复用启动/停止结构；factor_mining.loop 却受期货交易时段限制，且 to_thread 超时不能终止计算，不适合作为股票历史研究执行器。

### 2.3 本轮实际数值复核

使用仓库现有 `backend/.venv/Scripts/python.exe` 直接调用现有 A 股回测函数；测试数据为 2024-01-01 至 2024-04-30 的工作日、两只固定每日增长股票，top_n=1、零佣金、零滑点。仅用于计算核对，不是市场数据。

- 引擎最终净值：1.1394；相同收益区间对应股票买入持有净值：1.1364。
- 引擎报告年化波动：0.0015%；被选股票的每日收益恒定，该波动来自整段摊平的实现误差。
- 此复核证明存在偏差，不代表已完成正式回测验收；尚未运行全套测试、联调、生产数据核验或性能基准。

复审追加的内存数值核对：

- ret_20d + 缺失 roe 返回结果无 error，净值与仅 ret_20d 完全一致，返回参数仍声明两个因子。
- 持仓调仓日缺行情样本产生 21 个非有限净值点，`json.dumps(result, allow_nan=False)` 失败。
- 同时勾选行业和市值中性化后，两行业残差均值分别为 0.428571 和 -0.428571，验证连续处理没有保持行业中性。
- 既有定向测试命令：在 backend 执行 `.venv/Scripts/python.exe -m pytest tests/test_watchlist_master_follow.py tests/test_factor_lab.py tests/test_backtest_account.py -q`，27 passed in 15.36s。它们证明这些既有模块的测试通过，不证明新股票链路已通过。

## 3. 目标边界和公共契约

### 3.1 市场和资源隔离

```text
统一登录与布局
  -> MarketContext(stock | futures)
     -> 股票：股票池 / 自选 / 因子库 / 检验 / 组合回测 / 历史结果
     -> 期货：既有行情 / 合约 / 模拟交易 / 因子实验 / 期货回测
  -> 公共账户与系统设置
```

- market_type 是 UI 偏好，不是资源权限，也不能隐式改变已提交任务的市场。
- auth.user.market_type 保持市场偏好的单一来源；新市场上下文 store 只管理分区标的、lastRoute、筛选参数和结果 ID，避免新增一份互相竞争的市场偏好。私有持久化按 user_id + market 分区。
- 快速切换使用请求取消和上下文代次校验；旧响应不得写入新市场或新账号状态。后台研究任务继续执行，切换页面不等于取消任务。
- 标准标识由证券主数据解析，例如 000001.SZ 与 000001.SH 分别表达股票与指数；内部同时记录资产类型、市场、交易所。保留旧六位 code 查询适配，禁止继续用前缀猜交易所作为最终依据。
- 私有结果缓存键含 user_id、market、规范参数哈希、data_version、factor_version、method_version、engine_version；公共数据缓存必须显式限制为可共享的授权数据。

### 3.2 新增研究任务接口

新增 `/api/stock/research/tasks` 系列，保留旧 `/api/stock/factors/analysis` 和 `/api/stock/backtest` 的兼容契约；新页面迁移完成前不删除旧接口。旧接口加安全边界，超出可同步处理范围时明确报错，不擅自把旧 200 结果体改成 202 任务体。

```typescript
type MarketType = "stock" | "futures"
type ResearchKind = "factor_analysis" | "portfolio_backtest"
type ResearchStatus = "pending" | "running" | "completed" | "failed" | "cancelled" | "expired"
type ResearchErrorCode = "INVALID_INPUT" | "DATA_MISSING" | "DATA_STALE" |
  "DATA_CONFLICT" | "PROVIDER_FAILED" | "INSUFFICIENT_SAMPLE" |
  "FORBIDDEN" | "BUDGET_EXCEEDED" | "TASK_TIMEOUT"
```

- `POST /api/stock/research/tasks`：认证、限额、严格 Schema，接收 kind、params、data_version；Idempotency-Key 必填。返回 202：task_id、request_id、status、status_url。
- `GET /api/stock/research/tasks?cursor=&limit=`：只返回本人任务，默认 20，最大 100。
- `GET /api/stock/research/tasks/{id}`：检查 owner，返回状态、步骤、尝试次数、开始/更新/结束时间、版本、错误和结果入口。
- `POST /api/stock/research/tasks/{id}/cancel`：检查 owner；pending 可直接 cancelled，running 写取消请求，由 worker 检查点确认。终态重复取消幂等返回，不覆盖 completed。
- `GET /api/stock/research/tasks/{id}/result`：仅已完成且有权限时返回结果；大体积明细分页，不再 trades[:500] 无提示截断。
- `GET /api/stock/research/tasks/{id}/trades?cursor=&limit=`：返回完整分页成交与未成交原因。
- 错误采用当前项目 HTTPException/detail 风格，并加入稳定错误码和 request_id；不返回 HTTP 200 + error 伪成功。
- 状态迁移：pending -> running/cancelled/expired；running -> completed/failed/cancelled/expired；终态不可随意覆盖。使用条件更新防止取消与完成竞态。
- worker 采用 PostgreSQL 原子领取、租约与有界子进程执行；启动/停止接入现有 scheduler 选主机制，首轮不强制新增独立部署服务，不在 API 线程执行长任务。领取租约和结果提交带执行代次校验，旧 worker 失去租约后不能发布结果；历史研究不受期货开闭市限制，超时能终止子进程。
- 初始保护默认值：每用户同时运行 1 个、等待 3 个；全局并发默认 1 可配置；300 只股票、5 年作为验收负载，运行硬超时初始 600 秒，性能目标仍为 300 秒内完成，按实测调整资源。

### 3.3 数据与金融口径

- 原始价用于成交、涨跌停与股数记账；复权因子和公司行动用于构建一致的研究收益与权益事件处理，禁止拿前复权价直接模拟股数及资金。
- 固定 Asia/Shanghai、交易所日历、244 交易日年化；公告仅有日期时保守设为下一交易日可用，已有精确公告时刻按实际可获得时刻处理。
- T 日收盘后形成信号，最早 T+1 开盘成交；同日收盘价形成完整因子后又按该价成交的路径关闭。
- 财务、行业和指数成分使用历史生效区间及可获得版本；缺少必要历史数据时拒绝“无偏差”结果，明确降低研究可用性，不能只写免责声明后继续标为有效。
- 缺少行情与停牌是不同状态：数据缺口报质量错误；停牌持仓继续保留及估值，不能把它免费卖掉或直接删除。
- 年化收益 `(末净值 / 初始净值)^(244 / 实际收益日数) - 1`；年化波动按真实每日收益样本标准差计算；最大回撤从包含初始净值 1 的权益序列计算；Calmar 为年化收益 / 最大回撤，不截断；零分母返回 null 和原因。
- 无风险利率参数为年化小数，默认显式为 0，日化 `(1 + rf_annual)^(1/244) - 1`；Sharpe 使用每日超额收益均值及其标准差。
- 胜率分别展示正收益日比例和已平仓交易胜率；换手展示买卖成交金额合计 / 调仓前权益与单边换手，名称、单位和首次建仓规则明确。
- ICIR 主值为 IC 均值 / 样本标准差；如展示年化 ICIR，标明采样频率和重叠收益影响，不把多日重叠标签无条件当独立日样本。
- 多空曲线是因子研究组合，不承诺 A 股可裸卖空；按非重叠持有或明确的组合分袖逐日记账，独立标注交易成本与融券假设。

## 4. 五个实施工作包

依赖顺序：包 1 -> 包 2 -> 包 3 -> 包 4 -> 包 5。包 3 内先交付包 5 定义的最小任务提交/状态/持久化能力，再连接因子检验；包 4 复用该能力，包 5 完成历史对比、恢复和发布验收。避免直到最后一包才把核心链路改成异步。未通过数据和正确性门槛的研究入口通过 Feature Flag 仅内部开放。

### 工作包 1 市场切换和现有内容隔离

**产出：** 切换股票/期货后恢复各自标的和页面，原期货功能不被股票状态影响。

修改文件：

- frontend/src/stores/app.ts、frontend/src/stores/auth.ts、frontend/src/stores/market.ts
- frontend/src/components/layout/top-navbar.tsx、frontend/src/components/layout/sidebar.tsx
- frontend/src/app/(main)/layout.tsx、frontend/src/app/(main)/market/page.tsx
- frontend/src/components/market/stock-market-view.tsx、frontend/src/lib/api.ts、frontend/src/hooks/sync.ts
- frontend/src/hooks/keyboard.ts、frontend/src/components/market/contract-search-dialog.tsx、frontend/src/components/ai/floating-assistant.tsx
- backend/app/services/kline_history.py、scripts/import-kline-history.py
- backend/app/api/users.py、backend/app/api/watchlist.py、backend/app/services/watchlist.py、backend/app/schemas/watchlist.py

新增文件：frontend/src/stores/market-context.ts、frontend/src/lib/market-navigation.ts、backend/tests/test_market_context_api.py、backend/tests/test_watchlist_market_isolation.py。

- [ ] 先沿 activeContract、localStorage、跨标签广播、自选 API 的所有调用方建立清单，再增加股票专属状态；期货 activeContract 兼容保留。
- [ ] 按市场过滤菜单；股票显示行情、因子研究及历史结果，期货保留原业务。直接访问市场专属页面时路由到对应市场有效入口。
- [ ] 顶栏切换串行化并显式展示失败；失败回滚不覆盖更新后的账号或偏好，支持快速连续切换；股票侧搜索、快捷键和浮窗遵循当前已开放能力。
- [ ] 股票查询不得接收 rb 合约；期货不得收到股票选择广播。共享系统通知保留，期货专属 UI 订阅按上下文挂卸载，不中断已运行服务任务。
- [ ] 自选增删改排均校验 user、market、trading_mode；缺省 market=futures 保持旧调用兼容，非法市场返回 422，不静默归一。
- [ ] 期货历史查询、最新日期、挖掘品种列表与导入范围统一限定 futures；共享表写入显式市场值。股票自选行情不走期货 Redis quote 键，响应提供市场和数据时间。保留 uq_code_date，防止破坏既有 ON CONFLICT 调用。
- [ ] 运行 API/权限测试、前端类型检查与构建；浏览器检查快速切换、失败回滚、刷新恢复、跨标签和退出后换账号。

**关键验收：** 期货选择一个有效合约 -> A 股选择 600519.SH -> 返回期货仍是原合约；两侧自选增删和研究输入互不改变；股票页不显示期货盘口/模拟下单，期货页不显示 A 股研究结果。

**回退：** 关闭新导航标志，恢复原期货入口；旧期货记忆键保留，不把股票值写回旧键。

### 工作包 2 股票历史数据可信化与版本迁移

**产出：** 研究可以选择并追溯不可变数据版本，避免当前成分股、修订财报及增量复权污染历史。

修改文件：backend/app/models/stock.py、backend/app/data/stock_history.py、backend/app/data/tushare_client.py、scripts/import-stock-data.py、backend/app/api/stock.py、backend/app/core/config.py、backend/app/main.py、backend/pyproject.toml、backend/uv.lock。

新增文件：

- backend/app/models/stock_dataset.py：数据集版本、历史成员、行业区间、公司行动与导入批次。
- backend/app/services/stock_data/validation.py、snapshots.py：标准化质量校验、Parquet 快照清单与校验和。
- backend/migrations/0001_dual_market_stock_research.sql、backend/scripts/apply_migrations.py：版本化迁移及执行记录，执行前与现有启动补列逻辑协调。
- backend/tests/test_stock_data_contract.py、test_stock_pit_data.py、test_stock_migration.py、test_stock_adjustment_consistency.py。

- [ ] 定义股票专属日线事实表，存 instrument_id、raw OHLC、volume 股、amount 元、adj_factor、可交易状态、来源及采集时间；不可变研究版本通过 Parquet 快照与小型清单保存，不在数据库重复复制每个版本的全量日线。旧 kline_history 的股票行保留 legacy 兼容读，不伪造已丢失的原始价。
- [ ] 对历史成员、退市日期、财报修订、行业生效区间和可获得时间建立独立记录；新增唯一键包含数据版本与标准标识。
- [ ] 首轮不改共享 kline_history 唯一约束；股票独立表使用完整证券标识。自选未来若扩展唯一键，需要同时处理 main.py 启动阶段重建 uq_user_mode_contract 的逻辑，不能只写一个会被下次启动覆盖的迁移。
- [ ] 供应商按能力声明 daily、valuation、financial、calendar、constituents、corporate_actions；首选用户已指定 DaoShare，配置和元数据使用真实供应商名称，保留兼容字段。用户资料中的卡密兑换步骤不意味着自动消耗兑换码；现有提供值已能查询，无须兑换。
- [ ] 完整处理分页/截断、单位、有限数、OHLC 合法性、重复冲突、缺失、数据过期和批次失败；权限/参数错误不重试，限流/网络错误有限退避。
- [ ] 新导入先写批次临时快照并验完整性，全部通过后原子发布版本；中断不推进成功水位，重跑不重复写入，不在启动阶段全量回填。
- [ ] 使用独立测试 PostgreSQL 验证旧数据升级、重复执行、批次中断与权限隔离；复权测试覆盖区间扩展和除权日，历史版本结果保持不变。

**关键验收：** 同一原始行情和版本不因后续导入改变；历史股票池包含当时退市及后续移出成员；无公告时间或修订历史缺失可见，不能生成未经标记的合格报告。

**迁移和回退：** expand -> 分批补齐股票事实数据 -> 对账 -> 切读；新增股票表和旧股票行保留，关闭新股票入口可回到原期货程序。首轮保留期货表约束，不给期货写入路径引入数据库兼容负担；后续清理旧股票行单独发布。

### 工作包 3 因子库 自定义表达式与有效性检验

**产出：** 九个内置因子与自定义公式都能在股票横截面数据上检验，并保存方法和数据版本。

修改文件：backend/app/services/stock_factors/catalog.py、engine.py、backend/app/api/stock_factors.py、frontend/src/lib/stock-api.ts、frontend/src/app/(main)/factor-research/page.tsx、frontend/src/components/factor-research/、docs/STOCK-FACTOR-RESEARCH.md。

新增文件：

- backend/app/schemas/stock_research.py：日期、枚举、窗口、因子及预处理结构契约。
- backend/app/models/stock_factor_definition.py：用户自定义公式和不可变版本。
- backend/app/services/stock_factors/expression.py、preprocessing.py、analysis.py：受限表达式、联合中性化与真实分组收益。
- backend/app/api/stock_factor_definitions.py：用户公式的创建、验证、版本查询与归档。
- backend/migrations/0002_stock_factor_definitions.sql。
- backend/tests/test_stock_factor_catalog.py、test_stock_factor_expression.py、test_stock_factor_preprocess.py、test_stock_factor_analysis.py、test_stock_factor_permissions.py。

- [ ] 为九个内置因子声明单位、方向、窗口、可用数据、方法版本；PE/PB 非正值不直接视为优质低估值，政策写入定义并返回样本覆盖率。
- [ ] 完整校验所选因子数据可用性，不静默跳过；按表达式最大依赖窗口加载起始日前预热数据。估值、量价及财务矩阵先统一股票列和交易日索引，防止缺交易日时对齐错误。
- [ ] 实现白名单公式：算术、abs、log、rank、zscore、delay、rolling_mean、rolling_std；仅允许非负滞后和有界历史窗口，限制表达式长度、深度、依赖数，拒绝属性访问、导入、执行代码和未来引用。
- [ ] 公式 `zscore(roe) - zscore(pb)` 从验证、保存到检验/回测使用同一版本；财务字段只来自包 2 的历史时点面板。不能仅把文本保存在页面。
- [ ] 去极值 MAD/3σ/关闭、缺失中位数/剔除、Z-Score 和行业/市值开关接入表单；全缺失截面不填零，预热不足不纳入有效选股池。
- [ ] 行业哑变量 + log 市值联合 OLS，固定秩亏和样本不足处理；测试残差与设计矩阵正交及股票列置换不改变结果。
- [ ] 输出 IC / RankIC / ICIR、1/5/10/20/40/60 日衰减、五/十分位收益、单调性统计和多空研究净值；未来标签不用于当日已实现收益，统计缺样本时返回 null 与原因。
- [ ] 在页面展示预处理、样本覆盖率和方法说明；未知因子、除零、超长公式和不可见用户公式均有明确错误。

**关键验收：** 九个因子各有手算样本；禁止未来引用；同一报表修订前后使用正确历史版本；至少两组因子形成可比较图表。与包 5 任务服务接通前只允许内部有界样本，不开放全量长任务。

**回退：** 自定义因子和新检验入口独立开关；原期货 factor_lab 内核与调用契约不变；旧 A 股检验结果标明 legacy 方法，不混为新版本结果。

### 工作包 4 股票组合构建与正确回测

**产出：** 真正逐日记账、逐期调仓，净值与成交明细能逐笔对账。

修改文件：backend/app/services/stock_factors/engine.py、backend/app/api/stock_factors.py、backend/app/schemas/stock_research.py、frontend/src/lib/stock-api.ts、frontend/src/app/(main)/factor-research/page.tsx、frontend/src/components/factor-research/。

新增文件：backend/app/services/stock_factors/portfolio.py、execution.py、metrics.py、backend/tests/test_stock_portfolio_weights.py、test_stock_execution.py、test_stock_backtest_accounting.py、test_stock_metrics.py、test_stock_backtest_bias.py。

- [ ] 等权、市值加权、因子分数加权统一实现；分数权重对负值、全相同分数和缺失设明确规则；持股上限、周/月调仓、股票池、起止日期、初始资金可配置。
- [ ] 信号日与成交日分离；现金、持股数量、当日可卖数量、未成交目标和每日权益显式记录，按漂移后实际持仓计算调仓量，稳定标识作为并列排序次级键。
- [ ] 实现 T+1、停牌、涨跌停保守撮合、成交量上限、整手买入、零股卖出、退市处置和公司行动；无数据不得模拟成交，未成交必须输出原因。
- [ ] 佣金、最低佣金、卖方印花税、过户费、滑点按市场和历史生效日期明确配置；先核实规则来源和适用证券范围，缺少依据不硬编码当前税率覆盖整个五年。
- [ ] 每日 NAV = 现金 + 持股市值；成本按实际成交当日扣除，不把期末收益摊平；期末保留持仓估值，若选择强制清仓必须通过同样成交约束。
- [ ] A 股 metrics 独立实现本文件第 3.3 节口径，不修改共享期货 metrics 造成历史结果变化。
- [ ] 对账覆盖完整清仓、停牌跨调仓、涨停买不进、跌停卖不出、现金不足、首日亏损、零交易与期间公司行动；结果输出基准曲线和超额收益，基准资产身份不能与同码股票混淆。

**数值验收样例：** 100 -> 120 -> 100，零成本全仓持有的末净值必须为 1，期间峰值 1.2、最大回撤 1/6；不得被整段平均收益变成一条无波动曲线。固定价格仅增加成本时，权益减少必须等于佣金、税费与滑点影响。

**关键验收：** 简单买入持有与独立手算逐日一致；策略与基准比较使用相同日期/价格/费用假设，误差阈值按现金与股数舍入设定并记录；不可把策略跑赢当成正确性标准。

**回退：** 新股票回测方法使用独立 engine_version；失败关闭新入口，保留新旧结果版本，不回退到已知失真的实现并标为正确。

### 工作包 5 异步任务 历史结果与全链路验收

**产出：** 用户可以提交、查看进度、取消、重启后查回、复跑及对比完整研究结果，并形成题目 2 交付材料。

修改文件：backend/app/main.py、backend/app/services/scheduler.py、backend/app/core/config.py、backend/app/api/stock_factors.py、frontend/src/lib/stock-api.ts、frontend/src/app/api/stock/[...path]/route.ts、frontend/src/app/(main)/factor-research/page.tsx、backend/pyproject.toml、backend/uv.lock、docs/SELF-HOSTING.md、docs/STOCK-FACTOR-RESEARCH.md。仅当实测需要独立 worker 部署时才调整 docker-compose.yml。

新增文件：

- backend/app/models/stock_research_task.py、backend/app/services/stock_research/tasks.py、worker.py、results.py。
- backend/app/api/stock_research.py、backend/migrations/0003_stock_research_tasks.sql。
- frontend/src/app/(main)/factor-research/history/page.tsx、frontend/src/components/factor-research/research-task-status.tsx。
- backend/tests/test_stock_research_tasks.py、test_stock_research_api.py、test_stock_research_integration.py、test_stock_research_reproducibility.py。
- scripts/benchmark-stock-research.py、docs/STOCK-FACTOR-DEFINITIONS.md、docs/STOCK-RESEARCH-ACCEPTANCE.md。

- [ ] 持久任务记录 owner、request_id、幂等键、规范参数、所有方法与数据版本、步骤、尝试次数、租约、超时、取消及结果位置；相同键不同输入返回 409。
- [ ] worker 有界领取并恢复过期租约；计算提交结果和完成状态具备一致性策略。文件先写临时对象再原子替换，DB 提交结果元数据，清理未引用文件；重试不重复发布结果或扣额度。
- [ ] 新前端通过短请求创建任务、轮询进度、分页取明细；切换市场不丢失研究，不显示旧请求结果；代理补齐新增方法与请求追踪透传，不依赖 300 秒长连接。
- [ ] 完整保存输入参数、公式版本、数据快照校验和、代码/引擎版本、交易与净值；运行环境依赖版本纳入复现清单，不以“没有随机数”替代复现证据。
- [ ] 用户 A 不能读取/取消/导出用户 B 任务；多标签重复提交、worker 重启、DB/Redis 失效、取消与完成竞态、文件写入失败均有链路测试。
- [ ] request_id 贯穿 API / worker / 数据加载 / 计算 / 持久化；记录耗时、排队、失败类型、数据源失败和缓存命中。日志不得含 token 或用户明细面板。
- [ ] 300 只、5 年、月调仓真实授权数据进行冷/热执行基准，记录硬件、输入版本、耗时、峰值内存和队列等待，目标计算完成小于 5 分钟；未达标优化本链路后复测，不声称已达标。
- [ ] 输出九因子定义、两组因子对比报告、有效性图表、偏差处理说明、数据字典、部署/迁移/回退说明与人工验收记录。

**关键验收：** 同输入 + 同数据/因子/方法/引擎版本两次运行，净值、交易和指标一致；任务 ID、request_id 和运行时间等非经济字段不要求相等。完成全部人工验收后再打开正式入口。

**回退：** 停止新任务领取、处理运行中任务并关闭股票研究 Flag；保留任务和结果可读及旧期货应用兼容。不可删表冒充回滚。

## 5. 题目 2 覆盖矩阵

| 原文要求 | 工作包 | 可检查的交付 |
|---|---|---|
| 至少八因子、四类以上 | 3 | 六类九因子定义、版本、手算与抽样核对 |
| 自定义表达式 | 3、5 | 校验/保存/编辑版本/试算/检验/回测接通 |
| MAD 或 3σ、Z-Score、填缺、行业及市值中性化 | 3 | 表单可配置、联合回归与边界测试 |
| IC、IR、衰减、分组收益、多空净值 | 3 | 真实统计和图表、样本与假设可见 |
| 等权、市值、因子加权 | 4 | 三种组合权重与资金/股数对账 |
| 持股数量、周/月、手续费、滑点 | 4、5 | 输入参数、成本与完整调仓记录留存 |
| 净值和八项绩效指标 | 4 | 真实逐日权益、固定口径独立测试 |
| 前视、幸存者、停牌 | 2、4 | 历史成员与可获得时间、持仓及不可成交测试 |
| Web 参数、指标、净值、分组、调仓明细 | 1、3、4、5 | 市场分流、加载/空/错/权限/完成状态、分页 |
| PostgreSQL、Parquet 中间结果 | 2、5 | 版本迁移、快照清单、结果引用 |
| 300 股五年月频五分钟 | 5 | 授权真实数据、冷/热基准原始记录 |
| 参数和结果持久化与复现 | 2、5 | 历史任务、不可变版本、两次经济结果一致 |
| 至少两组因子对比和部署说明 | 3、5 | 报告、图表、手册与回退步骤 |

题目所列 ECharts：现有 K 线保留 lightweight-charts；研究图表如需满足题目技术项则增加锁定版本 ECharts，复用一个图表封装。加入前检查现有图表组件、许可证与依赖安全，避免迁移全部现有图表。

## 6. 验证命令与人工路径

以下是**后续实施计划命令，本轮未执行**。新增测试文件在相应包内落地后才运行。后端工作目录 backend，前端工作目录 frontend。

```powershell
uv run pytest tests/test_market_context_api.py tests/test_watchlist_market_isolation.py tests/test_watchlist_master_follow.py -q
uv run pytest tests/test_stock_data_contract.py tests/test_stock_pit_data.py tests/test_stock_migration.py tests/test_stock_adjustment_consistency.py -q
uv run pytest tests/test_stock_factor_catalog.py tests/test_stock_factor_expression.py tests/test_stock_factor_preprocess.py tests/test_stock_factor_analysis.py tests/test_stock_factor_permissions.py -q
uv run pytest tests/test_stock_portfolio_weights.py tests/test_stock_execution.py tests/test_stock_backtest_accounting.py tests/test_stock_metrics.py tests/test_stock_backtest_bias.py -q
uv run pytest tests/test_stock_research_tasks.py tests/test_stock_research_api.py tests/test_stock_research_integration.py tests/test_stock_research_reproducibility.py -q
uv run pytest tests/test_backtest_account.py tests/test_backtest_runner.py tests/test_factor_lab.py -q
pnpm exec tsc --noEmit
pnpm build
```

数据库集成测试必须连接专用测试库并强制拒绝生产库，核心一致性/迁移测试不能用内存 mock 代替。当前前端 package.json 没有 test/lint 脚本，不编造 `pnpm test` 已可运行；包 1 先完成浏览器验收，必要的自动化工具选型与锁定跟随实际验证需求，不单独扩建基础设施。

人工验收顺序：

1. 账号 A 登录，期货选择有效合约、增加自选并记录行情设置。
2. 切 A 股，确认菜单和数据日期，选 600519.SH，增加股票自选并配置研究参数。
3. 快速往返切换及刷新，确认两侧选中标的、参数、自选及结果分别恢复；浏览器网络中无错误市场标的请求。
4. 验证九因子及自定义公式，选择五/十分位、切预处理，检查覆盖率、IC、衰减和分组图。
5. 提交周/月调仓及三种权重回测，检查每日净值、费用、未成交和完整清仓记录，分页无截断。
6. 比较两组因子；同版本复跑确认一致；关页、切市场和重启 worker 后仍可查回结果。
7. 账号 B 验证不能读取账号 A 的私有自选、公式、任务、结果及导出文件。
8. 注入缺数据、停牌、涨跌停、无权限、重复请求、取消、超时和数据源失败，核对明确错误与 request_id。

## 7. 发布 兼容与成本

- 初始 Flag 建议分别为 STOCK_RESEARCH_ENABLED、STOCK_RESEARCH_V2_ENABLED，默认不开启未验证的新研究链路；市场切换先完成验证后单独开放。
- 部署顺序：备份 -> 独立迁移和历史数据版本构建 -> 兼容后端 -> worker -> 前端 -> 内部冒烟 -> 数据和数值验收 -> 正式启用。
- 保留 futures 旧接口默认值；新股票路由不得重解释期货 trading_mode。历史股票结果标为旧方法，不覆盖或无声重算。
- 研究为确定性计算，新增链路模型调用费用为零；AI 辅助功能不作为题目 2 的必需依赖。
- 增量成本主要为合法数据权限、历史数据和 Parquet 快照磁盘、计算峰值内存、备份与 worker CPU；先实测再设资源，不能给出未经计量的价格承诺。
- 快照按数据版本共享仅限有权使用者，私有报告按用户隔离；设置保留期与引用计数后才能清理，不无限保存所有大面板。
- 数据授权、历史成员/修订数据、生产数据库规模及部署资源仍需在实施中核实；缺关键历史数据时，其他包可继续用明确标记的测试夹具验证，不伪造正式真实数据验收。

## 8. 下一轮与后续扩展

下一轮聚焦工作包 1，按四个子交付推进：

1. 市场上下文、独立标的和账号级持久化，兼容期货旧记忆。
2. 菜单/路由分流、切换失败与异步响应防串写。
3. 自选读写/排序/分组权限及市场边界闭环。
4. 前端构建、定向接口回归与切换人工验收记录。

题目 2 扩展方向在五包验收后独立排期：因子正交化和可配置合成、机器学习选股及样本外验证、历史行业轮动、参数敏感性分析。它们不能替代必需功能，也不得默认加入首轮扩大实现范围。

用户新增的两市场功能对齐按四包推进：A 股工作台/行情/自选统一体验；A 股 AI 问答与看盘工具；股票模拟账户/订单/持仓；A 股 AI 交易和通知。先复用布局、表单、消息组件和模型渠道，逐项替换证券主数据、交易日历、可交易状态、现金/股数、T+1 及权限工具；不能照搬期货保证金、多空开平与合约乘数到股票。

## 9. 本轮执行记录

- 完成状态：项目关键链路审阅和计划包已编写；产品功能未实施，生产就绪未验收。
- 实际修改：仅新增本文件；没有修改业务代码、密钥配置或数据库。
- 实际命令类别：Get-ChildItem / rg --files / rg -n / Get-Content；PowerShell ZipFile + XML 提取任务书题目 2；git status --short；Get-Command uv,pnpm,node；仓库 Python 环境运行一段内存回测核对。
- 成功：题目 2 功能与验收条目读取；现有代码与计划映射；内存数值核对进程退出 0，并发现回测偏差。
- 失败/限制：git status 无仓库元数据；探索中若干猜测文件名不存在，已改为目录检索定位真实文件；未发现主项目现成的股票专属测试文件。
- 未执行：pytest 全套/定向测试、tsc、构建、迁移、真实数据导入、网络数据源授权核验、浏览器联调、性能压测和部署。
- 本轮金融变化、兼容影响、迁移影响和模型调用成本：均无运行时变更；金融修正和迁移只在上述工作包中规划。
