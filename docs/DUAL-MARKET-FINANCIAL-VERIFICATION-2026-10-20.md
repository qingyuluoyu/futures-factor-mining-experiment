# B07 双市场审查和金融改良真实交付记录

时间：2026-10-20北京时间00:18；计划文件保留任务开始日 `docs/superpowers/plans/2026-10-19-dual-market-financial-improvement.md`。本轮正常用户请求，不是自动化触发。

## 完成状态

- B07-1：已完成代码审查和新功能矩阵，`docs/DUAL-MARKET-FUNCTION-REVIEW-2026-10-20.md`。每个菜单标明股票/期货现状、前后端验收目标；没有把旧审查所有缺陷重复算成当前缺陷，也没有把新测试当全站认证。
- B07-2：A股方法v5中性化可识别性已实现，实际计算、PG/HTTP、保存与报告读取验证通过；报告显示原保存政策并兼容旧报告缺字段。金融域政策、信号滞后/成本/公告择期保留。
- B07-3：期货新增本盘缓存参考估值和三处前端消费已实现，第二层验证通过；最新本地后端已重载。完整结算/今日基准、旧客户端迁移、财务页面全部会话竞态、浏览器人工验收未完成，不能把整个账户系统或全系统标完成。

## 实际修改文件

后端：
- `backend/app/services/stock_factors/engine.py`、`METHOD.md`：v5、行业组最低样本/满列秩/残差自由度、政策元数据。
- 新 `backend/app/services/paper/valuation.py`：typed参考估值、账户/持仓join、ORM刷新、本盘缓存、Decimal多空估值与失效策略。
- `backend/app/api/paper.py`、`backend/app/services/paper/account_svc.py`：GET新增valuation、request_id状态日志、旧账面契约标志。
- 新 `backend/tests/test_dual_market_financial_improvement.py`；`test_stock_financial_selection.py`、`test_stock_value_policy.py`仅升级当前方法明确断言，并增加政策断言，旧数值/资格断言保留。

前端：
- `frontend/src/lib/paper-api.ts`；新 `paper-valuation.ts`、`paper-valuation.test.mjs`。
- `frontend/src/components/dashboard/asset-summary.tsx`、`frontend/src/app/(main)/assets/page.tsx`、`frontend/src/components/portfolio/virtual-funds-panel.tsx`：消费新参考估值，未知浮盈/今日盈亏/风险率不显示旧假0，旧后端缺字段仍安全退到账面口径。
- `frontend/src/lib/stock-research-api.ts`、`stock-research-api.test.mjs`；`frontend/src/components/factor-research/research-report.tsx`、`research-report.test.mjs`：保存政策Schema/旧报告兼容及解释。

验证/文档：
- 新 `scripts/test-dual-market-financial-postgres.py`；`scripts/test-stock-completion-batch-02-postgres.py`仅允许新的dual报告标签。
- 本记录、功能矩阵、详细子计划、主计划检查点。无新依赖、无提交/分支，本目录无Git仓库，因此没有伪称有git diff/commit。

## 金融、契约、兼容与回退

### 股票

原问题：欠定/恒定市值回归仍可能返回残差，单成员行业减自身得0而被当作可用因子。新方法：每组≥2有效观测，联合矩阵满列秩、n>rank；不合格保持缺失，不在中性化后填回。`neutralization-identifiability-v1`和method v5跟随结果持久化，不修改旧报告。

无生产表结构或数据迁移。现有队列会拒绝旧method任务而非暗换新法；部署前等待活动任务结束。回退v4会重新引入旧计算问题，只应做明确风险决策；已保存v5报告仍按其版本读取，不改写历史。正式发布需保持新旧结果的版本解释。

### 期货

新增GET `/api/paper/account` 字段valuation：方法/状态/币种/盘、Decimal字符串账面和参考权益/浮盈/风险、nullable今日盈亏、采样/缓存接收时间、issue/note。状态cache_marked/no_positions/unavailable。旧数字字段保留，`equity_basis=book_balance_plus_frozen_excludes_floating`、`legacy_pnl_fields_available=false`明确旧语义；AI任务旧现金基线不被偷偷改变。

参考浮盈=Σ(参考价−存储成本价)×手数×存储乘数×多空符号，权益=账面余额+冻结+浮盈。无持仓浮盈0；缺单日基线的today_pnl=null；负权益不裁零，权益≤0风险率null。任一持仓无价/错源/未知合约/时间不合格/数值异常时不返回部分成功估值。一次Redis MGET、2秒、无重试/补价，最大1000持仓。virtual只接受OpenCTP来源且拒绝pulse；live拒绝virtual来源。接收时间不是交易所行情时点，白名单不是独立数据认证，120秒后应显示不可估值。

join同时读取本人本盘账户和正持仓，强制`populate_existing`防ORM旧实体与新持仓拼成不同快照。Decimal直接读数据库，不通过摘要float回转。UI的其他旧字段与全局store仍可能存在会话竞态，已列P1后续，不声称本轮全部解决。

兼容扩展，无DDL/清库/历史重算；撤回新后端时新前端会显示账面/未知而非误读旧0。撤回新前端会丢失未知状态保护，须明确此风险，不推荐单独回退UI。旧字段应后续单独版本化迁移，不能本轮直接删改以破坏调用方。

## 实际命令、通过和失败

以下均在项目根执行；Node命令在frontend目录执行。

1. `backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_dual_market_financial_improvement.py -q`：最初9反例失败，`logs/dual-market-financial-red.log`。初次实现与engine/custom_funds相关共42通过，`logs/dual-market-financial-green.log`。
2. 增补边界后同一命令：1e100导致Decimal量化InvalidOperation反例失败，其他16通过，`logs/dual-market-boundary-red.log`；改为不可估值，不吞为0。无持仓、负权益、future/缺接收时间、bool/NaN、失败不重试测试保留。
3. `node --test src/lib/paper-valuation.test.mjs`：缺helper初始3失败；实施后3通过。政策API/真实报告渲染新增2反例也先失败再修，`logs/dual-market-policy-view-red.log`。
4. `backend/.venv/Scripts/python.exe -X utf8 scripts/test-dual-market-financial-postgres.py`：最终 **83检查全部通过**，`logs/dual-market-pg-http-final-2.log`、`logs/stock-completion-dual-pg-20261019-161018.json`；私有schema和随机前缀QA Redis keys均清理，原三账户余额、订单/流水不变。真实JWT/ASGI/PG/Redis；无mock身份或金融内核，行情仅明确标注的私有合成测试样本，不是供应商认证。
5. PG脚本真实失败保留：首轮没有真实worker却错误期待创建成功，被503拒绝；脚本改为断言503，调用真实service/state machine保存实际计算结果，**未伪装healthy worker**。第二轮误认为result有嵌套结果，实际是增量平铺契约，KeyError；修脚本核对全部原字段和版本。随后增加ORM旧实体反例先失败，`logs/dual-market-identity-snapshot-red.log`，加入populate_existing后通过。每次schema/keys清理为true。
6. `backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests -q`：首次4失败均为旧v4断言（2476通过/3skip）；更新当前方法断言，未改旧报告fixture。第二次2487通过/3skip，1个既有WS恢复测试0.2秒等待超时，完整日志保留 `logs/dual-market-backend-regression-final.log`，未改WS代码/测试。专项 `... -m pytest backend/tests/test_ws_subscriber_recovery.py backend/tests/test_dual_market_financial_improvement.py backend/tests/test_stock_research_engine.py -q` **58通过**。第三次完整确认 **2488 passed、3既有skip、626 warnings、117.27秒、退出0**，`logs/dual-market-backend-regression-confirmation.log`。这不能证明该WS偶发超时根因已修。
7. 直接不带loader运行Node全测试曾失败（别名@/lib无法解析），是运行方式错误，`logs/dual-market-frontend-regression.log`。实际全量命令 `node --import ../logs/node-alias-register.mjs --test (Get-ChildItem -Recurse src -Filter *.test.mjs | ForEach-Object FullName)`：最终 **433通过、0失败/skip**，`logs/dual-market-frontend-confirmation.log`；`node node_modules/typescript/bin/tsc --noEmit --incremental false` 退出0，`logs/dual-market-tsc-confirmation.log`。
8. `backend/.venv/Scripts/python.exe -X utf8 scripts/audit-product-readiness-local.py`：READ ONLY观测成功，`logs/dual-market-readonly-audit.log`；实际数据缺口和五年over_limit见矩阵，未为了测试通过放宽容量门或补价。
9. 重载前实际READ ONLY统计：研究33条均终态（9failed/17completed/7cancelled），挖掘/主播0，AI交易1paused，无pending/running，`logs/dual-market-before-reload.log`。核对清单PID12292、Win32进程/父进程项目路径、8002监听身份后才停止本项目后端，执行 `./scripts/run-local.ps1 -TimeoutSeconds 60`，五服务ready、退出0，`logs/dual-market-reload.log`。
10. 使用httpx只读HTTP探针：后端health200；匿名paper/account和stock/factors均401；前端login/dashboard/assets/profile均200，`logs/dual-market-runtime-http.log`。**页面HTTP200不证明用户会话/图表/交易端到端通过**。

## 未执行、成本与剩余风险

- 本轮没有重新尝试或绕过之前浏览器安全策略拒绝；当前用户登录后的三处估值视觉、换号/切盘、股票报告政策展示等人工验收未执行。没有重新做生产构建、完整发布/迁移演练、供应商报价认证、当日结算、实盘或国际金银接入。
- 本轮受控验证上游和付费模型调用0；真实PG/Redis计算与回归增加本机CPU/I/O。常驻服务原有后台行为不计为这项“0新增测试调用”声明；新估值每次请求有有界PG join和缓存MGET，不增加模型调用。
- 当前EOD仍缺10月19日批次；目录/历史/回放/归档差距明显。旧同步研究绕过预算、股票API能力校验、全局期货挂载、GP检查点/进化、真正期货研究、服务端确认/会话撤销和版本迁移/发布均仍待完成。
- 现有3个skip和626warnings未删除/隐藏；最后完整回归通过不等于无风险。未停止续做自动化，04:40/09:50安排不改。

## 人工验证路径

1. 访问本地3000，登录期货模拟盘：工作台、资产、个人模拟资金三处应说明账面或缓存参考，不再把未知浮盈/今日盈亏显示成0。有当前本盘合格缓存才能显示参考浮盈；休市/缺价是“—”，不能强行拉假价。
2. 有持仓时检查数量/方向/乘数及本盘报价，用保存成本价手工核对；缓存失效应退到账面口径。不要为验收在真实用户账本手工改价/金额。
3. 切A股，对已覆盖数据运行/读取新研究：看到v5及中性化政策说明；读取旧报告显示政策未记录，不自行补v5。恒定市值/样本不足只在隔离夹具验证，禁止改公共行情造例子。
4. 换号/换盘测试仍需专项防串状态；若看到旧资金/任务立即记录，不以刷新后恢复作为修复通过。

下一轮五包为：盘后漏日闭环、API/前端财务会话Scope及旧同步入口归一、GP固定输入/种群检查点、真实期货研究切片、持久确认/迁移发布安全。详见功能矩阵和主计划。
