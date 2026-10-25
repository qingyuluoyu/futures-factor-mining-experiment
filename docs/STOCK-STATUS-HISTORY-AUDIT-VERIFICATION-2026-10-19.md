# 2026-10-19 00:37 盘后状态与旧历史审计检查点（北京时间）

## 状态与边界

本轮三包已实施并完成下述本机验证；整个系统仍为**部分完成**，不是生产发布验收。用户要求未完成内容05:15继续，已通过应用工具创建并查看本对话**今天10月19日05:15单次**续做 `a-05-15`（COUNT=1）；原02:05/07:10续做未改变。无Git目录，不提交、不建分支、不覆盖前轮文件。

本轮0供应商HTTP、0模型调用、无新增依赖或数据库迁移，不重导/覆写旧历史、不接真实账户。实际重启会执行原应用已有的幂等初始化路径，不能将“没有新迁移”描述为从未执行任何DDL。凌晨未到收盘，不采集或编造Oct19日线。

## 包1：用户可见的盘后状态

- 新增需登录的 `GET /api/stock/market/sync-status`，v1字段契约见本轮计划和前端Zod。5秒DB总超时、仅2个LIMIT1的元数据投影查询，不读取整份quotes、不写任务、不请求供应商；no-store、追踪ID、错误503和类名脱敏日志。
- 行情/工作台共享组件，覆盖12种运行状态、加载/取消/失败重试、手动刷新；10秒请求超时，无自动高频轮询。刷新只读状态，不触发采集。
- 不从日期推测交易日历或认证新鲜度；运行过期仅显示expired，不擅自重领任务。当前真实结果是before_window、worker运行、今日任务null；最新仍Oct16，5548/5558，缺价10、目录未匹配19。
- 旧快照没有daily原响应证据，页面明确缺失；只读元数据不等于独立认证。审查发现cached_daily仅有目录/日历摘要也会被判定有原响应证据，已先3红后绿修复：必须live_http且恰一daily，合法SHA/带时区接收时间/1..8MiB字节数/匹配行数。持久测试覆盖布尔/越界/非法时间等。
- 实际HTTP发现读取不存在的state.settings导致误显manual_only，已先3红后绿修复：启动函数仅保存公开的sync_enabled布尔，路由读同一值，不把包含密钥的完整settings放入状态响应。

## 包2：旧历史只读审计

新增本地PG5433审计CLI，REPEATABLE READ + READ ONLY，statement5秒、lock2秒、总30秒；3条核心SQL聚合/重复检查/全局最多50条样本，股票过滤，不读全历史到Python，不扫期货内容。输出已存在时在访问DB前拒绝。

真实报告 `logs/stock-history-readonly-audit-final-20261019.json`：585392条、300只、2018-01-02至2026-10-16；当前目录5558，只300只有旧历史，5258没有旧历史。未来/非法代码/非有限零负价/异常量/OHLC/重复/目录缺失/上市前日期计数均0，**不能由此认证真实性**：585392条全部仍unverified，缺逐批来源、采集/可得时点、版本和复权锚点；整数手是否已截断无法反推。零量本身只是需检查，不判为假价或停牌。

本轮未做同日raw/qfq条件性核对，报告明确not_performed，两者不可直接当等价价格比较。质量统计不等于全市场历史覆盖，更不等于历史时点无幸存者偏差。

`logs/stock-history-public-readonly-proof-20261019.json`保留CLI前后公共stock历史count/minmax/sum(id)/sum(close)/sum(volume)、目录数量及EOD数量/最新日期相等证据；只是选定聚合不变，不是密码学全库diff。未改共用KlineHistory整数volume字段、旧报告或回放数据集。

## 实际修改文件

- `backend/app/services/stock_eod_status.py`、`backend/tests/test_stock_eod_status.py`（新增）。
- `backend/app/api/stock_market.py`（新端点）、`backend/app/main.py`（启动公开布尔接线）。
- `backend/app/services/stock_history_audit.py`、`backend/tests/test_stock_history_audit.py`、`scripts/audit-stock-history.py`（新增）。
- `scripts/test-stock-status-local.py`（新增真实本地PG/同源HTTP验证，限既有qa_账号）。
- `frontend/src/lib/stock-eod-status-api.ts`及`.test.mjs`、`frontend/src/components/market/stock-eod-status.tsx`及`.test.mjs`（新增）。
- `frontend/src/components/market/stock-board-browser.tsx`及`.test.mjs`、`frontend/src/components/dashboard/stock-dashboard.tsx`及`.test.mjs`（共享组件接入及fixture）。
- `docs/superpowers/plans/2026-10-19-stock-status-history-audit.md`、本记录、原总计划追加检查点。

## 实际命令与结果

所有路径相对仓库；前端命令从frontend目录执行。日志保留失败，不删除测试/跳过或放宽断言制造通过。

| 命令/验证 | 实际结果 | 证据 |
|---|---|---|
| `backend/.venv/Scripts/python.exe -W error -m pytest backend/tests/test_stock_eod_status.py backend/tests/test_stock_history_audit.py -q` | **64 passed** | `logs/stock-status-history-final-focused-20261019.log` |
| `backend/.venv/Scripts/python.exe -m pytest backend/tests -q --junitxml=logs/stock-status-history-backend-reviewed-20261019.xml` | **1985 passed / 3既有skipped / 502既有warnings**，61.36秒 | 同前缀`.log`、`.xml` |
| `node --import ../logs/node-alias-register.mjs --test (Get-ChildItem -Recurse src -Filter *.test.mjs \| ForEach-Object FullName)` | **297 passed** | `logs/stock-status-history-frontend-reviewed-20261019.log` |
| `node node_modules/typescript/bin/tsc --noEmit --incremental false` | 退出0 | `logs/stock-status-history-typescript-reviewed-20261019.log` |
| `backend/.venv/Scripts/python.exe scripts/audit-stock-history.py --output logs/stock-history-readonly-audit-final-20261019.json` | 真PG退出0，READ ONLY on；同路径复跑FileExistsError退出1且不访问DB | final JSON、`logs/stock-history-audit-focused.log`（相邻59通过） |
| `backend/.venv/Scripts/python.exe scripts/test-stock-status-local.py` | **14检查通过**，两个既有QA用户、401/追踪/缓存/真实PG契约/前后数据聚合不变 | `logs/stock-status-http-final-20261019.log`、`logs/stock-status-http-results-20261019.json` |
| `backend/.venv/Scripts/python.exe scripts/test-stock-eod-local.py` | **17检查通过**；隔离schema并发/回放幂等/失败原子性/JSON投影/公库与序列不变/清理 | `logs/stock-status-history-pg-20261019.log`、`logs/stock-eod-postgres-results-20261018-163444-f07f728c.json` |
| `backend/.venv/Scripts/python.exe backend/scripts/migrate_stock_replay.py --check`；同方式research_jobs/eod | 三个既有版本结构与SHA全部通过，applied=false | 原001/002/003源文件未修改；终端实测 |
| `backend/.venv/Scripts/python.exe logs/check-running-stock-jobs.py`；`pwsh -NoProfile -File scripts/run-local.ps1` | 无运行研究任务；验证命令行身份后只重启本项目backend，其他服务复用 | `logs/stock-status-local-restart-final-20261019.log`、`logs/local-services.json` |
| 独立QA浏览器工作台→刷新状态→行情 | 可见before_window、Oct16、覆盖缺口、原响应缺失；未下单/充值/改用户原会话，QA页已关闭 | `logs/stock-eod-dashboard-status-20261019.png`、`logs/stock-eod-market-status-20261019.png` |

失败与修正：最初status模块缺失16红；证据范围3红；启动接线3红和第一次真实HTTP失败均保留相应red/HTTP日志。最初将邻接旧board测试一起用-W error导致3个fixture错误（旧StockFinancial.utcnow弃用警告），不改无关代码；普通模式邻接25通过/9warnings，新专项-W error64通过。首次裸node全量测试遇既有@/别名加载失败；使用原有loader重跑297通过，不修改期货测试断言。两轮独立审查已复验，修复后无新增Critical/Important；末轮审查64通过。

未执行：本轮没有再次完整发布build、真实下一交易日收盘验收、独立交易所核验、全历史来源重取、股票AI付费端到端。既有Windows standalone EPERM/主题hydration及原502警告不是本轮修复范围，不声称发布完成。

## 兼容、回退、运行和成本

仅增加只读API/可见组件和离线CLI，无破坏性契约/新DDL，无金融公式、币种、手续费、账户资金或撮合规则变化。回退可移除两处组件接入、新API及公开flag赋值；只删此次新增字段/模块会保留原worker/旧数据，但前后端须同步回退以免新组件请求404。不要删原表/恢复整库/清Redis；原三迁移保持兼容。CLI报告是离线文件，不回写。

本机监听：backend12652:8002、frontend7268:3000、PG3532:5433、bridge14236:8110、原WSL keepalive32884与Redis6380复用。以后必须重新核对PID身份，不能照本记录直接kill；原生6379未触碰。health200仅进程就绪，market.live.ok仍false、virtual67报价键且CTP心跳缺失，不代表期货所有行情可用。0新增外部费用；状态刷新最多2个有界SQL，审计本地聚合成本，未自动高频轮询。

## 人工检查与05:15下一轮四包

人工仍待用户验收：在原页面登录，切到A股工作台/行情，核对最近快照日期、缺口和原响应标记，点击刷新仅GET状态；切回期货确认原账户/图表不受新组件影响。观察真实收盘要等16:30后，不能在05:15生成今天价格。

1. 股票专用版本化日线/因子观察表设计与兼容扩展迁移：Decimal小数手、raw价格、因子可得时点、复权锚点、批次来源与版本；测试隔离PG迁移/回退，不改共用期货volume，不冒充旧行已有证据。
2. 安全逐批导入/校验/切换：先显式预算和批次幂等、断点/失败状态；保留旧qfq及历史报告，明确未验证旧数据。raw与qfq/锚点不混接，禁止盲重导585392条。
3. 10缺价/19未匹配的受控标识与来源对账、实际下一收盘daily证据落库及缺口统计；日历/上游失败不造价，不无限重试；离线漏日补采尚未实现。
4. 股票AI只读证据/预算/权限与报告回归，以及期货live/国际金银可信合约规格、交易日历、币种和授权/Windows发布回退验证。国际金银仍未开放模拟交易；只模拟、不接真实账户。优先可安全落地部分，不把外部阻塞藏成成功。

本轮审计/状态读取并未解决全部历史真实性、完整历史覆盖、付费AI和发布阻断，故不停止原续做自动化，不称全系统完成。
