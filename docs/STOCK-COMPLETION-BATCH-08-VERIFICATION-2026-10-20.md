# B08 真实交付与续做检查点

检查点：2026-10-20 01:18（上海）。本轮五个工作包代码和本机第二层验证通过；**整个主计划仍部分完成，未完成生产发布和本轮浏览器人工验收**。不是把B08五包当成所有历史工作包完成。

## 1. 五包交付和数据流

### B08-1 财务会话隔离

- `frontend/src/stores/paper-trading.ts`：作用域包含会话代次、用户、市场、登录盘及原token；换号、同token重登、切市场立即清空私有账户/委托/持仓/流水，epoch使晚到成功和错误作废。读刷新有序；摘要只轮询账户，不每8秒重复拉全部订单。
- `frontend/src/lib/paper-api.ts`：委托、预估、撤单、持仓等兼容增加可选原token参数，使用现有请求前/响应后token校验；旧意图不得改用新用户token，资金写不自动重试。
- `frontend/src/components/dashboard/asset-summary.tsx` 改为消费同scope store；新增 `frontend/src/components/layout/financial-session-sync.tsx`，在主布局挂载，登录/换盘/确认切回期货后重新读取。
- `frontend/src/components/portfolio/virtual-funds-panel.tsx` 增加sessionRevision/market重挂载；保留原充值幂等键和未知提交核对，不按月/日限额。
- 独立复查真实复现一个P2：乐观切换后立刻读取，新市场偏好还未写完，409之后没有再读。本轮增加 `frontend/src/stores/auth.ts` 的 `marketSwitchPending`，仅当前会话最新排队切换可清除；旧成功/失败不解锁新意图。共享 `frontend/src/components/layout/stock-feature-boundary.tsx` 和资金面板等确认后才挂载工作区，store在pending时不读写，确认后同步。未靠重试交易解决竞态。
- 新/扩展测试：`stores/auth.test.mjs`、`stores/paper-trading.test.mjs`、`components/layout/financial-session-sync.test.mjs`、`components/layout/stock-feature-boundary.test.mjs`、`components/portfolio/virtual-funds-panel.test.mjs`。独立只读复查复验通过，无剩余具体P1/P2发现；不是全仓安全认证。

### B08-2 后端市场能力

- 新 `backend/app/core/market_capability.py`；六组股票router（stock、stock_market、stock_factors、stock_replay、stock_research_jobs、stock_research_reports）统一先校验当前用户股票市场，错误市场409+能力头+request_id+no-store。paper router反向拒绝股票用户。匿名401/冻结403原鉴权、资源owner/JWT盘范围不放宽，成功返回结构不变。
- 新 `backend/tests/test_stock_market_capability.py`，以及原股票测试的合法市场身份夹具；`test_international_metals_domestic_guard.py` 补充期货身份，原USD/CNY保护仍断言422。
- 旧PG脚本B03/B04/dual在所有期货验证之后，仅把私有schema的第二用户设为股票市场，再验证他人报告404；未将严格404放宽为409。B08另行验证真实错误市场409、匿名/冻结，两个权限维度分别覆盖。
- R02旧同步股票研究接口的任务化/预算归一、其他期货AI/挖掘入口的完整市场能力仍未在本轮完成，不把这组六router推广为全站验收。

### B08-3 有界补采与状态

- `backend/app/services/stock_eod_sync.py`：最近7个已到上海16:30收盘窗口的**自然日**，不是假定周一到周五交易；每周期最多1日期，原认领锁/唯一键，单日最多3来源请求。来源仍逐日核验日历；记录过的失败/休市/部分/成功不自动重试，超时租约保留失败。凌晨不采当天收盘，不填零/造价量。
- 新 `STOCK_EOD_CATCHUP_ENABLED=false`（`core/config.py`、`.env.example`、`main.py`）。新补采默认关闭，重载不会默默花费最近7天预算；旧当日16:30定时采集保留。**真实本地新开关未启用**，状态披露关闭，不声称自动历史漏日已经运行。
- 来源HTTP401/403、402、429、5xx、传输、解析错误分类保存，脱敏固定详情，不输出上游响应体/密钥、不自动重试。
- `stock_eod_status.py`、`api/stock_market.py` 提供只读有界catchup窗口、待核验自然日、失败/过期和恢复标记。`frontend/src/lib/stock-eod-status-api.ts` 兼容可选字段并严格校验窗口/日期/预算，`components/market/stock-eod-status.tsx` 展示开关、预算、失败和缓存恢复，不把待核验日期称已证实交易日缺口。
- 相关 `test_stock_eod_catchup.py`、`test_stock_eod_source.py`、`test_stock_eod_status.py`、`test_stock_eod_lifecycle.py` 和前端状态API/组件测试通过。

### B08-4 集成、复查与运行

- 新 `scripts/test-stock-completion-batch-08-postgres.py`，复用B02隔离框架，仅添加允许报告名；真实本地PG5433/Redis6380/JWT/ASGI HTTP验证。私有证券序列也隔离，不增公共序列；测试fixture不是供应商数据认证。
- B08 86检查全通过；受影响旧B03 61、B04 85、dual 83检查全通过，共315检查；私有schema和Redis测试key清理通过，各测试报告公共业务写0、来源0、模型0。
- 安全重载前只读确认研究任务均终态、挖掘/主播0、AI1paused，核实项目backend进程/端口身份后重启该backend并运行既有隐藏启动器；PG5433、Redis6380、backend8002、bridge8110、frontend3000就绪。backend PID19820是该时点观测，不作永久配置。
- 最后HTTP健康200；匿名paper/account及stock/market/sync-status401；login/dashboard/assets/market页面200。**页面200不证明登录后交互正确**。本轮不绕过先前浏览器安全拒绝，真实用户视觉/会话验收未执行。

### B08-5 失败批次的缓存审计恢复

- 初始真实库证据：10月19日是failed/attempt1，10月18日日历休市、10月16日已有有效部分快照；并非只有worker漏调度。旧泛化错误不能追溯为某一种供应商原因。
- 新 `scripts/diagnose-stock-eod-source.py` 仅针对现存失败日、受控本地PG/日志路径；一次诊断最多3请求、90秒，不写业务表。实际2026-10-19诊断**3次调用**（trade_cal/stock_basic/daily）成功，5564源记录；目录版本与当前PG相同。没有在失败后无限重试，没有再下载同批。
- 新 `backend/migrations/20261020_007_stock_eod_recovery.sql`、`backend/scripts/migrate_stock_eod_recovery.py`、`backend/app/models/stock_eod_recovery.py`：expand新增审计表，迁移checksum/check-only/重复执行/锁和超时校验，不改003等旧迁移，不恢复应用启动DDL。审计事件**应用层只追加**，未声称DB trigger/角色已强制不可变。
- 新 `backend/app/services/stock_eod_recovery.py`、`scripts/recover-stock-eod.py`、`backend/tests/test_stock_eod_recovery.py`：本地操作CLI，默认只读dry-run，显式apply才写。缓存重新标准化，校验价量、时区/窗口、目录、来源元数据、完整hash；最多10000报价，JSON/字节预算。不为缺值造价，不能覆盖已有有效/运行/休市批次。
- 只有空failed批次可恢复；事件保存完整原失败行、操作来源/reason/hash/幂等键/方法/新版本。行锁+事务锁+唯一键，审计和批次新投影同事务；原attempts=1是原采集次数，额外恢复1次独立标注。源响应hash是声明来源的证据，不是独立交易所认证或防管理员篡改认证。
- 实际先dry-run，再对**本地公共业务schema**显式应用007和单个19日失败批次（不是只跑夹具）。同key再次apply返回already_applied，同恢复ID、零重复事件；两次恢复应用合计新增来源/模型调用0。
- 实际恢复ID `4a92f937-5906-4240-9389-3504535d09c2`，原batch `f66ca04e-60b8-44c5-ad3c-4d4126a7a404`；旧失败保存审计，投影标记eod_recovered，状态partially_completed，不标全覆盖。
- 测试涵盖PG迁移check/重复、事务中断双表回滚、并发幂等、意图冲突和checksum漂移拒绝。原历史/qfq、模拟账户/委托/流水不由恢复CLI改写。后台既有归档worker随后把该真实快照归档到影子数据集。

## 2. 最后只读数据观测（01:15）

证据 `logs/full-code-audit-snapshot-20261019-171552.json`、`logs/b08-readonly-final.log`；REPEATABLE READ READ ONLY、source/model0。

|范围|实际观测|不能据此声称|
|---|---|---|
|股票目录|5558证券、110行业|每证券都有全历史|
|最新盘后|2026-10-19；覆盖5545/5558，缺13、未匹配19；源5564行|全市场100%覆盖/独立真实认证|
|Decimal影子归档|11093观测、5549证券、2日期|五年版本输入/复权与公司行动已齐全|
|旧研究历史|300证券、585392行，仍到10月16日|已把原价直接混入旧qfq/研究能取19日全历史|
|回放数据集|3证券、3版本，仍到10月16日|全市场回放可交易|
|固定五年预检|2302800面板格>2000000，8931缺价，over_limit|五年容量/时点/完整性已通过|

缺13证券保留，不填价格；未匹配19不按猜测挂到已有代码。原价归档与旧qfq分离，K线不能靠混锚点变长。

## 3. 实际命令与验证结果

命令在仓库根运行；Node/TSC在frontend目录。下列均实际执行，不是待执行建议。

```powershell
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests -q
backend/.venv/Scripts/python.exe -X utf8 scripts/test-stock-completion-batch-08-postgres.py
backend/.venv/Scripts/python.exe -X utf8 scripts/test-stock-completion-batch-03-postgres.py
backend/.venv/Scripts/python.exe -X utf8 scripts/test-stock-completion-batch-04-postgres.py
backend/.venv/Scripts/python.exe -X utf8 scripts/test-dual-market-financial-postgres.py
backend/.venv/Scripts/python.exe -X utf8 scripts/audit-product-readiness-local.py
./scripts/run-local.ps1 -TimeoutSeconds 60
node --import ../logs/node-alias-register.mjs --test (Get-ChildItem -Recurse src -Filter *.test.mjs | ForEach-Object FullName)
node node_modules/typescript/bin/tsc --noEmit --incremental false
```

- 后端最终 **2509 passed、3既有skipped、630 warnings，exit0**；`logs/b08-backend-final.log`。本轮后端测试不因既有3skip成为零未执行，也不称所有依赖告警已解决。
- 前端最后 **449 passed、0 failed/skip**；`logs/b08-node-final.log`；TSC0，`logs/b08-typecheck-final.log`。
- 实际组件/store切换确认反例先红7失败，再绿23通过；`logs/b08-market-confirmation-red.log` / `green.log`。独立复查重跑21相关测试通过。
- B08 PG报告 `logs/stock-completion-b08-pg-20261019-170353.json`（86）；旧回归 `...b03-pg-20261019-171531.json`（61）、`...b04-pg-20261019-171534.json`（85）、`...dual-pg-20261019-171537.json`（83），全部passed/isolated_schema_cleaned=true。
- 真实失败保留：`b08-backend-regression.log` 初次48个旧夹具未声明股票市场；`b08-backend-confirmation.log` 后续1个期货夹具未声明市场；合法夹具补齐但不改owner/mode/422断言。B03先在 `b08-b03-guard-red.log` 他人股票报告404断言失败，之后正确股票scope仍严格404通过。
- TDD还保留财务scope/轮询、market能力、catchup/默认关闭、来源分类、恢复和前端状态红日志；未以删测试、吞异常、硬编码或降低金融断言凑通过。
- 本轮**未执行新生产构建/部署、浏览器人工E2E、全历史独立源认证、完整PIT/五年性能和国际金银规格认证**。此前Windows standalone复制EPERM仍是发布风险，未假报解决。

真实来源与恢复命令：

```powershell
backend/.venv/Scripts/python.exe -X utf8 scripts/diagnose-stock-eod-source.py --date 2026-10-19 --output logs/b08-eod-source-diagnostic-20261019.json
backend/.venv/Scripts/python.exe -X utf8 scripts/recover-stock-eod.py --snapshot logs/b08-eod-source-diagnostic-20261019.snapshot.json --idempotency-key 838b53ce-2b8f-4856-bf83-e49a982daac1 --reason 'Restore failed 2026-10-19 from bounded authorized-source diagnostic' --output logs/b08-eod-recovery-dry-run.json
backend/.venv/Scripts/python.exe -X utf8 scripts/recover-stock-eod.py --snapshot logs/b08-eod-source-diagnostic-20261019.snapshot.json --idempotency-key 838b53ce-2b8f-4856-bf83-e49a982daac1 --reason 'Restore failed 2026-10-19 from bounded authorized-source diagnostic' --apply --migrate --output logs/b08-eod-recovery-applied.json
backend/.venv/Scripts/python.exe -X utf8 scripts/recover-stock-eod.py --snapshot logs/b08-eod-source-diagnostic-20261019.snapshot.json --idempotency-key 838b53ce-2b8f-4856-bf83-e49a982daac1 --reason 'Restore failed 2026-10-19 from bounded authorized-source diagnostic' --apply --output logs/b08-eod-recovery-idempotence.json
```

已应用命令是审计记录，**不要换幂等键或文件名盲目重做恢复**。实际无密钥输出；供应商单价未知，不把3次来源调用说成费用为0。模型调用0，新依赖0；既有常驻服务的常规流量未冒称包含在受控探针计数内。

## 4. 兼容、迁移和回退

- 007新增表/版本行，兼容旧应用；SQL checksum `f82224f023a548a451f26bcbc19a52606bc75ff66a0f49f1d1331872d7cca339`。本地已apply+repeat+check，非生产全局基线迁移验收。
- 应用代码可回退而保留新表和恢复审计。事务中断可回滚本次事件/投影；**提交并被归档worker消费后不能盲目恢复failed或删除事件**。先停相关worker、审计派生数据，再做独立前向修复；没有提供未经验证的自动数据逆迁移。
- 不删除旧迁移/字段、旧报告/缓存键；catchup和恢复标记为兼容可选响应。409是有意能力边界收紧，直接API消费者必须先选择适用市场；401/403与同市场owner404保持。
- 不恢复 `_init_tables` 启动DDL。P01全局迁移、S04持久人工确认、S06服务端会话撤销仍未完成，不解除保护。

## 5. 人工验证路径及下一轮五包

本轮浏览器未执行，以下是明确待验收步骤，不是已通过结论：
1. 本人登录 http://localhost:3000，A股行情查看19日盘后5545/5558、13缺失/19未匹配、恢复提示及补采关闭状态；不能拿原价2日要求混入旧qfq。
2. A股→期货→A股快速连点，切换期间显示确认中，不显示另市场财务；确认后账户正常刷新；模拟资金用自定义金额，未知提交核对幂等流水，不连续盲点。
3. 同用户换盘、退出换号及同token重登，账户/委托/持仓/流水不能沿用旧scope。股票仅日线回放，不能访问期货充值/订单；期货不能直接访问股票报告API。
4. 已保存报告复开/导出/对照/恢复参数，错误用户及盘拒绝。AI供应商限流仍由上游额度决定，不做无限模型探针；聊天交易写保护保持。

下一轮依赖五包（不是本轮已完成）：
1. R02：盘点旧同步研究消费者，兼容迁到有预算/幂等/取消的异步任务入口，保留响应版本策略。
2. GP：版本化固定真实输入、完整种群/RNG/best_seen检查点，逐代原子推进及暂停取消/重启、市场/盘隔离；不再每tick首代。
3. 真正期货研究：授权数据/换月/交易样本和方法契约→任务/IC分组/报告/UI；本地合格期货历史缺失不能借股票冒充。
4. S04/P01：持久人工确认+同事务写幂等、全局DDL版本迁移及发布/回退；逐步开放股票只读AI/Schema策略草稿，不允许模型自确认。
5. D01/D03：按预算启用/演练补采、13缺失/19未匹配逐项真实对账、原价与复权版本桥接和历史扩池。基准、自定义DSL、五年/PIT、回放扩池/公司行动、收藏/消息仍按主计划保留。

本轮不停止续做自动化，不声称全系统完整可用。
