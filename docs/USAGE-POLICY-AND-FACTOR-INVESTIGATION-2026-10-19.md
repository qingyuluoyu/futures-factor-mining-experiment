# 取消业务次数配额及两项因子问题调查（2026-10-19）

## 结论与完成边界

**因子/回测业务次数配额已取消，最新本地后端已重载；超级因子和期货研究的问题已定位，但没有宣称两项功能已完整修好。**

本轮对应三包：B05-1取消使用配额，B05-2超级因子调查与错误候选止损，B05-3期货研究入口调查。计划为 `docs/superpowers/plans/2026-10-19-usage-policy-and-factor-investigation.md`。

## 1. 已实施的使用策略

- 因子实验室搜索、LLM生成、公式回测、公式对照，及历史策略回测、AI合成K线生成，旧入口全部经过同一兼容用量服务。因子/回测不再按每天3/2次或用户覆盖值限制，已用Redis旧计数不会再触发403。
- 兼容 `get_effective_limits` 的factor_daily/backtest_daily字段固定None；旧 `check_and_incr` 签名保留，即使旧调用仍传limit=1也不会读取/扣减/限制。未知类别仍拒绝，登录与权限没有绕过。
- 进一步复查发现 A 股研究还有**累计1000份任务后永久不能再建**的门槛（无自助删除/归档入口），也已移除。第1001次可创建；已有活动任务仍429。不是只改管理员默认数字。
- 旧数据库列、用户历史覆盖值、Redis旧keys不删除，不批改公共数据库。系统配置旧字段回显0，旧客户端提交正数也按0保存；用户旧覆盖字段保留兼容但不参与判断，schema标deprecated。
- 管理端全局/用户两处不再提供日次数输入或提交旧字段。明确保留活跃任务、单次token/调用轮数/超时、数据大小与质量检查。没有把这些保护误称成已有按金额计费预算。
- 不取消交易规则、权限、幂等、结果大小、队列背压、GP搜索参数/运行时间、历史读取分页、外部数据恢复预算等保护。注册open策略此前已不按日次数限制，managed注册安全策略不在金融研究使用配额范围内。

## 2. 超级因子“挖不动”的已证实原因

### 2.1 后台实际上重复第一代，没有连续进化

控制流证据：

- `backend/app/services/factor_mining/loop.py:193` 每次tick重新创建 `search_stepwise`，传入当前代数，拿一个快照后break并丢弃生成器。
- `backend/app/services/factor_lab/search.py:692` 每次重新初始化固定seed的RNG并创建原始种群。
- `search.py:759` 对已完成代只是continue，没有重演或恢复种群演化；`:794` 先yield快照，`:801` 之后才交叉/变异。
- 因而一代一tick模式从未走到繁殖代码；历史champions只恢复进best_seen，不是完整种群。增加总代数不能解决这一控制流错误。

主执行者纯内存复现（不接数据库/行情/模型）：240根明确合成测试OHLCV，日期改为合法连续日，population=12、generations=4、seed=7，关闭测试段和WF**仅隔离算法控制流**，不改变产品门槛。

`logs/stock-completion-b05-mining-reproduction.log`：

- 模拟后台四tick：候选轨迹完全相同，交叉0、变异0，分数始终0。
- 单个生成器连续推进：交叉28、变异9，分数由0变至约0.623。
- 这些分数仅为fixture控制流对照，不是策略收益或市场验证。旧测试7项通过，但没有覆盖后台每tick的连续种群演化。

### 2.2 “期货日线有8个品种”是脏标签造成的假象

真实本地PG只读事务、10秒statement_timeout：

- `factor_mining_tasks` 当前**0条**；没有用户失败任务可重开，不能声称某个真实任务的错误栈已复现。
- `kline_history` 标记futures的17,008根、8个code，实际全为 `300308.SZ/300316.SZ/300394.SZ/300408.SZ/300413.SZ/300418.SZ/300433.SZ/300442.SZ` 股票代码。
- 股票标签下另有300只/585,392根。期货产品标识域在这一本地日线表中为0；这不代表所有Redis、远端tickdata或分钟源都无期货数据，只证明本机超级因子**日线PG路径没有可用期货产品**。
- 原 `list_mining_symbols` 只看market_type标签，把这8个股票code显示为可挖期货；`loader.py:76` 的期货代码解析不能识别它们，创建日线会失败。

**已做最小止损**：候选必须同时是futures标签与当前期货合约注册表中的产品code；不再把股票错标签列成期货可挖品种。实际只读验证现在eligible=0，17,008脏标签行仍原样保留。没有批量改市场、去后缀、覆盖原价或重导数据；后续需逐源/hash/重复冲突核对再迁移，不能盲目UPDATE。

证据：`logs/stock-completion-b05-mining-readonly.log`、`logs/stock-completion-b05-symbol-readonly.log`。此检查只验证代码域，不追认证明数据授权、来源、换月规则或质量。

### 2.3 其他已确认但本轮未修复的链路

- `loop.py:65` 在非交易时段完全不推进，历史日线也暂停；界面未明确说明休市等待。不能简单删时间门，否则分钟路径可能在休市反复拉供应商。
- 模型/实际表只有seed_tokens、champions两个JSONB，`population`是整数超参；没有完整种群、RNG、候选池、checkpoint版本或固定数据hash。不能通过保存冠军/在内存cache塞生成器冒充可重启断点续训。
- 每tick重新取数据，输入不固定，无法严格复算。
- `frontend/src/components/super-factor/use-mining-tasks.ts:144` 将冠军读取失败变成[]，把接口错误和“没有合格因子”混淆。
- 任务模型没有market_type/trading_mode，资源查询仅按user_id+id；前端仅切市场不等于后端实虚盘任务隔离。

本轮没有修改GP公式、质量门、搜索引擎或自动恢复已知错误算法，也没有造冠军。已知缺陷仍阻止把该模块称为正式研究可用。

## 3. 因子研究只有A股的原因

- `frontend/src/app/(main)/factor-research/page.tsx:6` 无条件渲染StockResearchEntry；后者明确拒绝期货。菜单却两市场都显示“因子研究”，是实际入口与能力不匹配。
- 全仓未发现隐藏、可直接恢复的完整期货研究组件。现有workspace调用股票目录、覆盖、任务、持股/市值中性等股票契约，不能改标题或传market参数就变成期货研究。
- 期货原功能还在：`/factor-lab`因子实验室、`/backtest`策略回测、`/history`超级因子任务页。
- 期货现有 `factor-lab/meta/search/backtest-factor/compare` 是**单品种时间序列公式**及同品种多公式仓位比较；它的ts_ic/5根/20根IC不是逐交易日跨品种截面ICIR和分组收益。
- 现有IC加权公式组合默认eval_from=None时用全样本估权，不能直接标成样本外有效组合研究；数据入口还有换月平滑/回退但没有完整不可变输入清单。

因此这不是取消配额或多导入股票数据就能补齐。**本轮未用链接页、空表格或股票API伪装期货研究完成，也未擅自隐藏原期货功能。** 原路由问题和缺少完整期货研究切片仍明确待办。

只读独立调查涉及 `frontend/src/components/factor-research/stock-research-entry.tsx`、`factor-research-workspace.tsx`、`app/(main)/factor-lab/page.tsx`、`backtest/page.tsx`、`history/page.tsx`、`backend/app/api/factor_lab/{search_api,backtest_api,common}.py`、`scoring/{evaluate,portfolio}.py`。

## 4. 实际修改文件

- `backend/app/services/quota/quota_svc.py`
- `backend/app/services/system_settings_svc.py`
- `backend/app/services/stock_research_jobs.py`
- `backend/app/services/kline_history.py`
- `backend/app/schemas/admin.py`
- `backend/app/api/backtest.py`、`backend/app/api/factor_lab/search_api.py`、`llm_api.py`、`backtest_api.py`：仅清除已过时的日配额注释/说明，没有改计算或接口签名。
- `frontend/src/app/admin/settings/page.tsx`
- `frontend/src/components/admin/user-quota-card.tsx`
- 新 `backend/tests/test_unlimited_usage_policy.py`、`test_mining_symbol_market_integrity.py`
- 新 `frontend/src/components/admin/usage-policy.test.mjs`
- 新 `scripts/test-stock-completion-batch-05-postgres.py`；B02 harness仅report_label白名单加b05。
- 本计划、交付记录及主计划最新检查点。没有生产DDL/数据迁移、新依赖、提交或建分支。

## 5. 验证与真实失败

### 红绿

- quota初始7 failed/3 passed，`logs/stock-completion-b05-quota-red.log`；修后与admin schema19通过。
- 新A股累计门槛反例1 failed/10 passed，`logs/stock-completion-b05-lifetime-red.log`；去掉累计计数后相关service/权限/并发与候选完整性合计20通过，`logs/stock-completion-b05-quota-green.log`。
- 管理端2 red→2 green，真实组件事件保存载荷不含日限额，`logs/stock-completion-b05-quota-node-{red,green}.log`。
- 错标签反例1 red→原7挖掘用例及新1用例全部通过，`logs/stock-completion-b05-symbol-{red,green}.log`；原5行夹具保留不改。
- 独立只读审查发现累计1000任务限制和“按金额模型预算”文案不准确，均已反例修复/文案纠正；未借取消配额删除并发保护。

### 真实PG/Redis/HTTP

命令 `backend/.venv/Scripts/python.exe -X utf8 scripts/test-stock-completion-batch-05-postgres.py`。

- 最新 `logs/stock-completion-b05-pg-20261019-111328.json`：**64检查全通过**；PG随机私有schema清理true，两个仅属于新QA用户的Redis旧日计数键清理，公共业务写/上游/付费模型调用0。
- 私有旧全局/用户限额1、真实Redis计数1,000,000；实际JWT→ASGI→数据查询PG→真实期货策略回测内核与因子公式计算各连续3次成功。仅行情适配为私有合成PG行，未mock资金/指标内核或权限。不是供应商真行情或策略认证。
- 匿名401、非法参数422、普通用户改管理策略403；旧管理员正数payload不能恢复日限额；A股第1001份研究创建成功，第二个活动任务仍429；错标签股票被排除且原数据保留。原账户余额/订单/流水不变。
- 首次脚本AttributeError（B02 app没有redis属性）及第二次KeyError（误以为ai_calls在顶层，实际在config）各自失败JSON保留，均已清理schema/已建QA键。修脚本契约，不修改产品去配合测试。首轮无Redis连接或key建立。

### 全回归与实际运行

- `backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests -q --junitxml=logs/stock-completion-b05-backend.xml`：**2464 passed、3既有skipped、624 warnings，240.22秒，退出0**，`logs/stock-completion-b05-backend.log`。既有skip没有删除或改动，警告不等于生产验收通过。
- 前端目录 `node --import ../logs/node-alias-register.mjs --test (Get-ChildItem -Recurse src -Filter *.test.mjs | ForEach-Object FullName)`：**408 passed、0 failed/skip**，`logs/stock-completion-b05-node.log`。
- 前端目录 `node node_modules/typescript/bin/tsc --noEmit --incremental false`：退出0，`logs/stock-completion-b05-tsc.log`。
- 两项调查均已独立只读复核；候选过滤独立复跑8项通过，确认未改原数据。原GP7通过不证明逐tick正确，专门反例已显示缺陷，不把它算修复通过。
- 重载前实际READ ONLY统计：AI交易只有1 paused，主播/超级因子0，股票研究均终态，无running/pending。仅停止核实身份的本项目backend进程，再执行 `./scripts/run-local.ps1 -TimeoutSeconds 60`，退出0；没有强杀其他服务或改库。`logs/stock-completion-b05-before-reload.log`、`reload.log`。
- 最新前后端HTTP200，OpenAPI的deprecated日配额schema出现，证明新后端已加载；保留上一批只读启动门。管理员真实浏览器保存（没有当前管理员会话）、新的完整构建/生产灰度和全站人工验收本轮未执行。
- 最终检查点（2026-10-19约19:20）：本地backend PID18900，启动器清单已更新；业务次数不限策略已在当前实例加载。只完成本批配额垂直切片、候选止损和两项原因调查，GP连续演化及完整期货研究仍待实施。

## 6. 金融口径、迁移、成本、回退

- 不改行情、复权、资金、因子公式或成本方法；只改使用策略与候选证券代码域。无数据版本迁移，不删除旧配置或Redis审计记录。
- 用户“次数不限”不是上游/模型没有收费，也不是无止境GP、无限重试或无限数据下载。单次token/调用/时间与质量门保持；期货模型路径尚未证明按金额费用预算，不能虚称已有。
- 撤回本批策略会让旧正数日配额和累计1000门槛重新生效；正式回退必须明确此行为并保留不恢复旧业务限制的前向修复。数据库旧列保留，不需要回滚表结构。
- 报告继续增长需要独立的容量监控、用户可控归档及游标历史，不可用永久累计次数拒绝替代存储治理，也不可静默删除用户结果。
- 本轮隔离验证0付费/上游调用；运行实例原有新闻/行情/后台任务不计入该零调用声明，没有主动创建付费模型或挖掘任务。
- 原始错标签数据仍未迁移；GP种群/输入快照/盘权限仍不足，期货完整研究仍缺实现。系统整体部分完成，续做自动化不停止。

## 人工路径与下一轮五包

1. 管理端全局/用户设置应显示使用次数不限，不再有因子/回测日次数输入；任务资源配置仍在。
2. 用户多次运行合法因子/回测不应出现“今日次数上限”；数据不足、输入错误、活动队列满仍明确拒绝，不是假成功。
3. 超级因子日线候选本机现在为空，应先解决真实期货数据来源，而不是选择错标签股票；不要用增加代数修算法控制流。

后续五个可验收包：①脏标签逐源/hash/重复对账、受控期货数据覆盖；②版本化种群/RNG/best_seen/checkpoint与固定数据快照；③单代状态转换、原子认领/暂停取消/重启恢复一致性及market/mode权限；④前端区分休市等待、无合格因子和读取错误、取消假续训承诺；⑤期货研究独立方法与契约、可追溯换月/PIT、IC/分组引擎和异步持久化再接入口。原S04/P01/R02等主计划未因此完成或删除。
