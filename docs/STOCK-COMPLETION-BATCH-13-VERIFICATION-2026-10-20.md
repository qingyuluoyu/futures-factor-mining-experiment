# B13 AI客户端隔离与历史修复真实检查点

## 状态和边界

本批四包已实施并完成目标测试和隔离PG/JWT/HTTP验证；全系统仍**部分完成**。不是股票Agent开放、持久人工确认或服务端历史会话分盘完成。生产standalone构建失败，本机完整服务/本人浏览器未验收，独立审查因额度不可用未完成。不得从测试数或健康页推导整个产品可发布。

当前子计划：`docs/superpowers/plans/2026-10-20-stock-completion-batch-13.md`。无Git提交/分支、无依赖升级、无供应商/付费模型调用、无公共账户/订单/研究/行情写入。

## 四包修改

### B13-1 模型、渠道及选择缓存

- 新增`frontend/src/lib/ai-client-scope.ts`：从匹配本人sub的JWT读取live/virtual，结合用户/市场/登录revision；恢复中、未加载、市场确认中、无身份或JWT错用户均无有效scope。同身份正常token轮换不清空正在接收的回答。此处只是客户端隔离，不代替服务端验签/权限。
- `frontend/src/stores/ai-settings.ts`：认证订阅同步清空providers/models/testResults；每类读取有最后发起者代数，旧finally不关闭新loading；写操作await后复核scope及epoch才更新/链式刷新。市场离开后再回来也不能让原写结果复活；没有自动测试付费渠道。
- `frontend/src/stores/ai-chat.ts`：模型偏好按用户/市场/JWT盘存本人的模型行UUID；只从当前模型缓存恢复。旧全局`ai:selected-model`删除不读取、不猜归属迁移。

### B13-2 聊天、迟到SSE与上传

- `frontend/src/stores/ai-chat.ts`：创建/切对话最后意图胜、即时清空旧消息并abort；取消在create返回前同样生效；退出/换号/市场确认/换盘使请求代数失效并清空图片、私聊、列表和模型。删除、分页及晚到error/done不能修改新scope。
- 所有message/thinking/tool/indicator/confirmation/agent/error/done通过统一守卫，绑定捕获scope、chatRevision、原controller及abort状态；旧指标工具不能修改新图表。列表刷新失败显式显示过期风险，不吞成空成功。
- `frontend/src/lib/ai-stream.ts`：增加可选captured accessToken；旧字符串conversationId签名仍兼容。store使用发起时凭据，不在异步创建结束后重新取另一账号的localStorage凭据。
- `frontend/src/components/market/ai-chat-panel.tsx`：身份scope及epoch变化重取模型/历史、清空未发送输入；FileReader和上传完成回调不能把旧图片/结果带入新会话。上传状态本地显示，不再自动付费发送上传成功/失败文本给模型。
- 未改变真实用户API配置，未重试或绕过供应商TPM/RPM。股票聊天仍保留服务端409，不能复用期货Agent伪装股票已接通。

### B13-3 历史契约、分页及假确认

- `backend/app/api/ai_conversation.py`：本人过滤不变；列表limit默认50、1—100，offset0—10000，保留原数组响应；updated_at/id稳定倒序。消息以created_at/id稳定正序，正确end/start窗口，75条的两页分别50/25且第三页空，不再重叠。
- 复用`FinancialRequestRoute`提供脱敏request_id/状态/耗时；含数据的成功响应no-store。401、他人404、非法参数422真实验证。
- 调查又发现实际保存的是工具记录**列表**，`MessageItem`却只接受dict，导致带工具历史500。兼容扩展`backend/app/schemas/ai_conversation.py`、`backend/app/models/ai_conversation.py`、`save_message`注解、`frontend/src/types/index.ts`为list/dict/null；列仍是既有JSONB，**无DDL变化、不需要结构迁移**，不重写旧消息。
- 共用`historyBubble`恢复新/旧页的工具记录，旧不支持的对象显式提示、保留正文、不崩溃且不恢复执行。`frontend/src/lib/api.ts`增加可选分页参数，`frontend/src/components/ai/chat-history.tsx`增加有界加载更多/ID去重及失败提示。
- `frontend/src/components/ai/message-renderer.tsx`明确逐笔确认未开放，转手动模拟交易；旧`resolveConfirmation`不再发`[action:confirm_tool]`给模型，不创建新收费请求、不宣称执行。服务端模型自确认和敏感工具关闭保护未解除。

### B13-4 验证、复查与交付

- 新增`frontend/src/stores/ai-session.test.mjs`、`backend/tests/test_ai_conversation_pagination.py`；扩展`frontend/src/stores/chat-response.test.mjs`的真实store依赖和SSE凭据测试，不删除/放松原断言。
- 新增`scripts/test-stock-completion-batch-13-postgres.py`；B02探针仅扩允许报告名。私有schema克隆表、真实PG/JWT/ASGI HTTP、FK级联和归属，不向公共会话插测试数据。B02既有模型是明确的工具边界fixture，不是实际供应商认证。
- 已派发只读独立审查，但审查者返回usage limit，未取得审查结论；未反复请求或另绕额度。本人复查发现市场往返写/删除晚到及工具历史Schema错误，均补失败反例后修复。不能写成独立审查通过。

## 真实命令和结果

仓库根目录：
```powershell
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_ai_conversation_pagination.py -q
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests -q
backend/.venv/Scripts/python.exe -X utf8 scripts/test-stock-completion-batch-13-postgres.py
backend/.venv/Scripts/python.exe -X utf8 scripts/audit-product-readiness-local.py
```

frontend目录：
```powershell
node --import ../logs/node-alias-register.mjs --test src/stores/ai-session.test.mjs src/stores/chat-response.test.mjs
node --import ../logs/node-alias-register.mjs --test (Get-ChildItem -Recurse src -Filter *.test.mjs | ForEach-Object FullName)
node node_modules/typescript/bin/tsc --noEmit --incremental false
$env:BACKEND_URL='http://127.0.0.1:8002'; node node_modules/next/dist/bin/next build
```

- 初始客户端7失败`logs/b13-session-red.log`→7通过；加入历史分页1失败`logs/b13-history-red.log`；市场确认1失败`logs/b13-pending-market-red.log`、往返删除/工具历史2失败`logs/b13-history-race-red.log`均已补修。初始后端分页8失败`logs/b13-pagination-red.log`；工具列表Schema反例1失败/9通过`logs/b13-tool-history-red.log`→目标10通过`logs/b13-history-green.log`。
- 完整Node最终**466通过、0失败/skip、exit0**：`logs/b13-node-final.log`。TSC最终**exit0**，见`logs/b13-tsc-final-confirmation.log`，无关闭类型校验。
- 完整后端最终**2610通过、3既有skip、703warnings、exit0**，178.91秒，见`logs/b13-backend-confirmation.log`；较早完整2608通过/3既有skip/703warnings在`logs/b13-backend-full.log`，不是最终Schema修复后的确认次数。既有skip和utcnow/异步mock告警未消除。
- 真实隔离PG最终**65检查通过，0失败，schema已清理**：`logs/stock-completion-b13-pg-20261020-122452.json`、`logs/b13-pg-confirmation.log`。覆盖75消息/同时间戳/50+25+0、列表边界/并集、工具数组JSONB→HTTP、本人删除及私有FK级联、匿名401、其他用户404、模型自确认拒绝/两盘账户不串及账本余额不变。不是完整浏览器或真实模型端到端。
- 前两次PG探针**失败ConnectionRefusedError，0检查**，分别`logs/stock-completion-b13-pg-20261020-075252.json`和`logs/stock-completion-b13-pg-20261020-121928.json`，未创建schema、没有伪称通过。按既有固定目录恢复专用PG5433后才真实通过；没有删除pid/WAL或手动改表。PG重启日志显示中断后自动恢复，不猜测中断原因。
- Next生产构建**exit1**：编译、类型检查和40页生成通过，但standalone文件追踪symlink **EPERM**；`logs/b13-build.log`。没有删除缓存/关检查/升级依赖或把前面编译成功说成可发布包。

## 数据、兼容、成本与回退

- 无金融公式、价格/成交量、复权、财报/回测方法或交易/费用规则改动；无数据导入，不以同源样本一致证明历史真实。使用次数不限的既有规则不变，仍保留权限/并发/单次预算及供应商限流。
- API添加有界参数但返回数组/MessagePage形状保持，tool字段兼容扩大而非删除旧对象；旧对象不猜为新格式。私有schema测试不是公共迁移；公共数据库结构完全未改，既有JSONB列表保留原样。
- 回退不需DB回滚，但**不得恢复全局模型缓存、文本确认或旧dict-only读取错误**。若需代码回退，应保留隔离/确认关闭及JSONB兼容修复，旧消息只读保留；恢复前等待活动会话/任务结束或由用户明确取消。scope变化清空未发草稿/图片是有意安全行为，不把旧未知归属内容迁回。
- 本批上游0、付费模型0、公共业务写0；消耗仅本地测试/构建CPU/内存。独立审查额度失败不等同用户配置的金融模型API额度失败。

## 当前服务与未完成风险

- 2026-10-20 **20:25上海时间**只读快照`logs/full-code-audit-snapshot-20261020-122551.json`：目录5558/110行业，历史300股585392行仍至16日；最新EOD仍19日5545/5558、缺13/未知19，回放3股，影子11093观测/5549股/2日。本次未补抓20日，不证明今天行情已更新；五年8931缺价及600221/2022-09-29持仓估值阻断仍未解决。
- 该审计整体**失败ConnectError**在8002 health；只读SQL部分已取得，不将其整体标成功。现场5433恢复，但8002/6380/8110没有监听；3000为非本项目进程占用，未结束它、未冒用3000页面作本项目验收；`logs/local-services.json`是旧状态，不作为当前在线证明。此轮没有启动/重载完整业务服务。
- 后端会话表仍仅user归属，没有可信市场/JWT盘字段；用户主动打开自己的旧对话仍可能来自另一盘。客户端不自动带入不等于服务端分盘完成，S05保持未完成。模型流仍按model_id解析，同名模型多渠道精确row路由需后续契约包。
- S04持久人工确认/一次性执行状态机/交易幂等、GP可信输入及开放、版本历史/五年完整回测和基准、发布迁移/回退、独立审查和本人浏览器均未完成。本批没有关闭任何续做自动化。

## 人工验收及下一四包

先确认端口归属并恢复本项目服务（当前3000不能当项目页），本人登录后：①添加自己的模型/打开聊天；退出或切市场确认中旧消息/图片/模型应即时清空；回原市场不接收旧流，正常token轮换不清回答。②快速切两条历史/取消刚创建会话，后返回者不盖新意图。③75条历史向前加载得到50+25，无重复，带工具记录可读，格式异常有明确提示。④旧确认卡仅手动入口，不新建模型或交易请求。真实模型测试必须单次预算明确，不能在本批0调用结果上声称已验收。

下一四包：
1. **S05服务端会话/PIT上下文隔离：** 用版本迁移新增会话市场/盘、可信新会话写入和owner+scope校验；旧会话标legacy_unknown、不猜归属、不自动入新上下文，记忆/RAG同边界并真实PG迁移/回退验证。
2. **S04持久人工确认：** Schema草稿→服务端pending/confirm/execute/expired状态机，资源归属/单次令牌/幂等/审计，同事务模拟账本；用户独立确认后才可开放，模型confirmed字段永不授权。
3. **D01/R03缺口核验：** 600221缺价及20日EOD未更新原因做有界只读分类/来源授权与费用预估；不补价、不删样本、不盲重导，取得同基准证据后再推进研究版本和五年结果。
4. **P01运行发布：** 端口占用与专用服务持久运行、Windows standalone权限/目标平台构建、完整版本迁移与回退、独立复查及人工浏览器验收；不得抢占非项目进程或用HTTP200代替验收。
