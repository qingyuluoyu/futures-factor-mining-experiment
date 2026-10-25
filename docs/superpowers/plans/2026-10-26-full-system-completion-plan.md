# 计划包：全功能完善剩余工作（2026-10-26）

> 用户指令（2026-10-26）：「你执行吧，没有做好的地方你开子agent去执行，做好计划包先，接下来我不会回答你，一直做直到全功能完善再说」。
> 本文只做计划与事实登记，不代表包已实施。执行时逐包先红后绿、一次自审，不自动建分支。

## 0. 本轮起点事实（均已验证）

### 0.1 CI 工作流已上线并跑通（原「第二项」）

`.github/workflows/frontend-build.yml`（160 行，9 步骤）已提交推送：`67d7581`（工作流）→ `f0aec17`（修复）。

首次运行 **失败**，已按系统化调试定位根因并修复；第二次运行 **9/9 步骤成功，68 秒**：

| 步骤 | 结果 | 耗时 |
|---|---|---|
| Checkout / Setup pnpm / Setup Node.js | success | 4s |
| Install dependencies（`--frozen-lockfile`） | success | 3s |
| Typecheck（`tsc --noEmit`） | success | 9s |
| Build（standalone） | success | 42s |
| Assemble runnable bundle | success | 2s |
| Upload artifact / Summary | success | 4s |

artifact：`frontend-standalone-f0aec17…`，**21,222,355 字节**，保留 14 天。

**首跑根因**（与本地内存墙/EPERM 无关，是工作流自身引入的新问题，必须记录）：
`frontend/pnpm-workspace.yaml` 只有 `allowBuilds`、缺 `packages` 字段。pnpm 一在该目录发现此文件就把 `frontend/` 当 workspace 根，`packages` 必填，缺失即 `ERROR packages field missing or empty` 并退出 1。

此前未暴露是因为两条消费路径都绕开了该文件：本地用 pnpm 12.3.4 对此宽容；`frontend/Dockerfile` 的 deps 阶段只 `COPY package.json pnpm-lock.yaml`，从不复制它。CI 全量 checkout 才踩到。

**不能删除该文件**：pnpm 12 已明确 WARN 不再读 `package.json` 的 `pnpm.onlyBuiltDependencies`（"no longer read by pnpm … keys were ignored"），构建审批须由本文件承载。故只补 `packages: ["."]`，`allowBuilds` 原样保留。补后本地 `pnpm install --frozen-lockfile --dry-run` 仍报 "lockfile is up to date; a real install would make no changes"，与 lockfile 仅有的单个 importer `"."` 一致，锁文件未变。

字节备份与精确 patch：`logs/ci-fix-pnpm-workspace-20261025/`
`before ac32da4674879516506d9c173bab34d7009615abb546713d01901e967e4d6bf5`
`after  96e8eea92f5dcb35917f35e085a8adbeca5e8c7a063a040a654ad25812add5b2`

### 0.2 剩余四包（承接执行18末）

| 包 | 内容 | 状态 | 阻塞 |
|---|---|---|---|
| ②-A | 工作台/行情/筛选职责收敛 | 未开始 | 无 |
| ②-B | 跨页未确认写恢复 | 未开始 | 共享侧栏冻结，路由拦截须独立兼容包 |
| ⑤ | 数据集资格升级（`research_only`/`backtest_ready`） | 未开始 | 离线部分无阻塞；真实 PG apply 属①授权范围 |
| ④-1 | 迁移020 + 016 模型/schema 约束放宽 | 未开始 | 无 |
| ④-2 | 挂载两个未挂载 router + 016 worker + 开关改 Settings 旋钮 | 未开始 | `app/main.py`、`core/config.py` 属共享接触点，需兼容设计+期货回归+回退 |
| ④-3 | 按 kind 派发 + 新子进程 + `finish_experiment`/`finish_report` | 未开始 | 依赖 ④-1、④-2 |
| ④-4 | 六个能力的正式 API 路由（默认 OFF） | 未开始 | 依赖 ④-3、⑤ |
| ① | 获准恢复原服务后的 PG/HTTP/本人与期货发布回归 | 受阻 | 需用户明确授权重启服务 |
| ③ | tick候选来源/授权/修订/单位对账和显式预算下补采 | 受阻 | 数据/模型调用默认 0 预算，需显式授权 |

## 1. 本轮白名单登记（AGENTS.md：股票独立白名单逐轮登记）

### 1.1 已登记并已实施

| 文件 | 改动 | 理由 |
|---|---|---|
| `.github/workflows/frontend-build.yml` | 新增 160 行 | CI 构建 standalone，根治本地内存墙与 Windows symlink EPERM |
| `frontend/pnpm-workspace.yaml` | 补 `packages: ["."]` + 5 行注释 | CI 安装失败根因；字节备份与 patch 见 `logs/ci-fix-pnpm-workspace-20261025/` |

### 1.2 冻结（SHA 锚定，不得修改）

以下共享/期货文件已由 `logs/stock-pages-18-accepted-inventory.json`（1182）与 `...-accepted-supplemental-sha.json`（34）锚定：

| 文件 | 说明 |
|---|---|
| `frontend/src/components/layout/sidebar.tsx` | SHA `167c3c3fb5b74fc8e12009d55d0a29f93223973fd84eaa1724bb162e12045360`；双市场共用（`app/(main)/layout.tsx:130`），且按 `stockMarket` 与 `supportsStockRoute` 分支 |
| `frontend/src/components/layout/top-navbar.tsx`、`financial-session-sync.tsx`、`stock-feature-boundary.tsx` | 共享认证/网关/布局 |
| `frontend/src/app/(main)/layout.tsx` | 共享布局 |
| `frontend/src/components/factor-research/use-research-task.ts` | `logs/stock-pages-18-compatibility.json` 记录 `shared_job_recovery_and_idempotency_unchanged: true`；计划P4明确「不改持久幂等协议」 |
| `frontend/src/app/(main)/factor-lab/page.tsx`、`backtest/page.tsx` | 字节验证：仅可增删单个股票 `view` 属性 |
| `frontend/src/components/dashboard/dashboard-frame.tsx` 及其 5 个 slot 组件 | 期货专用，且被 `landing/auth/login-dashboard-background.tsx:5,110` 二次消费 |
| `frontend/src/components/screener/screener-workspace.tsx`、`condition-card.tsx`、`lib/screener-api.ts` | 期货专用（import `useAppStore.setActiveContract`、`getIndicatorSettingsApi`） |
| `frontend/src/components/market/kline-workspace.tsx`、`market/kline/*`、`stores/indicator*`、`stores/market-pane-indicator` | 期货专用 |
| `frontend/src/stores/auth.ts`、`hooks/auth.ts`、`lib/http-session.ts`、`tsconfig.json` | SHA `05636e5b2998cb7ee216821014240e016a2fb8f4ceca4ef9c9a80b8ed2b42250` |
| `frontend/package.json`、`frontend/pnpm-lock.yaml` | 依赖与锁文件 |
| `frontend/src/lib/market-routes.ts` | 共享；`STOCK_ROUTES` 已含 `/dashboard`、`/market`、`/market/screener`，改动会同时影响期货侧栏「未接通」标记 |

共享路由内的**期货分支**同样冻结，只可改股票分支 JSX：`app/(main)/dashboard/page.tsx`、`market/page.tsx`、`market/screener/page.tsx`、`trading/page.tsx`、`positions/page.tsx`、`orders/page.tsx`、`assets/page.tsx`。

### 1.3 可变（股票独占，已有测试或需补测试）

`components/dashboard/stock-dashboard.tsx`、`components/market/stock-market-view.tsx`、`stock-screener-workspace.tsx`、`stock-board-browser.tsx`、`stock-quote-detail.tsx`、`stock-screen-results.tsx`、`stock-data-availability.tsx`、`stock-eod-status.tsx`、`components/trading/stock-replay-workspace.tsx`、`stock-replay-state.ts`、`stock-replay-order-history.tsx`、`components/factor-research/stock-factor-editor.tsx`、`factor-research-workspace.tsx`、`stock-factor-library.tsx`、`stock-research-entry.tsx`、`stock-research-history.tsx`、`research-restore-panel.tsx`、`stock-dataset-jobs.tsx`、`research-report-bookmark.tsx`。

## 2. 验证命令（从 `frontend/` 执行；基线 778 通过 / 0 失败 / ~42s）

```powershell
$testFiles = @(rg --files src -g '*.test.mjs')
node --experimental-strip-types --import ../logs/node-alias-register.mjs --test --test-concurrency=1 @testFiles
node node_modules/typescript/bin/tsc --noEmit --incremental false
```

注意：alias 注册器在**仓库根** `logs/node-alias-register.mjs`（不是 `frontend/logs/`），所以参数是 `../logs/...`，且要求 `process.cwd()` 为 `frontend/`。新测试文件与源码同目录同名 `*.test.mjs`，共 109 个。

后端（执行18 口径）：

```powershell
backend/.venv/Scripts/python.exe -X utf8 -m pytest tests/test_stock_kline_reference.py tests/test_stock_market_capability.py tests/test_stock_market_api.py tests/test_stock_eod.py tests/test_stock_eod_integration.py tests/test_stock_research_api.py tests/test_stock_research_legacy_adapter.py -q
```

期货回归门禁：

```powershell
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b33-preservation.py verify logs/futures-preservation-b33-start.json
```

## 3. 包②-A：工作台 / 行情 / 行情筛选 职责收敛

### 3.1 问题（全部位于股票独占文件，收敛本身不需要改任何共享文件）

三个路由都是双市场，各有一条冻结期货分支：`dashboard/page.tsx:17`、`market/page.tsx:183-184`、`market/screener/page.tsx:13`。三个股票工作区各自重复实现了同一批职责：

| # | 重复职责 | 工作台 | 行情 | 行情筛选 |
|---|---|---|---|---|
| A-1 | 独立 `getStockBoardsApi("")` 拉取 + 自有 `revision` 刷新态 → 同一目录 3 次网络请求 | `stock-dashboard.tsx:16-31` | `stock-board-browser.tsx:69-84` | `stock-screener-workspace.tsx:15-28` |
| A-2 | `<StockEodStatus>` → 每页 3 次独立 GET | `stock-dashboard.tsx:42`（常驻展开） | `stock-board-browser.tsx:102`（`<details>`） | `stock-screener-workspace.tsx:40`（`<details>`） |
| A-3 | 覆盖摘要卡（证券目录/行情覆盖/行情交易日/来源/复权口径） | `stock-dashboard.tsx:46-51` + `52-64` | `stock-board-browser.tsx:88` | `stock-screener-workspace.tsx:43`（单行） |
| A-4 | 「盘后行情来源与缺口」审计 `<details>`，**两处字节级近似副本**，第三处缺失 | `stock-dashboard.tsx:52-64` | `stock-board-browser.tsx:89-101` | 无 |
| A-5 | 行业筛选驱动 `StockMembers`，三种 UI 同一切态 | `stock-dashboard.tsx:20,72,78`（传 `compact`） | `stock-board-browser.tsx:70-71,104` | `stock-screener-workspace.tsx:14,44-50,55` |
| A-6 | 股票列表 `StockMembers` 三页共用，但查询态各自重置 | — | — | — |
| A-7 | `StockQuoteDetail` 三种「返回」语义，且行情是 URL 真相（`stock-market-view.tsx:12-13`）、另两页是组件态真相 | `stock-dashboard.tsx:79` | `stock-market-view.tsx:18` | `stock-screener-workspace.tsx:56` |
| A-8 | 股票用户的 `market/page.tsx` 上约 80 行仅服务期货的布局死状态 | `LAYOUT_PREF_KEY:27`、`splitMode/fullscreen/rightCollapsed/rightTab:102-105`、`persistLayout:117-128` 等，只被期货分支消费 | | |
| A-9 | 股票「工作台」实为第四个行情浏览页，而股票账户摘要在 `/trading` 等回放账本页（`stock-replay-workspace.tsx:301-304`） | | | |

另：`stock-market-view.tsx` **完全没有测试文件**（109 个测试中无一提及 `StockMarketView`）。

### 3.2 实施范围

1. 新增 `components/market/stock-market-overview.tsx` + 同名 `.test.mjs`：由它独占持有 A-1…A-4（一次 boards 拉取 + 一个 `revision`、一个 EOD 状态、覆盖卡、审计 `<details>`），以 props 向下提供 `{ industry, onIndustryChange, refreshKey, collapsible?, children? }`，让三页保留各自的行业 UI（看板按钮 / 看板网格 / `<select>`）而不再各自拉取。
2. 新增 `components/market/stock-market-view.test.mjs`：补上缺失覆盖；断言 URL 真相选股（`params.get("symbol")`）、`router.push("/market")` 返回导航、且只发起一次 boards 读与一次 EOD 读。
3. 改 `stock-market-view.tsx`（20 行）：行情 = 看板 + 成员 + 详情，A-1…A-4 委托给新组件。
4. 改 `stock-board-browser.tsx`：改为接收预取结果，删除自有 effect（`:77-84`）；`StockMembers` 不动。
5. 改 `stock-screener-workspace.tsx`：行情筛选保留筛选表单 + 结果 + 详情 + `<select>` 行业筛选 + `StockDataAvailability`，A-1…A-3 委托。
6. 改 `stock-dashboard.tsx`：**此处需要产品决策，不许猜**——要么 (a) 股票工作台改为账户中心（复用 `stock-replay-workspace.tsx:301-304` 的摘要形态），要么 (b) 继续作为行情总览但必须复用新共享组件、不再自带审计/覆盖块副本。②-A 按最小去重取 (b)，(a) 单独立项。
7. 扩展 `stock-dashboard.test.mjs`、`stock-screener-workspace.test.mjs`：断言「每页恰好一次 `getStockBoardsApi` 与一次 `getStockEodStatusApi`」（两处 harness 已捕获 `calls`，如 `stock-dashboard.test.mjs:30`、`stock-screener-workspace.test.mjs:32`）。
8. 明确不改：`market/page.tsx` 期货分支、`dashboard/page.tsx` 期货分支、`market/screener/page.tsx` 期货分支、`dashboard-frame.tsx` 及 5 slot、`screener/*`、`kline-workspace.tsx`、`market/kline/*`。A-8 的那约 80 行对期货用户是活代码，保持原样。

## 4. 包②-B：跨页未确认写恢复

### 4.1 问题

**今天完全没有任何导航守卫、没有任何跨页持久化、没有任何跨页提示。** 全仓 grep `beforeunload|popstate|window.addEventListener` 只找到 keydown/resize/pointerup/storage，没有导航/卸载守卫；Next App Router 客户端导航会立即卸载页面段。

各股票页的「未确认写入」态及失败模式：

| 位置 | 未确认态 | 失败模式 |
|---|---|---|
| `stock-replay-workspace.tsx` | `busy:66`、`retry:67`、`mutationInFlight:83`；`runMutation:174-222`，不确定路径 `setRetry(command):216`；卸载清理 `:132-141` 不持久化 `retry` | **重复委托（真金融风险）**：`prepareReplayMutation(null, …)` 在 `:236/245/250/255` 每次挂载都新铸 `crypto.randomUUID()`（`stock-replay-state.ts:20`），服务端幂等去重无法生效 |
| `use-research-task.ts` | `storageKey = stock-research:${scope}:36`，`persist({draft}):227` 在 `create(draft):228` **之前**；挂载 `initialize():187-196` 有恢复提示 | slot 是持久的但 scope 与路由无关（`stock-research-api.ts:65-71`），只有研究页读它；导航去 `/trading` 等页后 slot 成孤儿，**无任何可见提示** |
| `stock-factor-editor.tsx` | `pending` 为 `useRef` **仅内存**（`:29`）；挂载 effect `:66` 无条件置 null，清理 `:69` abort；`save():97` 新铸 UUID | **静默丢失 + 重复定义**：中途点侧栏 → 卸载 → 丢弃 `pending`，而服务端可能已落库；返回时换新键，服务端无法去重。且 `:167` 文案明说「切换会话会清理本地草稿」，把丢失变成静默 |
| `research-report-bookmark.tsx` | `unknown:11` 仅内存 | 组件随选中 job 销毁即丢 |
| `stock-dataset-jobs.tsx` | `perform():20-35` 卸载即 abort，**完全没有带原键重试路径** | 只在 `:75/:77` 打印键，UI 无处保存或重填 |

执行18 落地的是**页内**恢复（`research-restore-panel.tsx` + `stock-research-history.tsx`，P4）与**页内**导航禁用（`stock-replay-workspace.tsx:283-285`，P3）；**跨页**情况确未解决，不得描述为已解决。

### 4.2 实施范围

1. 新增 `lib/stock-pending-writes.ts` + 测试：`localStorage` 注册表，键 `${scope}`，值 `{ kind, idempotency_key, expected_revision?, signature, created_at }`；提供 `readPendingWrites` / `recordPendingWrite` / `clearPendingWrite`。存储形态与 `localStorage` try/catch 照抄已获接受的 `use-research-task.ts:43-47 persist()`，但作为**独立注册表**，研究 hook 的持久幂等协议保持字节不变。
2. 改 `stock-replay-workspace.tsx`：`runMutation`（`:176`）前把 `retry` 命令写入注册表，成功（`:193`）清除；挂载（`:132-141`）从注册表回填 `retry`，使既有「重试同一请求」（`:289`）能以**原**幂等键重发。这一步才真正堵住 `:236/245/250/255` 的重复委托洞。
3. 改 `stock-factor-editor.tsx`：同样持久化 `pending.current`；把 `:66` 的无条件置 null 换成回填读取，使 `:165` 的重试按钮可跨导航工作；修订 `:167` 文案（现文案告诉用户草稿已清理，与目标相反）。
4. 改 `research-report-bookmark.tsx`（`:11/:62`）与 `stock-dataset-jobs.tsx`（`:75`）同样登记；数据集任务补一个真实的「重试原请求」动作，而不是只打印键。
5. **不碰共享侧栏来展示提示**：`sidebar.tsx` 已 SHA 锚定且双市场共用，`app/(main)/layout.tsx` 也是共享的，所以 `beforeunload`/路由拦截守卫**不属于②**，须另立独立兼容包（含共享兼容设计 + 期货回归 + 可执行回退）。②-B 改为从已挂载在这些路由上的股票独占组件渲染横幅：`stock-research-entry.tsx`（覆盖 `/factor-research`、`/factor-lab`、`/backtest`）、`stock-replay-workspace.tsx`（覆盖 `/trading`、`/positions`、`/orders`、`/assets`），另可在 `stock-research-history.tsx`（股票独占、已是全局任务视图、已在 `:173` 链到 `/factor-research?restore=…`）加「未确认写入」区。页内先例见 `stock-replay-workspace.tsx:283-285`（`locked` 时导航 `<Link>` 变 `aria-disabled` `<span>`），照此形态扩展到注册表。

## 5. 包④：原016任务兼容演进与正式链路

### 5.1 已查实的约束（`backend/migrations/20261023_016_stock_research_v2_jobs.sql`，53 行）

016 只有两张表。**`stock_research_v2_jobs`**（L1–43）关键约束，原文：

```
L26:  result_dataset_id UUID REFERENCES stock_research_datasets(id) ON DELETE RESTRICT,
L29:  CONSTRAINT uq_stock_v2_job_key UNIQUE(owner_id,trading_mode,idempotency_key),
L30:  CONSTRAINT ck_stock_v2_job_mode CHECK (trading_mode IN ('virtual','live')),
L31:  CONSTRAINT ck_stock_v2_job_kind CHECK (kind = 'dataset'),
L32:  CONSTRAINT ck_stock_v2_job_status CHECK (status IN ('pending','running','completed','failed','cancelled','expired')),
L33:  CONSTRAINT ck_stock_v2_job_attempts CHECK (attempts BETWEEN 0 AND 1),
L34:  CONSTRAINT ck_stock_v2_job_runtime CHECK (max_runtime_seconds BETWEEN 30 AND 300),
L35:  CONSTRAINT ck_stock_v2_job_queue CHECK (max_queue_seconds BETWEEN 30 AND 900),
L37:  CONSTRAINT ck_stock_v2_job_result CHECK ((status = 'completed' AND result_dataset_id IS NOT NULL AND result_data_version IS NOT NULL AND result_qualification IS NOT NULL AND length(result_data_version) = 64 AND result_qualification = 'observation_only') OR (status <> 'completed' AND result_dataset_id IS NULL AND ...)),
L51（workers 表）: CONSTRAINT ck_stock_v2_worker_name CHECK (name = 'stock-v2-dataset-job-v1')
```

`L31` 是等值判断而非 `IN` 列表，所以 `dataset` 是唯一允许的 kind；`research_ic`/`backtest`/`benchmark`/`ml_train`/`report` 全部被拒。另有两处结构性阻断：

1. **结果形态被冻死在观察数据集**：L26 外键 + L37 强制每个 completed 任务必须指向 `stock_research_datasets` 行、`result_qualification='observation_only'`、64 字符 `result_data_version`。没有任何列能承载 ML 实验 id、基准报告 sha 或比较产物。
2. **`max_runtime_seconds BETWEEN 30 AND 300` 低于 ML 训练预算**：`TrainingParameters.max_seconds` 是 `ge=1, le=600`（`backend/app/schemas/stock_ml.py:35`）。

015 进一步叠加：`backend/migrations/20261023_015_stock_research_datasets.sql:42` `CONSTRAINT ck_stock_dataset_qualification CHECK (qualification = 'observation_only')`，所以 `DatasetRef.qualification` 虽然名义上有 `observation_only | research_only | backtest_ready`（`schemas/stock_research_v2.py:75`），库里永远只能是 `observation_only`；`services/stock_research_v2/portfolio.py:94-95` 因此对任何非 QA 可成交回测都追加阻断 `dataset_not_backtest_ready`。

模型侧完全镜像：`app/models/stock_research_v2_job.py:21`（kind）、`:27`（result）、`:65`（worker name）。SQL 文件里**没有** `stock_research_v2_job_schema_versions` 表，它由 runner `backend/scripts/migrate_stock_research_v2_jobs.py:90` 创建。

### 5.2 派发链现状：只有 `dataset` 通，且 worker 未挂载

```
POST /research/v2/datasets/jobs        app/api/stock_dataset_jobs.py:88-92
  → JobService.submit                  jobs.py:139-166（L160-162 硬编码 kind；L150 要求 available() → 活 worker 租约）
worker 循环（显式单claim）：
  run_dataset_once                     dataset_worker.py:21-45
    → claim_next                       jobs.py:203-240
        L212  WHERE Job.kind == "dataset" AND Job.workflow_version == WORKFLOW_VERSION   ← kind 门
        L217  重建 CreateDatasetJob(...)，非 dataset 参数 → ValidationError → L229-232 标 failed
    → execute_claim / run_claim_process dataset_worker.py:84-145 / :48-81（spawn_bounded_process，Windows Job，512MiB）
        → build_claim_dataset          claim_execution.py:12-45（:32 archive.build_dataset，只会建观察数据集）
    → finish_dataset                   jobs.py:290-309（:300 要求 qualification == 'observation_only'）
```

应用真正启动的 worker 是 **002 那条**：`app/main.py:975-981` 起 `StockResearchWorker`，循环 `claim_next_job`（`services/stock_research_jobs.py:205-229`），**无 kind 过滤**，kind 在子进程里判（`stock_research_process.py:28` `if kind not in ("analysis","backtest"): raise`），表约束 `ck_stock_research_job_kind CHECK (kind = 'analysis' OR kind = 'backtest')`。

即：**两套互不相通的 job 系统**。002 走 legacy `stock_factors.engine`；016 走新 `stock_research_v2` kernel 但只做 dataset。自定义 IC / 可成交回测 / 基准 / ML / 报告**没有任何东西派发**。

**016 worker 未挂载**：`dataset_worker.py:1` docstring 明写 "Explicit, one-claim orchestration. Not mounted in application lifecycle."，`run_dataset_once`/`execute_claim` 只被 `tests/test_stock_columnar_execution_pipeline.py` 导入；`app/services/` 下无 v2 worker，`app/main.py` 无对应 `app.state` 项。

### 5.3 能力矩阵（六项 kernel 均已实现，缺口全在接线）

| 能力 | kernel | API 路由 | 接 016 | 开关默认 |
|---|---|---|---|---|
| (i) 固定数据 | `archive.py`/`columnar.py`/`columnar_store.py` | **部分**：`api/stock_research_v2.py:79-93` 目录路由，但**该 router 未在 main.py 挂载**；`api/stock_dataset_jobs.py:69-97` 也**未挂载** | 是（016 唯一端到端） | OFF，且 `stock_v2_jobs_enabled` 在 `app/` 内**从未被赋值** |
| (ii) 自定义IC | `expressions.py`/`factor_values.py`/`analysis.py`/`custom_factors.py` | **仅 CRUD+validate**：`api/stock_custom_factors.py:79-120`，**无 IC 路由** | 否 | OFF，`custom_factors_ready(session)` 迁移校验和门 |
| (iii) 可成交回测 | `portfolio.py`/`execution.py`/`costs.py`/`corporate_actions.py`/`metrics.py` | **无** | 否 | **结构性阻断**（015 qualification） |
| (iv) 基准 | `benchmark.py`/`comparison.py` | **无**；`comparison.py` 全仓只被 `tests/test_stock_comparison_v2.py` 导入 | 否 | 无入口故无门 |
| (v) ML/LightGBM | `ml_dataset/ml_split/ml_train/ml_predict/ml_evaluate/ml_portfolio/research_experiment/model_registry` | **只读**：`api/stock_ml_registry.py:85-111`；**无训练/提交路由**；`schemas/stock_ml_registry_api.py:20` `training_enabled: Literal[False] = False` | 否 | OFF 且硬编码，`stock_ml_registry_enabled` 同样在 `app/` 内从未赋值 |
| (vi) 报告 | `research_experiment.py:60-64` 写不可变报告；`model_registry.py:152-174` 读 | **部分**：019 报告读路由存在（flag 门）；legacy 002 报告路由 `api/stock_research_reports.py`（`main.py:1093`，`stock_research_jobs_enabled` 默认 False） | 否 | OFF |

另有两项 016/015 router **完全没挂载**：`api/stock_dataset_jobs.py` 与 `api/stock_research_v2.py`，grep 确认只被各自测试导入，故 `/api/stock/research/v2/jobs*` 与 `/datasets*` 在真实应用中返回 404。相比之下 `stock_custom_factors` 与 `stock_ml_registry` 经 `api/stock_factors.py:41-42` 挂了但被 flag 关着。

### 5.4 开关与可用性：当前无生产配置路径

| 开关 | 读取处 | 默认 | `app/` 内有赋值？ |
|---|---|---|---|
| `stock_v2_jobs_enabled` | `api/stock_dataset_jobs.py:72,90` | False（getattr 默认） | **否**，仅测试 |
| `stock_ml_registry_enabled` | `api/stock_ml_registry.py:54,64` | False | **否**，仅测试 |
| `stock_research_jobs_enabled` | `api/stock_research_jobs.py:35` | False | 有，`core/config.py:90` |
| `stock_legacy_sync_enabled` | `api/stock_factors.py:66` | True | `core/config.py:91` |
| `custom_factors_ready(session)` | `api/stock_custom_factors.py:81` | 017 迁移校验和门 | `custom_factors.py` |
| `require_stock_market` | 每个股票研究 router 的 `dependencies=` | 非股票会话 409 `stock_required` | `core/market_capability.py:9-12` |

注意 016 与 019 的门是**裸 `app.state` 属性，无 Settings 字段、main.py 无赋值**——即目前没有任何生产配置路径能打开它。`stock_research_artifact_store`（`api/stock_research_v2.py:54`、`api/stock_ml_registry.py:56`）同样从未在 main.py 赋值。

### 5.5 迁移策略：下一个空闲版本是 `020`

总包 §2.6 原文：「后续正式业务任务/最终测试消费迁移须独立兼容设计后从未占版本分配，不覆盖019；执行前再次确认目录无新冲突，若冲突重新分配独立版本，禁止覆盖既有014或其他迁移。」`backend/migrations/` 现有 17 个文件，最高为 `20261025_019_stock_ml_registry.sql`，**020 未被占用**；014 已被重复占用（既有问题），故 §2.6 的「执行前再次确认」适用。

020 必须遵循的机制（照 016/019 runner）：
- `backend/scripts/migrate_stock_research_v2_jobs.py:77-105`：check 用 `SET TRANSACTION READ ONLY`，`SET LOCAL lock_timeout='5s'`，`SET LOCAL statement_timeout='30s'`，`pg_advisory_xact_lock(20261023016)`，按 `;` 切分，`validate_schema(connection)`，再写 `_schema_versions(version, checksum)`。020 用自己的锁 id（如 `20261025020`）与自己的版本行。
- **`validate_schema` 按约束名 + 规范化文本比对反射得到的 CHECK 与 SQLAlchemy 模型**（`scripts/migrate_stock_research_v2_jobs.py:42-44`）。所以放宽 SQL **必须同批改动** `app/models/stock_research_v2_job.py`，否则 apply 报 `v2 job check mismatch`。
- **`jobs_ready()`**（`jobs.py:36-38`）读 `V2JobSchemaVersion` 比对 SQL 文件 SHA。把 `SCHEMA_VERSION` 指向 020 会让仅部署 016 的环境报 `v2_job_schema_unavailable`（503）——这是预期的 fail-closed，但灰度期就绪检查应同时接受 016 或 020。

### 5.6 ④的最小实施方案

**迁移**：新增 `backend/migrations/20261025_020_stock_research_v2_task_chain.sql` + runner `backend/scripts/migrate_stock_research_v2_task_chain.py`。纯增量，绝不改 016/019 SQL。自有 advisory lock `20261025020`、自有版本行、登记校验和，缺失即拒绝服务。

**要放宽的 SQL 约束**（全部在 016 的两张表内）：
1. `ck_stock_v2_job_kind`（L31）：`kind = 'dataset'` → `kind IN ('dataset','research_ic','backtest','benchmark','ml_train','report')`，用同名 `DROP CONSTRAINT` + `ADD CONSTRAINT`。
2. `result_dataset_id` 外键（L26）与 `ck_stock_v2_job_result`（L37）：**不删**，改为加可空列并放宽结果 CHECK 接受第二种形态：
   - `result_experiment_id UUID REFERENCES stock_ml_experiments(id) ON DELETE RESTRICT`（019 已存在，不得重建）
   - `result_report_sha256 VARCHAR(64)`、`result_artifact_id UUID REFERENCES stock_research_artifacts(id) ON DELETE RESTRICT`
   - 新 CHECK：观察数据集形态 **或** 实验形态 **或** 报告产物形态；非 completed 行三者保持 NULL。
3. `ck_stock_v2_job_runtime`（L34）：上界提到 `30 AND 900`，使 `max_seconds ≤ 600` 放得下；或在新 schema 里把 `max_seconds` 压到 300。优先提 job 上界，`windows_job_process.py` 的 512MiB/4 进程预算不动。
4. `ck_stock_v2_worker_name`（L51）：放宽为 `name IN ('stock-v2-dataset-job-v1','stock-v2-research-task-v1')`。
5. `ck_stock_v2_job_mode`/`_status`/`_attempts`/`_queue`/`_identity`/`_running`/`_finished`/`uq_stock_v2_job_key` 全部不动。

**模型与 schema 必须与 SQL 逐字一致**（否则 `validate_schema` 失败）：`app/models/stock_research_v2_job.py` 同步上述四处 + 新可空列；`app/schemas/stock_research_v2_jobs.py` 增加 `CreateResearchICJob`/`CreateBacktestJob`/`CreateBenchmarkJob`/`CreateMLTrainJob`/`CreateReportJob`，把 `WORKFLOW_VERSION`/`METHOD_VERSION` 从 L9-L10/L26/L32-L33 的单个 `Literal` 泛化，`DatasetClaim.parameters`（L35）改为可辨识联合；`app/schemas/stock_dataset_jobs_api.py` 同步放宽 `DatasetJobSummary.kind`（L21）、`method_version`（L32）、`workflow_version`（L33），保留 L41 的 `result.qualification` 断言。

**要扩展的派发**：`jobs.py:212` 把 `Job.kind == "dataset"` 过滤换成新 kind 集合并按 kind 分流（替代 L217 的无条件重建 `CreateDatasetJob`）；新增 `finish_experiment`/`finish_report` 作为 `finish_dataset`（L290-309）的兄弟，写 `result_experiment_id`/`result_report_sha256`，各自按 L300 同规格复验 scope/fencing；`jobs.py:279` 放宽 `fail_claim` 码集合；`dataset_worker.py:54,59` 增加新子模块名走同一 `spawn_bounded_process`；`claim_execution.py` 按 kind 分支，只有 `dataset` 调 `build_claim_dataset`，新 kind 调 `run_experiment`/`run_ml_portfolio`/`compare_to_index` 并经 `ModelRegistryService.register_experiment`（`model_registry.py:176`）登记；**在 `app/main.py` 生命周期内挂载 worker**（挨着 002 worker 的 L975-981），仅当新开关打开时启用显式 `run_dataset_once` 循环；**挂载那两个未挂载的 router**（`api/stock_dataset_jobs.py`、`api/stock_research_v2.py`），各带 `Depends(require_stock_market)`。

**要新增的 API 路由**（统一 `/api/stock/research/v2`，全部 `require_stock_market`）：
`POST /research/v2/datasets/jobs`（已写好，只需挂载 + 放宽 kind）、`POST /research/v2/custom-factors/{id}/ic` → `research_ic`、`POST /research/v2/backtests` → `backtest`、`POST /research/v2/benchmarks` → `benchmark`、`POST /research/v2/ml/train` → `ml_train`、`POST /research/v2/reports` → `report`。模型登记发生在 `ml_train` 任务内部（`ModelRegistryService.register_experiment`），019 的只读 API 不需要新增写路由。

**开关全部默认 OFF**：在 `app/core/config.py`（L90 旁）加 `stock_research_v2_task_chain_enabled: bool = False`，并在生命周期镜像进 `app.state`，使门成为真正的 Settings 旋钮而非当前未赋值的裸属性。开关关闭时 `GET .../jobs/availability` 必须返回 `accepting: false` 并给原因，list/cancel 继续可用（`stock_dataset_jobs.py:69-75` 现有行为，须保留）。

**幂等与归属不回归**：`uq_stock_v2_job_key UNIQUE(owner_id, trading_mode, idempotency_key)`（016 L29）+ UUID `Idempotency-Key` 头；`Scope` 只从 `AuthContext` 派生（`stock_dataset_jobs.py:51`）；`request_hash` 绑定参数 + `method_version` + `workflow_version`（`jobs.py:143`）；`MAX_SCOPE_ACTIVE = 2`/`MAX_ACTIVE = 16`（`jobs.py:21-22`）作为并发上限不变；模型不自确认、不直写库、不发交易。

### 5.7 明确不属于 ④ 范围

- **资格升级到 `research_only`/`backtest_ready`** 是 015 的改动（`ck_stock_dataset_qualification` L42）加一个基于证据的资格迁移；不升级则 `portfolio.py:94-95` 继续阻断可成交回测、`ml_train.py:49` 继续阻断非 QA ML。这是**独立包**（见 §9 包⑤），不是 016 放宽能解决的。
- **019 不得触碰**：`stock_ml_experiments` 与 `stock_ml_registry_schema_versions` 原样保留，020 只**引用** `stock_ml_experiments(id)`。
- **无期货代码改动、无共享认证/网关/布局/运行配置改动**。唯一共享接触点是 `app/main.py`（挂载 router + 生命周期 flag）与 `app/core/config.py`（一个默认关闭的 Settings 字段），两者都需 AGENTS.md 要求的单独兼容设计 + 期货回归 + 可执行回退。

### 5.8 ④的相关测试

016/dataset/columnar：`test_stock_research_v2_jobs.py`(250L)、`test_stock_research_v2_job_migration.py`、`test_stock_dataset_jobs_api.py`(160L)、`test_stock_dataset_catalog_api.py`(148L)、`test_stock_dataset_claim_execution.py`、`test_stock_dataset_execution_pipeline.py`、`test_stock_columnar_execution_pipeline.py`、`test_stock_dataset_process_supervisor.py`、`test_stock_columnar_process_supervisor.py`、`test_stock_dataset_windows_resources.py`、`test_stock_dataset_qualification.py`

ML：`test_stock_ml_train.py`、`test_stock_ml_predict.py`、`test_stock_ml_split.py`、`test_stock_ml_dataset.py`、`test_stock_ml_evaluation.py`、`test_stock_ml_portfolio.py`、`test_stock_ml_bounded_process.py`、`test_stock_ml_registry.py`、`test_stock_ml_registry_api.py`、`test_stock_ml_registry_migration.py`、`test_stock_ml_registry_mount.py`、`test_stock_research_experiment.py`、`test_stock_archived_study.py`、`test_stock_study_inputs.py`

kernel：`test_stock_analysis_v2.py`、`test_stock_benchmark_v2.py`、`test_stock_comparison_v2.py`、`test_stock_execution_v2.py`、`test_stock_costs_v2.py`、`test_stock_metrics_v2.py`、`test_stock_corporate_actions_v2.py`、`test_stock_factor_values_v2.py`、`test_stock_factor_expressions.py`、`test_stock_point_in_time_v2.py`、`test_stock_universe_v2.py`、`test_stock_research_panels_v2.py`、`test_stock_research_archive_v2.py`、`test_stock_custom_factors_api.py`、`test_stock_custom_factors_migration.py`

注意 `test_stock_archived_study.py` 与 `test_stock_comparison_v2.py` 是全仓 `run_archived_study`/`compare_strategies`/`save_comparison` 的**唯一**调用方，即这两项能力生产调用方为零。

执行16 的 14 文件 ML/016 集合见 `logs/stock-course-execution-16-backend-test-files.log`。

## 6. 受阻包与需要用户授权的一件事

### 包①（需授权重启服务）

AGENTS.md：不自动重启服务、不因自动化提示反复拉起用户随后关闭的进程。因此本包在获得明确授权前只能就绪，不能执行。就绪后按序：起五原端口（3001 前端 / 8002 后端 / 8110 connector / 5433 PG / 6380 Redis）→ 期货回归门禁 `check-stock-b33-preservation.py verify` → 真实 PG 迁移 check/apply → 原 HTTP 契约 → 本人视觉路径 → 部署新构建 → 回退演练。证据基线 `logs/stock-pages-18-runtime.json`、`logs/run-20261025-redis-recovered.json`。

### 包③（需显式数据预算授权）

AGENTS.md：数据/模型调用默认 0 预算；已有 API Key 不代表允许任意补采。本包涉及 tick 候选 263 根的真实来源/采集/响应 hash 与手转股单位对账，未获显式预算前不发任何采集请求。原 PG 中 605058 日线仅 5 根，不能以选源目录冒充补齐真实数据。

### 需要用户授权的一件事（合并为一次问清）

1. 是否授权重启五原端口服务以执行包①？
2. 是否授权包③的显式补采预算（额度、起止、证券范围）？

两项在授权前，②-A、②-B、④ 均不受阻塞，可连续推进。

## 7. 执行顺序

```
已完成：CI 工作流 + 首跑根因修复（9/9 步骤绿，artifact 21.2MB）
   ↓
②-A 职责收敛（无阻塞，股票独占文件）
   ↓
②-B 跨页未确认写恢复（无阻塞；侧栏路由拦截另立包）
   ↓
⑤ 数据集资格升级（④的前置阻断；离线部分可做，真实 PG apply 属①授权范围）
   ↓
④-1 迁移020 + 模型/schema 放宽（④的地基，纯增量）
   ↓
④-2 挂载两个未挂载 router + worker + 开关改为 Settings 旋钮
   ↓
④-3 按 kind 派发 + 新子进程 + finish_experiment/finish_report
   ↓
④-4 六个能力的正式 API 路由（默认 OFF）
   ↓
① / ③ —— 受阻，等用户授权
```

④ 的四个子包有严格依赖，必须顺序执行；⑤ 可与 ②-B 之后、④-1 之前的任何时点插入，但其迁移版本号需与 ④-1 协调（执行前重新确认 `backend/migrations/` 目录）。

每包固定协议（AGENTS.md §4，不得省略）：定向读直接调用链 → 失败反例（红） → 仅实现本切片（绿） → 一次自审（金融公式/时间/缺值单位/用户盘市场/幂等竞态/取消后后台/状态机/日志脱敏/费用） → 目标测试 + TSC + 迁移静态检查 → 记录未执行项。停机阶段只能记录第二层未执行，不得勾选整包完成。

## 8. 状态口径

单包状态只用：未开始 / 实施中 / 已实现待验证 / 受阻 / 已完成并验证。源码、迁移、上下游、关键异常/权限、真实测试、人工路径未闭合不得报完成。本轮已完成项仅「CI 工作流 + 首跑根因修复」一项，且其「已完成并验证」的依据是 GitHub Actions 9/9 步骤 success 与 artifact 实际上传。

## 9. 包⑤：数据集资格升级（`research_only` / `backtest_ready`）

这是 ④ 的前置阻断，必须独立成包，不能借 016 放宽夹带。

**现状**：`backend/migrations/20261023_015_stock_research_datasets.sql:42` `CONSTRAINT ck_stock_dataset_qualification CHECK (qualification = 'observation_only')`，使库里资格永远只有一档。连带后果：
- `services/stock_research_v2/portfolio.py:94-95` 对任何非 QA 可成交回测追加 `dataset_not_backtest_ready`
- `services/stock_research_v2/ml_train.py:49`、`archived_study.py:28-29`、`research_experiment.py:26-27` 除非 `qa_only=True` 否则抛 `dataset_not_qualified`

**总包 §2.2 的口径**（不得降低）：`DatasetRef` 三档为摘要，不是唯一授权条件；D3/D4 另返回 `capabilities[analysis|portfolio|ml|rotation]` 及每项 `required_fields, coverage, missing_reasons, allowed_scope, limitations`，按实际 FactorRef/中性化/权重/执行规则解析依赖。已选特征缺数必须失败或由用户显式新建另一配置，不自动删掉特征继续。当前分类/不明复权仅 `research_only`；真实回测关键价量/交易日历/历史池/执行规则缺失仍阻止相应认证，**不把「分级」当降级绕过**。

**总包 §2.2 的另一条硬线**：「只有规则证据完整才升级，不能用管理员按钮直接认证」。所以资格升级必须由**可复现的规则证据**驱动，不接受人工开关。

**实施要点**：
1. 新迁移（版本号按 §5.5 规则另取，020 若被④占用则顺延；执行前重新确认 `backend/migrations/` 无冲突），放宽 `ck_stock_dataset_qualification` 为 `IN ('observation_only','research_only','backtest_ready')`，纯增量、不整表历史回填。
2. 同步 `app/models/stock_research_dataset.py` 与 `app/schemas/stock_research_v2.py:75`，保持一致，否则 `validate_schema` 失败。
3. 新增/补齐资格判定服务：按 D3/D4 的真实覆盖（关键价量、交易日历、历史股票池、执行规则、公司行动/退市经济结果、复权口径明确性）计算可达最高资格与 `missing_reasons`，输出 `capabilities[]`。判定必须可复现、记录依据版本。
4. `portfolio.py` / `ml_train.py` / `research_experiment.py` / `archived_study.py` 的阻断改为读新资格与 capabilities，保留 QA 路径不变。
5. 缺证据时只降级并明示原因，不静默放行；不得为通过测试移除上限或伪造资格。

**验证**：资格判定单测（已知合格/已知缺项/缺交易日历/不明复权/退市经济结果缺失各一）、`portfolio.py` 阻断路径回归、`ml_train.py` 的 `qa_only` 与非 QA 两侧、迁移 check/apply 静态检查与模型一致性、期货回归门禁。

**未授权不得执行**：涉及真实 PG 迁移 apply、原 HTTP、本人页面验收，属包①授权范围。
