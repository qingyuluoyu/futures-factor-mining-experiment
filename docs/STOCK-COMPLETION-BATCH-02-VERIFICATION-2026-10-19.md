# A股整改第二批真实检查点（2026-10-19）

## 完成状态与续做交接

**部分完成，未发布，不能称系统完整可用。** 本批实施四个有独立测试的安全工作包；D05/S04/S05/S06父包均未整体完成。

- 12:10心跳开始，先读第一批记录、主计划和直接代码，新增第二批详细计划；没有覆盖第一批修改。
- 两个独立分工在12:21遭遇执行额度限制：股票查询分工已留下代码及50项green；HTTP分工只留下失败测试，未实现。15:30心跳恢复后主执行者重新核实文件、接手未完成实现，没有把代理错误当成功。
- 当前修改仍在同一工作目录，无提交、分支、新依赖、公共DDL或主后端重启。原后端是否已加载本批代码**未验证**；前端开发服务可能自动编译，不等于整批部署。
- 两项独立复查已完成：前端/股票查询无Important；后端复查发现摘要别名调用遗漏、流式研究禁用权限旁路两项Important，已先红后绿修复并二次复核通过。**独立代码复核不等于发布验收**。

## 四个工作包实际结果

### B02-1：股票查询有界且不吞日期错误

`backend/app/api/stock.py` 的估值接口严格校验ASCII六位代码、YYYY-MM-DD真实日历日、start≤end；空字符串保留旧“无边界”语义。新增limit/offset，默认最近3000条（最大3000），数据库按交易日倒序取窗口，返回窗口内部仍升序。财报默认40、最大200，按报告期倒序稳定分页。

两个接口保留数组及原字段；响应头提供页大小/偏移/更多/排序、请求编号、no-store和元数据。默认只读数据源仍是旧导入库，供应商/授权/采集时点/数据版本未知，明确 `legacy_unverified`；声明的单位取已有模型，不代表逐行来源或历史时点已认证。缺PE和披露日保持null。数据库异常503，不伪装空成功。

兼容边界：超过3000条的旧估值消费者需按固定end分页；已搜索仓库当前调用方，其原来只取最近值/片段，数组升序不变。D05还剩其他行情/列表契约，不标父包完成。

### B02-2：客户端认证刷新绑定原会话

新增 `frontend/src/lib/http-session.ts`，绑定user_id、sessionRevision及发起凭据；旧401不能刷新/覆盖/退出新登录，也不能把旧写请求重放到新用户。相同凭据的重新登录仍因revision改变拒绝旧响应。并发HTTP/WS共享一次刷新；迟到同会话401复用该轮已刷新凭据；成功体解析期间换号也丢弃旧结果。

刷新接口20秒有界，校验access/refresh字段类型和非空后才原子顺序提交客户端状态；普通原业务请求保留调用方原signal语义，**未加统一20秒超时破坏既有长回测**。刷新后DELETE204不再强制解析JSON。资料绑定API、市场切换队列、登录恢复契约保留。

S06服务端改密撤销/refresh轮换、明文记住密码清理尚未实现；市场变化不是用户会话变更，通用请求的market快照隔离仍归S05。

### B02-3：封堵聊天模型自确认，纸面账户读取按JWT盘

从 `get_auth_context` 创建冻结 `ExecutionContext(user_id, market_type, trading_mode, request_id)`，穿过真实SSE生成器、gateway、registry到paper dispatch。模拟持仓/委托/账户查询显式使用认证盘，参数伪造user/mode无效；缺上下文/错用户/股票范围拒绝。股票聊天入口409明确未开放。

**这是安全止血，不是完整聊天交易：** 当前写工具内部自行commit且没有服务器确认记录/幂等，继续接受模型 `confirmed=true` 会越过真人确认。因此五种聊天下单/平仓/撤单及代码沙箱全部临时关闭（含旧自由交易偏好），模型不再获发这些工具；即使模型强行调用也返回 `server_confirmation_required`，不显示假的待确认成功按钮。手工模拟交易页面、AI/量化任务原入口不改。

虚拟盘尚未适配的行情/多品种研究工具关闭，避免继续读live当virtual；本人纸面账户读取与图表设置保留。工具设置接口返回真实availability，前端显示原因并禁开关；旧“自由交易”不能绕过。S04持久化确认/参数hash/到期/幂等/账本同事务/前端结构化确认仍未做，不能声称AI聊天交易已恢复。

### B02-4：工具重试和数据库会话互斥

仅明确白名单且无共享数据库session的幂等读取允许超时重试一次；写、未知、图表修改和可能惰性开户的paper查询不自动重试。有同一个AsyncSession时顺序执行；无session纯读取仍允许并发。损坏JSON/数组/null/数字参数返回 `invalid_tool_arguments`，不进入executor；普通异常不回显可能含秘密的原始错误文本。

尚无统一总token/总时长/工具总数/成本审计，跨工具会话/知识记忆隔离也未完成，S05仍未完成。

## 实际修改文件

后端：

1. `backend/app/api/stock.py`
2. `backend/app/api/ai_chat.py`
3. `backend/app/api/ai_tools.py`
4. `backend/app/services/ai/execution_context.py`（新）
5. `backend/app/services/ai/gateway.py`
6. `backend/app/services/ai/tools/safety.py`（新）
7. `backend/app/services/ai/tools/registry.py`
8. `backend/app/services/ai/tools/loop.py`
9. `backend/app/services/ai/tools/paper/dispatch.py`
10. `backend/app/services/ai/tools/paper/exec.py`
11. `backend/app/services/ai/memory.py`（独立复查后的摘要调用兼容修复）

前端：`frontend/src/lib/api.ts`、新增 `frontend/src/lib/http-session.ts`、`frontend/src/components/ai-settings/tools-panel.tsx`、`frontend/src/components/ai-settings/tools/tool-row.tsx`。

测试/验证：新增 `backend/tests/test_stock_query_boundaries.py`、`backend/tests/test_ai_chat_execution_safety.py`、`backend/tests/test_ai_tool_execution_policy.py`、`frontend/src/lib/http-session.test.mjs`、`frontend/src/components/ai-settings/tools/tool-row.test.mjs`、`scripts/test-stock-completion-batch-02-postgres.py`。

文档：本记录、第二批子计划、主计划子进度说明。

## 实际命令与结果

仓库根目录执行，除注明前端目录外，Python均使用项目3.12环境：

1. `backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_ai_chat_execution_safety.py backend/tests/test_ai_tool_execution_policy.py -q`
   - 首轮red：42 failed、2 passed，`logs/stock-completion-b02-safety-red.log`。不是通过结果。
   - 后续增加实际SSE/gateway和工具设置反例，目标汇总：`backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_ai_chat_execution_safety.py backend/tests/test_ai_tool_execution_policy.py backend/tests/test_stock_query_boundaries.py -q`，**102 passed，11.67秒**，`logs/stock-completion-b02-target-green.log`。
2. 股票查询分工red/green：`logs/stock-completion-b02-1-red.log`、`logs/stock-completion-b02-1-green.log`，后者**50 passed**。主执行者随后完整套件重跑包含这50项，不重复累计。
3. 前端目录 `node --import ../logs/node-alias-register.mjs --test src/lib/api.test.mjs src/lib/http-session.test.mjs src/components/ai-settings/tools/tool-row.test.mjs`：**24 passed**；原HTTP/session和工具UI red保留 `logs/stock-completion-b02-http-session-red.log`、`logs/stock-completion-b02-tool-ui-red.log`。
4. `backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests -q --junitxml=logs/stock-completion-b02-backend.xml`：**2327 passed、3既有skipped、502 warnings、77.54秒**。日志 `logs/stock-completion-b02-backend.log`。未添加skip、未删测试或放宽旧金融断言。原R02队列偶发问题本次未出现，不宣称已修复。
5. 前端目录 `node --import ../logs/node-alias-register.mjs --test (Get-ChildItem -Recurse src -Filter *.test.mjs | ForEach-Object FullName)`：**358 passed、0 failed/skip**，`logs/stock-completion-b02-node.log`。
6. 前端目录 `node node_modules/typescript/bin/tsc --noEmit --incremental false`：退出0，`logs/stock-completion-b02-tsc.log`为空。
7. `backend/.venv/Scripts/python.exe -X utf8 scripts/test-stock-completion-batch-02-postgres.py`：首轮私有search_path配置下ProgrammingError，0业务检查、隔离schema已清理，保留 `logs/stock-completion-b02-pg-20261019-073758.json`；修验证脚本的public类型解析兼容后重跑：**42项实际PG/ASGI HTTP检查全部通过**，`logs/stock-completion-b02-pg-20261019-073841.json`，schema清理true。
   - 真实JWT、3个隔离用户、双盘不同余额/数量、真实纸面表/模型归属查询，经完整SSE/gateway到真实paper查询；业务参数伪造盘和用户不生效。
   - 六类模型自确认写均未新增订单/流水，三账户余额未变；股票聊天拒绝；其他用户模型拒绝；股票估值分页并集/空值/来源声明验证。
   - 供应商边界明确使用本地合成provider，不把mock当模型外部联调；这42项不证明真实行情来源或金融策略通过。
8. 原服务匿名只读HTTP：`/market`、`/ai-settings`200；估值无token401，记录 `logs/stock-completion-b02-live-readonly-20261019-074030.json`。初查`/api/health`404、8001未监听属检查地址错误，未改产品；读实际health路由和端口后检查 **8002/health=200**，live/virtual健康true，`logs/stock-completion-b02-live-health.json`。只证明进程/行情健康，不代表本批已部署或所有功能可用。

## 金融、数据、成本、迁移和回退

### 最终复查与重跑（15:46—15:50）

- 后端复查发现 `memory._generate_summary` 通过别名调用gateway未带新context；补无工具内部模式，摘要不获得默认live权限；遇provider伪造工具调用拒绝且脱敏记录失败。新增反例证明文本摘要仍真实经过gateway，交互模式无scope仍在数据库/模型前拒绝。
- 流式 `research_products` 原路径绕过registry的用户工具关闭检查；补执行前检查并交普通registry拒绝。原风险是未获允许的行情扫描及资源使用，**未证实额外模型收费**。
- 真实red：`backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_ai_chat_execution_safety.py -q`，2 failed、39 passed，`logs/stock-completion-b02-review-red.log`；修复后两目标54 passed，再补摘要注入/缺context/非超时不重试，独立二次复核两目标 **58 passed**。
- 最终全后端：`backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests -q --junitxml=logs/stock-completion-b02-backend-final.xml`，**2333 passed、3既有skipped、502 warnings、69.58秒**，`logs/stock-completion-b02-backend-final.log`。
- 最终全部Node同前述命令，**358 passed、0 failed/skip**，`logs/stock-completion-b02-node-final.log`。最终TSC同前述命令退出0，`logs/stock-completion-b02-tsc-final.log`。
- 最终PG同前述命令，**42 passed**，`logs/stock-completion-b02-pg-20261019-074613.json`，清理true。独立审查者仅静态核验报告，没有伪称再次运行PG。
- 最终改动较首批新增后端108项、前端16项，分别已包含在总数内，不能将目标/全套/复核数字相加制造更多通过。

- 无公式、费税、撮合、余额或回放数据变更；股票数据没有下载、补零或复权拼接；未认证旧历史来源。目录5558不等于5558完整历史。
- 无业务表结构变更或生产迁移；测试仅随机私有schema克隆结构和写明确测试样本。测试股票整数id显式指定，不使用公共表序列。
- 外部行情调用0、付费模型调用0；代理额度失败是开发执行额度，不应伪称用户数据渠道余额不足。顺序session可能降低同轮聊天工具并发，这是明确的正确性优先取舍。
- 返回字段兼容增加；估值默认上限和不可用聊天能力是明确的行为变更，发布必须说明。不得回退到模型自确认；服务器确认正式链路需独立007迁移、功能开关及同事务幂等后前向开放。
- 主后端仍有遗留启动DDL（P01），本批未重启。生产构建、浏览器人工验收、发布/回退演练、下一真实EOD收盘结果均未执行。

## 人工验收路径（部署安全路径验收后）

1. 同用户live/virtual分别查看聊天模拟持仓/委托/账户，确认对应各自数据；股票市场直接POST聊天应409。
2. 请求聊天下单/撤单，即使输入“已确认”也必须明确未执行，无新订单/流水；AI设置对应工具暂未开放，手工交易原入口保留。
3. A发起慢请求后退出登录B：旧响应不覆盖B、不刷新B、不跳出B；并发401只有一次刷新，DELETE204不报解析错。
4. 估值日期错误和倒序422；正常limit/offset分页及更多头一致；财报缺披露日期保持空，不能标历史时点认证。
5. 复核既有期货手工模拟下单/自定义资金、股票行情/研究/回放不受影响；不能用本批42项代替这些人工业务验收。

## 下一轮四个工作包

1. S04服务器持久化动作确认、规范化参数hash/到期/幂等，与paper业务账本同事务；前端结构化确认后才恢复聊天写。
2. S05剩余行情工具真实分盘、会话/知识记忆scope、预算和执行日志；不以一直禁用代替适配完成。
3. R02旧同步研究入口归一、原队列偶发测试确定性复现，随后R03因子依赖快照资源。
4. D01盘后增量缺口/恢复状态可见，区分目录/EOD/连续历史/回放范围；以真实收盘任务结果另验，不手工盲重导。
