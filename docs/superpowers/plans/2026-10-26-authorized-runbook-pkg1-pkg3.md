# 受阻包 ① / ③ 执行 Runbook（待用户授权后一次跑完）

> 状态：**受阻**。本文只做准备，未执行任何一步。
> 授权来源：AGENTS.md「不自动重启服务、不因自动化提示反复拉起用户随后关闭的进程」与「数据/模型调用默认0预算；已有API Key不代表允许任意补采」。
> 计划包见 `docs/superpowers/plans/2026-10-26-full-system-completion-plan.md` §6。

## 0. 授权前状态（2026-10-25T21:34:42 实测，`logs/stock-pages-18-runtime.json`）

| 端口 | 服务 | 监听 |
|---|---|---|
| 3001 | 前端（裸机；Docker 映射 3000） | 否 |
| 8002 | 后端 | 否 |
| 8110 | sina-bridge | 否 |
| 5433 | PostgreSQL（本地独立实例，`POSTGRES_PORT=5433`） | 否 |
| 6380 | Redis | 否 |

`migrations_applied: false`、`paid_model_calls: 0`、`financial_data_calls: 0`。

端口来源：`.env`（5433/6380）、`docker-compose.yml:15`（6380）、`:35`（8002）、`:67`（3000）、`:139`（8110）。

## 1. 包①：恢复原服务 → PG/HTTP/本人与期货发布回归

### 1.1 前置检查（不启动任何东西）

```powershell
# 端口是否真的全停（避免重复启动用户已关闭的进程）
Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
  Where-Object { $_.LocalPort -in 3001,8002,8110,5433,6380 } |
  Select-Object LocalPort,OwningProcess

# 期货保护基线是否仍在
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b33-preservation.py verify logs/futures-preservation-b33-start.json
```

`scripts/check-stock-b33-preservation.py` 是 `check-stock-b32-preservation.py` 的薄封装，且把 `guard.ALLOWED` 设为**空 frozenset**——即**不允许任何文件与基线不同**。本轮若已有代码改动（②-A/②-B/④-*），该门禁会报差异；此时应改用 `logs/stock-pages-18-accepted-inventory.json`（1182）+ `...-accepted-supplemental-sha.json`（34）核对，并逐项解释声明差异，**不得为消警而重置基线**。

### 1.2 启动顺序（PG → Redis → 后端 → connector/sina-bridge → 前端）

```powershell
# 1) PostgreSQL（本地实例，端口 5433）
# 2) Redis（端口 6380）
# 3) 后端（8002）——注意 AI 保持 paused/user，不恢复付费 AI
# 4) sina-bridge / loopback connector（8110）
# 5) 前端（3001）——用当前源码构建产物，不用 02:03 的 .next-stock-ux-round3
```

启动后立刻验活，五项都要 200/就绪才算「已恢复」：

| 检查 | 期望 |
|---|---|
| `GET http://127.0.0.1:8002/health`（或项目既有健康端点） | 200 |
| connector runtime / login | 200 |
| Redis `PING` / `BGSAVE` | PONG / OK |
| PG 连接 + 迁移 check（不 apply） | 无缺失版本 |
| 前端 `http://127.0.0.1:3001` | 200，且 BUILD_ID 是本次构建 |

**Redis 持久化已知坑**：曾出现 MISCONF 与 WSL I/O 故障阻塞（见 `docs/STOCK-RUN-2026-10-25.md` 顶部 13:27 更新）。若复现，先核实是否 WSL 发行版状态问题，不反复重启。

### 1.3 期货发布回归（先于任何股票验收）

```powershell
backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b33-preservation.py verify logs/futures-preservation-b33-start.json
```
外加原期货分发与旧接口逐条核对：9 因子/预处理/IC/组合/报告链路、原 `/api/stock/*` 默认行为、统一任务幂等 hook、11 个回放业务函数。

### 1.4 真实 PG 迁移 check / apply

**只 apply 已创建的 015–019**（`backend/migrations/20261023_015…` 至 `20261025_019_stock_ml_registry.sql`）。**不应用任何本轮新增迁移**，除非该包自己已按总包 §2.6 完成独立兼容设计。

```powershell
# check（只读）
backend/.venv/Scripts/python.exe -X utf8 scripts/migrate_stock_research_v2_jobs.py check
# apply（仅授权后）
backend/.venv/Scripts/python.exe -X utf8 scripts/migrate_stock_research_v2_jobs.py apply
```

各迁移自带 advisory lock、`lock_timeout=5s`、`statement_timeout=30s`。**缺版本表/列/锁文件/产物损坏必须拒绝服务，不能启动时自动建表或重采。**

### 1.5 原 HTTP 契约回归

按 `docs/STOCK-PAGES-EXECUTION-18-2026-10-25.md` 的后端命令实跑，并补真实 ASGI 权限/异常路径：

```powershell
backend/.venv/Scripts/python.exe -X utf8 -m pytest tests/test_stock_kline_reference.py tests/test_stock_market_capability.py tests/test_stock_market_api.py tests/test_stock_eod.py tests/test_stock_eod_integration.py tests/test_stock_research_api.py tests/test_stock_research_legacy_adapter.py -q
```
基线：**76 通过、36 条既有 utcnow 警告、10.16 秒**。

### 1.6 本人（人工）验收路径——执行18 原文，逐条过

> 明确获准运行后部署新构建，依次验：有两源证券默认最新日期→切历史qfq→回raw，日期/口径清楚且无拼接；605058仍仅真实5根；关闭EOD不能越界读取；实验室/研究/回测内容各自独立；跨kind最近任务不串草稿，历史回测恢复进入回测；交易有图/下单/推进，其余三页只有账本，切账户不残留旧私有内容；核原期货分发和旧接口。异常/身份/晚响应按子包路径验证。

### 1.7 发布与回退

- 部署当前源码构建（CI 产物 `frontend-standalone-<sha>` 或本地 `WENYUAN_PREVIEW_BUILD=1 pnpm build`），**不用** 02:03 的 `.next-stock-ux-round3`。
- 回退先核目标 SHA，按各独占 patch 撤增量而非整文件恢复；保留用户后续编辑/旧任务/账户/结果/数据；两个共享路由仅撤股票 `view` 属性；**不 DROP 数据**；保留 17 及旧成功构建。
- 新源码未发布前，不得伪称已验证过运行回退。

## 2. 包③：tick 候选来源 / 授权 / 修订 / 单位对账与显式预算下补采

### 2.1 已知事实（不得篡改）

- 原 PG 中 **605058 日线仅 5 根**。
- 本地找到 **tick 263 根**，但「row 来源/采集 hash 不足及混源写表风险不能直接认证或拼入」。
- 日线可能 **Sina / Teajoin 同表 upsert** 且丢失 row 来源、采集、响应 hash。
- **不能以选源目录当作补齐真实数据。**

### 2.2 授权前必须问清（不向聊天索要明文密钥）

1. 补采的**显式预算**：请求次数 / 费用上限 / 时间窗。
2. **证券范围**：仅 605058，还是候选全量。
3. **起止日期**与所需字段。
4. 供应商**授权用途**与配额/单价。
5. 脱敏样例（非明文密钥）。

### 2.3 授权后的执行步骤

1. **来源对账**：按 `backend/app/services/stock_eod_reconciliation.py`、`stock_history_audit.py`、`stock_raw_panel.py` 统计 605058 的日线/tick 真实行数、首末日期、来源分布、缺失率。
2. **修订与单位**：核对「手转股必须精确可验证，不截断小数」（总包 §2.1）；源原值保留 Decimal 字符串；`report_period`/`announced_at`/`available_at`/`collected_at` 分开，采集时间不等于历史可得时间。
3. **hash 补齐**：为既有 tick 行补 row 来源 / 采集 hash / 响应 hash，或标记为不可认证并隔离，**不混拼导入**。
4. **补采**：仅在显式预算内发起；每笔记录来源、时间、费用。
5. **资格重算**：补采后按包⑤的资格规则重算可达资格，缺证据只降级并明示原因。

### 2.4 硬线

- 未获显式预算，**不发任何采集请求**，`financial_data_calls` 保持 0。
- 不得填假价格、量、因子 1 或历史可得时间。
- 不得为通过测试移除上限或伪造资格。

## 3. 需要用户授权的事项（合并为一次问清）

1. **是否授权重启五原端口服务**（3001/8002/8110/5433/6380）以执行包①？
2. **是否授权包③的显式补采预算**？若是，额度、起止、证券范围各是多少？

在两项授权前，②-A、②-B、⑤、④-1~4 均不受阻塞，可持续推进。

## 4. 证据落点

授权执行时，证据写入 `logs/authorized-run-<日期>/`：端口探测、启动日志、健康检查、期货门禁、迁移 check/apply、HTTP 契约、本人逐条结果、回退演练、费用与调用计数。未执行的项逐条列出，不得勾选完成。
