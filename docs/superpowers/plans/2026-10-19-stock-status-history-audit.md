# 盘后同步状态与旧历史只读审计 Implementation Plan

> 使用executing-plans / test-driven-development / verification-before-completion；按dispatching-parallel-agents隔离文件所有权。无Git目录，不建分支/提交。保留上一轮所有修改。

**Goal:** 让用户看得见真实的盘后采集运行状态，同时量化旧历史风险，为安全迁移提供证据，不把凌晨旧快照当今日收盘。

**Architecture:** 保持FastAPI/PG/Next和16:30后的原worker。状态端点仅投影已有任务/快照元数据、不调上游、不改任务；两处现有A股页面共享只读状态组件。旧历史审计仅本地5433的READ ONLY事务，查询有超时/数量上限，不重导、不重写。旧表成交量/复权锚点迁移单独设计，不改期货字段。

**Tech Stack:** 原Python/httpx/SQLAlchemy/pytest与React/TypeScript/Zod/Node测试；无新依赖、无新DDL。

## 约束与时间

- 今天2026-10-19凌晨，未到收盘，不下载当日daily、不预生成今日价格、不把同源抽查当交易所认证。
- 本轮0上游/0模型预算；有阻断先完成可安全部分，不盲目补导。
- 已创建本对话单次05:15续做，ID a-05-15；原02:05/07:10保持，不重复创建。续做先读最新交付，不覆盖其他执行者；只在实质进展/失败/完成/需决定时通知。

## 包1：同步状态垂直切片

文件：新增backend/app/services/stock_eod_status.py、backend/tests/test_stock_eod_status.py；修改backend/app/api/stock_market.py。

入口GET /api/stock/market/sync-status，需认证，Cache-Control:no-store、X-Request-ID，5秒DB超时。现有stock代理已透传追踪头。仅2个有LIMIT1的任务/快照投影查询，不读完整quotes、不改变任务状态，不请求供应商。DB异常503及脱敏日志，不伪装空成功。

响应v1：

```
status_version: "stock-eod-status-v1"
server_time: 带时区ISO时间
timezone: "Asia/Shanghai"
collection_window: "16:30"
schedule_interval_seconds: 300
enabled, sync_enabled, worker_running: boolean
state: disabled | unavailable | manual_only | stopped | before_window | pending |
       running | closed | completed | partially_completed | failed | expired
message: 服务端固定中文说明
today_job: null | {id, trade_date, status, step, attempts, started_at, deadline_at,
                  finished_at, error_code}
latest_snapshot: null | {trade_date, data_version, collected_at, provider,
                         price_adjustment, coverage:{expected,quoted,missing,unknown},
                         raw_response_evidence:boolean}
verification: "stored_metadata_only_not_independent_certification"
```

当前任务只查询北京时间今天；running过期只显示expired，不擅自写失败。正常状态区分未到窗口、待调度、运行、休市、完整/部分完成、失败及服务停用。没有交易日历证据不推断“最新交易日”或“数据完全新鲜”。latest_snapshot是已存元数据，不伪称整快照hash/独立认证。

- [x] 状态/投影/权限/异常路径先红后绿。
- [x] 共享前端组件与Schema、加载/失败/重试/取消；工作台和行情接通，刷新状态不触发抓取。不能以新组件破坏原板块或图表。
- [x] 测试每种状态、未收盘/缺快照/旧证据、0模型/上游、跨股期货不改账。

前端所有权：新增frontend/src/components/market/stock-eod-status.tsx及测试、frontend/src/lib/stock-eod-status-api.ts及测试；只为接入修改stock-board-browser.tsx、stock-dashboard.tsx及相应fixture依赖。组件prop refreshKey?:number，与两个页面现有revision联动。不做自动高频轮询。

## 包2：旧历史只读审计与迁移前检

独立新增backend/app/services/stock_history_audit.py、backend/tests/test_stock_history_audit.py、scripts/audit-stock-history.py。

- [x] 仅读market_type=stock的真实统计，未来日期、无效/非有限/零/负价量、OHLC、重复/目录缺失等风险明确计数；样本上限50，不读取全历史到Python，不扫期货内容。
- [ ] 同日raw/qfq条件性价量核对未实施，明确not_performed；585392条全部unverified，不把不同复权基准直接比较。延后独立股票观察表/锚点迁移，不自动修复小数手。
- [x] CLI仅本地5433 READ ONLY、有限statement/运行超时，输出存在在DB访问前拒绝，0上游/0模型；异常不写假报告。
- [x] 测试真实SQLite/真实只读PG；保存选定公共数据聚合未变化证据（不是全库diff）。不修改旧表/迁移/账号，不碰其他工作包文件。

## 包3：集成、运行与交付

- [x] 专项/完整后端、前端Node/tsc、真实PG/HTTP和独立QA浏览器；只停核实身份的本轮后端，保留PG/Redis/前端原会话。
- [x] 真执行只读历史审计，说明全量历史不能认证的实际数量/口径；计划后续独立股票观察表/小数手/复权锚点迁移，禁止修改共用期货volume字段。
- [x] 独立审查并修重要问题；交付真实文件/命令/通过失败未执行、成本、回退、剩余风险及下一3至5包。
- [x] 单次05:15已创建并查看；届时读取本轮检查点续做。下一实际收盘仍待观察，不停止原续做或声称全系统完整。

## 00:37最新实际检查点

完整交付`docs/STOCK-STATUS-HISTORY-AUDIT-VERIFICATION-2026-10-19.md`。后端1985通过/3既有skip/502warnings，前端297及tsc通过；专项64通过-W error，真实PG17/同源HTTP14通过。status新增cached_daily证据误标与真实启动flag接线两个红绿回归。真实审计585392条/300只（5558目录中5258无旧历史），质量计数0但全部unverified；未重导、未迁移、0来源0模型。原三个版本--check通过；backend新listener12652须下轮重新核对身份。05:15应优先独立股票raw/因子/锚点/小数手兼容迁移设计与测试，不重复完成本轮状态UI，不抓未收盘日线。
