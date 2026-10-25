# 完整整改第一批真实交付与续做检查点

检查点：2026-10-19 11:31，北京时间。主计划 `docs/superpowers/plans/2026-10-19-stock-product-completion-master.md`；详细执行计划 `docs/superpowers/plans/2026-10-19-stock-completion-batch-01.md`。

## 状态

**系统仍部分完成。本批4包已实现并通过本机代码、相关行为、真实隔离PG/ASGI HTTP验证与独立代码复查；不是生产发布/用户浏览器验收完成。**没有重启原服务或应用产品表迁移，没有主动调用Teajoin/付费模型，没有新建公共研究/交易任务或用户。原Next开发服务可读取当前前端源码，但原后台进程没有以本批新代码重新部署，不能宣称运行中所有后端已更新。

## 1. 核心实现与数据流

### S01 市场切换与登录交接

- store增加会话代次，login/logout/restore使旧队列失效；同会话市场写请求串行，UI保持最后意图。失败回滚最近已确认市场，只改市场字段，不恢复旧整用户。
- 请求实际出队时取同会话当前token，避免排队期间刷新后仍用旧token；市场API绑定明确token、20秒超时，无跨会话401自动重发。
- 新绑定登录助手：先清旧身份并递增代次，再按明确token读取本人；代次+store token+storage token一致才完成登录。独立登录页和首页弹窗均接入，移除直接setState(accessToken)的绕过路径。
- 顶栏旧会话不得触发新会话跳转；失败显示原因。通用request/tryRefreshToken陈旧401、跨标签/超时后偏好全局顺序及服务端token撤销尚未全面处理，列入S05/S06，不以本包冒充完整认证验收。

### S02 真实个人资料

- 新真实资料组件接GET本人和PUT更新，删除硬编码用户名/邮箱/ID/手机号/注册时间、假认证及无效保存。
- 用户名只读，phone/email可编辑；头像上传未接通明确禁用。原接口不支持清空已绑定字段，UI如实说明；模拟产品不要求实盘账户/KYC。
- 加载/重试/提交/冲突/取消/重复提交/退出换号/响应身份不符均处理。保存只合并个人字段，不覆盖market/mode；刷新token不重载并丢失草稿。
- 绑定资料API有超时、提交白名单和基本身份响应校验，不跨身份刷新重发写请求。
- 后端更新使用AuthContext返回真实trading_mode，先查唯一性再修改字段，并发冲突rollback→409；返回X-Request-ID/no-store、日志不记录联系字段。rollback前保存user_id，避免回滚后读过期对象触发MissingGreenlet。
- 没有单独新增profile-state模块：当前组件状态可完整表达且已行为测试，不为文件数拆散。没有修改资料schema或伪装用户名可写。

### R01 研究参数和报告

- MAD/3σ/none、Zscore、缺失处理、行业/市值中性化可操作，接原state、schema和analysis/backtest提交；分组可选5/10，保留旧任务合法2—10组。实读契约上限10，不是初步计划中的20。
- 运行期间参数及左侧因子选择禁改；报告按保存任务显示真实组数/预处理及参数JSON。缺少旧组数记录时显示未记录，不再写死5组。
- 不改金融公式、默认值、预检、数据和费用；自定义表达式、基准、五年资源仍是后续包。

### S03 AI任务分盘取价

- 主K线、附加周期、参考价、停止强平显式使用规范化task.trading_mode，保留原下单已有正确mode传递。
- 修复更深同类缺陷：virtual缺报价时不读取live新浪源快照；live原回落保留。无报价强平/估值等调用方策略本批未整体金融验收。
- 完整评估用例覆盖两盘、缺参考价回落同盘已收盘bar而不是末尾forming=99999、多周期和强平；真实K线reader用例覆盖双盘缓存路径并阻断所有外部fetch。
- reader用例只为隔离命名空间关闭新鲜度门，不是新鲜度认证。原virtual历史存在seed底图设计，本批未重写，不宣称所有virtual bar原始来源完全独立。

## 2. 实际修改文件

业务源码15个：

```text
frontend/src/stores/auth.ts
frontend/src/lib/api.ts
frontend/src/lib/login-session.ts
frontend/src/components/layout/top-navbar.tsx
frontend/src/app/(auth)/login/page.tsx
frontend/src/components/landing/auth/auth-login-form.tsx
frontend/src/app/(main)/profile/page.tsx
frontend/src/components/profile/personal-info-panel.tsx
frontend/src/lib/stock-research-api.ts
frontend/src/components/factor-research/research-preprocess-controls.tsx
frontend/src/components/factor-research/factor-research-workspace.tsx
frontend/src/components/factor-research/research-report.tsx
backend/app/api/users.py
backend/app/services/ai_trading/engine.py
backend/app/services/paper/price_band.py
```

测试及验证脚本10个：

```text
frontend/src/stores/auth.test.mjs
frontend/src/lib/api.test.mjs
frontend/src/lib/login-session.test.mjs
frontend/src/components/profile/personal-info-panel.test.mjs
frontend/src/components/factor-research/research-preprocess-controls.test.mjs
frontend/src/components/factor-research/research-report.test.mjs
frontend/src/components/factor-research/factor-research-page.test.mjs
backend/tests/test_user_profile_context.py
backend/tests/test_ai_trading_mode_isolation.py
scripts/test-stock-completion-batch-01-postgres.py
```

另有主/子计划、本交付及原扩展计划检查点；日志/XML/JSON在logs。无新依赖、提交或分支。

## 3. 实际命令、通过与失败

命令在仓库根运行，frontend另注工作目录。目标集与全量重叠，不累加夸大。

| 实际命令 | 结果 |
|---|---|
| `node --experimental-strip-types --test frontend/src/stores/auth.test.mjs frontend/src/lib/api.test.mjs` | 首轮业务red：9失败/5通过；修后14通过。初版测试等待旧代码未完成Promise曾挂起，修等待方式后重跑，不把挂起当业务red。只停止核对过命令行的本次测试node，不停止应用 |
| `node --test frontend/src/components/profile/personal-info-panel.test.mjs` | 原页面5/5反例失败；实现后5通过，新增刷新草稿用例后6通过 |
| `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_user_profile_context.py -q` | 原代码virtual返回live失败；修后通过；补trace先失败再通过 |
| `node --experimental-strip-types --test frontend/src/components/factor-research/research-preprocess-controls.test.mjs frontend/src/components/factor-research/research-report.test.mjs frontend/src/components/factor-research/factor-research-page.test.mjs` | red 4失败/18通过 → green 22通过 |
| `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_ai_trading_mode_isolation.py -q` | 原强平mode断言失败；主评估fixture缺equity_baseline已修正。virtual源回落先红后绿；最终该文件8通过 |
| Node实际TS转译/VM：store、绑定登录、API、资料目标集 | 独立复查27/27通过，包含排队刷新token原反例red→green和4个绑定登录用例 |
| `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_ai_trading_mode_isolation.py backend/tests/test_user_profile_context.py -q` | 最终9通过；独立复查再次9通过 |
| PowerShell here-string管道虚拟环境Python：内存AST删除evaluate_task取价mode，再用pytest运行`-k 'task_primary and virtual'` | 预期2项虚拟盘反例失败，证明测试能拒绝旧模式遗漏；没有改磁盘源码，进程退出即丢弃变异 |
| `backend/.venv/Scripts/python.exe -X utf8 scripts/test-stock-completion-batch-01-postgres.py` | 最终真实私有PG/ASGI HTTP **20/20通过**；`stock-completion-b01-pg-20261019-031720.json`，schema清理成功 |
| `backend/.venv/Scripts/python.exe -m pytest backend/tests -q --junitxml=logs/stock-completion-b01-backend.xml` | 首轮 **2221通过、1失败、3既有skip、505warnings**，完整保留full-backend日志 |
| `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_stock_research_job_service.py -q` | 旧队列文件复核8通过，没有修改该文件 |
| `backend/.venv/Scripts/python.exe -m pytest backend/tests -q --junitxml=logs/stock-completion-b01-backend-recheck.xml` | 最终新增reader/缺价用例后 **2225通过、3既有skip、505warnings，69.34s**；保留首轮失败，不宣称偶发性根治 |
| frontend中 `node --import ../logs/node-alias-register.mjs --test (Get-ChildItem -Recurse src -Filter *.test.mjs \| ForEach-Object FullName)` | 最终 **342通过，0失败/skip**；`stock-completion-b01-full-node-final.log` |
| frontend中 `node node_modules/typescript/bin/tsc --noEmit --incremental false` | 最终exit0；`stock-completion-b01-tsc-final.log`无错误输出 |
| `backend/.venv/Scripts/python.exe -X utf8 scripts/test-stock-pages-http-local.py` | 原服务真实网络HTTP **22/22通过**；`stock-pages-http-20261019-032721.json`。只读页面/数据/报告/权限回归，不是新后端在线部署证明 |
| `backend/.venv/Scripts/python.exe -X utf8 scripts/audit-product-readiness-local.py` | 11:27只读聚合与health通过；`full-code-audit-snapshot-20261019-032719.json` |

### 真实失败修复及审查

1. 资料UI初次green的submit包装返回void，测试无法等待保存；改直接绑定async submit，保持真实交互语义，未删除断言。
2. PG隔离断言初版用regclass文本前缀，search_path可省略schema导致误报；改查tableoid对应pg_namespace。旧失败报告031133保留，修后18项通过。
3. 加并发唯一冲突后真PG确认MissingGreenlet：rollback后日志读取过期对象。保存user_id再rollback修后20项通过；031645/031703失败报告保留。
4. 独立前端审查确认排队token捕获过早Important，补反例并修队列/两个登录入口；复查无剩余已确认Important。后端独立复查补真实reader与闭合bar用例后同样无剩余Important，仅限本批范围。
5. 首轮全量旧用例 `test_cancelled_running_job_keeps_compute_slot_until_actual_process_stop_is_acknowledged` 在owner服务取消首次领取任务时404。它假定先领取owner，而队列按created_at/UUID排序；时间戳并列是待固定复现的解释。单文件8通过、最终全量通过，**不宣称偶发性已根治**；R02负责核实归属/排序假设。没有去掉权限检查、放宽断言、改时间精度或无限重跑。
6. 曾查找错误skill路径、组件名、PowerShell glob，后按实际文件定位；属于工具查找失败，不是业务red。

## 4. 金融、数据、成本、兼容和回退

- 研究公式、税费、复权、证券范围和真实行情不变。R01把已有参数接UI；S03修任务使用哪一盘数据。不认证策略有效或公司行动/强平规则。
- 11:27只读确认：目录5558/110、Oct16盘后5548/5558/缺10/未匹配19；旧历史585392行/300只；回放3只；影子归档仅1日。五年2302800面板格超2000000、缺8931价仍在。没有写未收盘日线或认证旧历史。
- 原health200、futures live/virtual布尔均true，不等于全部金融链路通过。国际金银未补造规格、未开放交易。
- 主动Teajoin/模型调用0；PG仅新私有schema写入/清理，公共业务写0。测试价量明确为隔离fixture，不进入公共库或用户页面。原运行服务定时/WS未停，不推断整机无外部流量。
- 无产品表变更/迁移、清库或重导，股票001—006迁移不变。资料响应纠正mode元数据、字段兼容；新市场/资料请求过期明确失败，不跨会话重发。
- 代码回退需逐文件反向应用修改或前向修复，没有演练完整代码回退，不声称有精确改前源码包。无数据回滚需求，不整库恢复或删有效报告。新参数沿既有schema。完整发布/回退仍P03待验。

## 5. 剩余阻断、人工路径和下一批

未完成：服务端AI确认、聊天执行上下文/写幂等、通用HTTP/refresh会话竞争、改密撤销/刷新轮换、旧明文记住密码、旧研究旁路/五年容量、历史版本/PIT/覆盖、DSL/基准、股票AI/收藏/消息、期货统一估值/无价强平、启动DDL和正式发布。

未执行本批新浏览器E2E、原后端受控部署、完整生产构建、真实上游/模型、五年性能/下一真实收盘验收。前轮截图、本轮网络HTTP不能当本轮浏览器通过。

未盲重启原后端的原因：审查F07确认启动路径仍含遗留DDL/回填，尚未完成P01版本化和发布回退演练；本轮用隔离ASGI/真实PG验证源码，而不是为让新代码在线立即生效而重复执行这些生产结构操作。正式切换需继续处理P01/P03，并先核对正在运行的用户任务。

人工路径：独立QA登录两入口→快速切市场/退出换号；个人资料核对真实身份→改邮箱→保存→刷新→取消/冲突，市场/模式不变；A股研究改预处理/5或10组→真实可用短窗提交→历史复开，确认显示保存参数、缺价仍拒绝。AI模式仅隔离fixture核验，不启动真实用户任务。

下一批先读实际代码形成详细子计划，再执行五包：S04服务端确认/一次消费；S05工具上下文/重试及陈旧HTTP；S06撤销/刷新/记住账号不存明文密码；R02旧研究归一与偶发队列测试契约；D01真实收盘增量/缺口和归档恢复可见性。受阻先做安全可执行包，不无限调用。

续做工具create并view确认：`a-15-30`、`a-20-33`均ACTIVE，绑定本对话，每日北京时间15:30、20:33；旧02:05/07:10未改。只有主计划最终验收通过才停止，当前不停止、不标系统完成。
