# B14 AI模型精确选路真实交付

## 状态

本轮四包实施及本地/隔离验证通过；**未发布，整系统部分完成**。修复实际代码中的同名模型误走渠道、渠道停用/归属未检查，不证明用户提供的任何真实供应商已恢复。无供应商和付费模型调用，不改密钥，不增加业务次数限制。

读取B13交付、主计划和直接调用方后，建立`docs/superpowers/plans/2026-10-20-stock-completion-batch-14.md`再实施。当前其他执行者只有上一轮额度失败的只读审查者，未重复派发或绕额度。未覆盖其他修改、提交或建分支。

## 四包与实际文件

### B14-1 精确选路完整链路

根因：前端保存/选择的是本人模型行UUID，但SSE仅传model_id，`gateway.chat_stream`查询同名模型后`.first()`，同时没有约束渠道user_id/is_active。两个渠道同名时UI选择和实际付费渠道可能不一致；不代表已证实过去用户那次429就是此原因。

- `backend/app/api/ai_chat.py`：`ChatRequest`新增可选UUID `model_record_id`，经真实SSE生成器传到网关；非法UUID422，不将请求中的user/provider/盘当权限。新增需JWT且仅期货开放的GET `/api/ai/chat/capabilities`，typed literal `owned-model-row-v1`、no-store、request_id；股票仍409，不暗开期货Agent。
- `backend/app/services/ai/gateway.py`：model_id、模型owner、渠道owner、active必须同时成立，再按可选row UUID限定。查询limit2，0行`model_unavailable`、多行`model_ambiguous`；拒绝路径在decrypt/create_provider前结束。SSE error metadata沿既有格式返回，精确UUID不能绕过model_id/归属/停用。
- `frontend/src/lib/ai-stream.ts`、`frontend/src/stores/ai-chat.ts`：发送选中行UUID及原scope捕获凭据；先检查精确选路能力。能力404/不匹配/坏JSON/网络失败不发模型POST，显示固定安全错误。能力请求独立10秒上限并响应原abort；不会拿旧后端忽略新字段当成功，也不静默回退同名model_id。
- 旧字符串conversationId签名仍兼容；无row UUID的旧唯一模型路径继续工作。模型工厂拿到的model_id仍是供应商模型名称，不把内部UUID当上游模型名。

### B14-2 旧消费者和文本摘要

- 网关旧model_id路径仅允许**唯一启用的本人渠道**；两个启用同名模型必须拒绝，不能为兼容旧客户端而随意选第一个。只剩一个启用渠道时旧入口仍可用；未启用或跨owner渠道永不候选。
- 内部memory摘要沿用相同网关、`tools_enabled=False`，歧义时明确失败且不调用付费模型；未改摘要/记忆表或赋予其交易权限，也未伪造成功摘要。这不是“按对话保存具体模型行”的迁移，旧摘要在歧义时可能不能生成，后续需要可信会话模型版本绑定。
- `backend/tests/test_ai_chat_execution_safety.py`的三处结果容器从first改all，原自确认、禁用工具、summary无工具、跨盘/市场断言全部保留，没有放宽断言或新增skip。

### B14-3 配置撤销与UI状态

- `frontend/src/stores/ai-chat.ts`订阅现有settings：选中行被删除、已知渠道被停用，立即cancel旧流并使选择失效；迟到事件仍受B13守卫。select/restore拒绝已知停用渠道，不把同名其他行当原行。
- 渠道列表尚未加载时前端不猜其active状态，最终以服务端SQL为准；这不是绕过后端权限。取消已发出的真实上游请求不保证供应商退款，0调用的结论仅限拒绝发生于模型工厂之前/本批验证。
- `frontend/src/stores/ai-session.test.mjs`覆盖UUID传递/删除/停用；`frontend/src/stores/chat-response.test.mjs`覆盖真实stream函数能力门控、捕获凭据、旧签名、坏响应不泄露片段且不POST。未自动调用供应商测试接口，不绕TPM/RPM。

### B14-4 真实验证及检查点

- 新增`backend/tests/test_ai_model_routing.py`；新增`scripts/test-stock-completion-batch-14-postgres.py`，B02探针仅增允许报告名。
- 私有PG构造同名两个渠道、其他用户模型、模型owner与provider owner故意不一致的负面fixture；实际SQL/JWT/ASGI链路验证命中地址、歧义拒绝、停用拒绝、ID/名称不一致、他人UUID、非法UUID、能力401/股票409，以及无工具摘要歧义不调用。只有**provider执行边界**使用明示fixture，不向外网发送；不是供应商端到端认证。
- 网关脱敏日志记录request_id、用户、JWT盘（内部无盘摘要标not_supplied，不默认live）、方法版本、匹配行数及内部row UUID；不记录key、URL/密码、prompt或持仓。

## 实际命令与结果

仓库根目录：
```powershell
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_ai_model_routing.py -q
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_ai_model_routing.py backend/tests/test_ai_chat_execution_safety.py backend/tests/test_chat_provider_resilience.py backend/tests/test_ai_conversation_pagination.py -q
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests -q
backend/.venv/Scripts/python.exe -X utf8 scripts/test-stock-completion-batch-14-postgres.py
```
frontend目录：
```powershell
node --import ../logs/node-alias-register.mjs --test src/stores/ai-session.test.mjs src/stores/chat-response.test.mjs
node --import ../logs/node-alias-register.mjs --test (Get-ChildItem -Recurse src -Filter *.test.mjs | ForEach-Object FullName)
node node_modules/typescript/bin/tsc --noEmit --incremental false
```

- 新后端反例**4失败**：`logs/b14-routing-red.log`（Schema丢UUID、错误无code、多候选仍取first、gateway无新参数）；客户端反例**4失败/20通过**：`logs/b14-client-red.log`。实现后目标绿；首次相关回归有7个旧fixture容器缺all失败，修容器契约后**63通过**，`logs/b14-targeted-green.log`。
- 本人复查补坏能力响应反例：**1失败/9通过**，`logs/b14-capability-red.log`，原错误仅显示网络错误；修改后固定脱敏提示且不发POST。没有把POST后断线说成未收费。
- 完整后端**2614通过、3既有skip、703 warnings、exit0**，157.21秒：`logs/b14-backend-full.log`。此后仅网关新增脱敏选路日志，最终目标**63通过、exit0**及真实PG再复核：`logs/b14-targeted-confirmation.log`；没有声称完整集在这条新增日志后又跑了一遍。旧utcnow/异步mock告警未消除。
- 最终完整Node **470通过、0失败/skip、exit0**，TSC **exit0**：`logs/b14-node-final.log`、`logs/b14-tsc-final.log`。早期469不是最终确认数。
- 最终真实隔离PG **63检查通过、0失败、私有schema已清理**：`logs/stock-completion-b14-pg-20261020-123950.json`、`logs/b14-pg-confirmation.log`；公共模型/渠道行数、原账本/三个账户余额不变，源/付费模型/公共业务写0。初次PG同样通过，未无限重试。
- 已逐包本人复查。独立审查B13留下的额度阻塞尚未解除，本轮不再发起；**没有独立审查通过结论**。

## 兼容、口径、成本与回退

- 无DDL、ORM/数据迁移、新依赖、金融计算或复权/价量/回放口径改变；不触碰真实资金、订单或公开历史数据。业务次数不限与上游/单次预算保护不变。
- 新API字段可选，旧唯一model_id兼容；旧歧义请求从错误随机选路变为安全拒绝，需旧调用方升级明确配置行，不能将其视作可自动恢复的错误重试。
- 新前端依赖明确能力，部署顺序**先后端再前端**；旧后端无能力时新UI只提示不可调用，不fallback付费。此probe每次发送增加一次本机/应用HTTP读取，最多10秒、不触达供应商；本轮模型/来源费用0。
- 回退无需DB恢复，但不能重新启用first随机选路或忽略渠道归属/active。若回退新版前端，须保留后端歧义拒绝和所有S04/S05保护；若回退后端，先将聊天入口关闭/只读，不能让新旧客户端误走供应商。真实调用发出后的外部费用不能虚构可回滚。

## 未完成和人工路径

- 本轮未启动/重载整项目，前后端线上运行链路未应用验证。两次只读端口检查仍5433专用PG在线、8002/6380/8110无监听，3000非项目进程占用，未结束它。B13生产standalone构建EPERM仍未修；本轮没有重复该已知失败构建或绕浏览器安全限制。
- 实际供应商响应/限流/额度和完整对话没有测试，不能据此保证用户API均可用。股票Agent仍未开放；S04持久确认/S05服务端会话归属、历史记忆/RAG盘隔离、五年缺价/真实收盘更新/GP/发布及人工验收仍未完成。未改任何续做自动化。
- 数据结论仍仅引用B13的20:25只读记录，没有新增行情审计/抓取；没有将19日数据说成20日收盘，也没有补假价、删除样本或改source。

人工：先安全恢复本项目后端和未占用的项目页面→本人创建两个同名模型但不同渠道→选择具体行，浏览器网络应先GET capabilities再POST带model_record_id；停用对应渠道应清空选择/停止旧流，不能仍收到迟到图表修改。旧客户端只传model_id有歧义时应有明确错误且供应商调用0；真实供应商仅在用户确定单次预算后验收。此处是待执行步骤，不是已完成浏览器验证。

下一四包：①S05服务端会话市场/盘与legacy_unknown兼容迁移、可信模型行/版本绑定，禁止猜旧归属；②S04持久人工确认和同事务模拟写幂等；③D01/R03真实缺价及盘后更新证据/费用预估，不盲重导；④P01端口/专用服务、目标平台构建和完整迁移/回退、独立及本人浏览器验收。
