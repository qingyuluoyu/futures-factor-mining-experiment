# A 股 K 线与模拟盘对齐实施计划

执行状态：以下四包已执行并验证；用户确认的日线回放另按 `2026-10-17-stock-replay.md` 接通。原清单保留，真实结果及未通过项以 `docs/STOCK-REPLAY-VERIFICATION-2026-10-17.md` 为准，整个产品不是全功能验收通过。

> 使用 executing-plans 在当前会话逐包执行，先失败测试再实现；不提交、不切分支。当前目录无 Git 元数据，修改前保留源码副本。旧的五包研究平台计划继续有效，本计划仅覆盖本轮用户优先调整。

**目标：** 保留期货既有功能，A 股使用同一产品布局并借鉴 tick 的成熟 K 线工作区；交易只做模拟，不接真实资金账户。

**架构：** 保持 Next.js / FastAPI / PostgreSQL / Redis。复用现有 lightweight-charts v5、指标计算和主题系统，迁入 tick 的近端蜡烛视窗、独立量能区、悬停信息条、指标开关及日周月交互，不搬入另一套后端或图表依赖。股票标的状态与期货彻底分开，股票页面不得展示或操作期货账户数据。

## 边界

- 不增加经纪商、银行卡、真实账户登录或实盘下单能力；原期货模拟业务保持兼容。
- 当前 A 股数据是本地前复权日线，不伪装为实时盘口；日线回放与实时模拟的撮合选择已向用户询问，回答前先完成安全的数据与图表部分。
- 复用界面和交互，不照抄期货保证金、双向开仓、手数或结算规则。
- 不修改 tick 参考项目；不新增依赖。图表包不改数据库，后续已确认的回放包新增独立版本迁移，通过链路验证后才开放。
- 已发现的研究回测五项失败不属于图表改造，不降低断言或标记为通过。

## 工作包与验收

- [ ] **1. 市场上下文隔离。** 新建 `frontend/src/stores/stock-market.ts` 和 Node 回归测试；股票选择按用户 / 登录盘隔离保存，拒绝期货代码，期货 store 拒绝股票代码。修正行情深链、搜索、广播和切换路由，避免股票标的写入期货状态。执行 Node 测试并在浏览器反复切换、刷新核验。
- [ ] **2. tick 风格 K 线。** 新建 `frontend/src/components/market/stock-chart-data.ts` 及测试，先验证 OHLC 聚合、排序、去重、非法数据和初始视窗，再修改 `stock-kline-chart.tsx`。复用指标计算；主图 / 成交量 / MACD 分区，近 60 / 120 / 250 根或全部，日 / 周 / 月切换，十字线 OHLC 信息，复位、放大和自适应高度；不默认压缩全部历史。
- [ ] **3. 行情垂直链路及模拟边界。** 修改 `stock-market-view.tsx`、`stock-universe.tsx`、布局和顶栏：清除切股旧数据，明确错误 / 重试 / 空数据，估值和行情日期分开，复权与非实时标识清楚。股票未接通的期货专属页面不渲染期货账户，给出可用入口，不伪装成功；模拟撮合按用户确认另行接通。
- [ ] **4. 后端隔离与回归交付。** 先补 SQL 条件测试，再给期货历史三处查询加 `market_type=futures`；不动唯一约束和旧写入路径。执行受影响 pytest、前端 Node 测试、类型检查、隔离生产构建、真实接口与浏览器验证，保存截图并更新交付记录。

## 验证命令与回退

在 frontend 运行 `node --test src/stores/stock-market.test.mjs src/components/market/stock-chart-data.test.mjs`、`pnpm exec tsc --noEmit`；在 backend 运行 `.venv/Scripts/python.exe -m pytest tests/test_market_history_isolation.py tests/test_factor_lab.py tests/test_watchlist_master_follow.py -q`。使用忽略目录内的前端副本执行 `node node_modules/next/dist/bin/next build`，避免影响正在运行的开发服务。

通过当前 QA 账户检查股票选择、K 线工具栏、日周月、量能 / MACD、浅深主题及市场切换；原用户不下单、不清理原数据。新增费用仅本机计算，不启用付费 AI 循环。

回退时只恢复本轮源码备份，新增文件单独移出；新股票浏览器键不影响旧期货键。无数据库结构变更，不需整库恢复。若模拟盘继续扩展，必须单独补迁移、成交规则、幂等和权限测试后才能开放。
