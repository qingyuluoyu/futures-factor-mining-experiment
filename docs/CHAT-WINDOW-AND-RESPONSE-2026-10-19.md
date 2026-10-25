# 聊天窗口与无响应修复检查点（2026-10-19 19:45）

## 状态与边界

三个工作包的代码与隔离链路已实现并验证，本地服务已安全重载；用户登录后的实际视觉和真实模型回答尚未验证。全系统仍部分完成。

截图中 `RateLimitExceeded.EndpointTPMExceeded`、`inference exceeds tpm/rpm limit` 是供应商速率限制证据，不是本项目因子/回测业务日次数限制。有效密钥、有余额和某模型可接受的每分钟token/请求数是不同条件；不能靠删除本地业务配额绕过供应商限流，也不能声称当前限流已解除。

## C01 窗口与消息可读性

- 默认由400×560改为640×640，最大宽1120；新增标题栏“宽屏阅读/恢复窗口宽度”，已有用户保存尺寸不强制覆盖。
- 320px视口等小窗口尺寸以视口为优先约束，不再因320最小宽度挤出屏幕；视口变化同时调整大小和位置，整个窗口/按钮保持可见。
- 面板、模型选择器、消息及输入框补min-width/min-height约束；长英文/错误换行，表格独立横向滚动，不把气泡撑出右侧。
- 保留原拖动与右下角调整大小。现有用户仍看到旧宽度时可点标题栏宽屏按钮，不清除聊天或浏览器全部设置。

## C02 上游失败与输出预算

- OpenAI兼容Provider统一将HTTP认证/权限、402/insufficient_quota、429/TPM/RPM、400和不可用转换为安全提示及标准错误码；支持整数Retry-After提示，不向界面透传任意上游原文。
- 超时/连接失败明确提示，均不自动重试或切源。流中error不再被忽略；usage-only空choices不会抛IndexError；支持紧凑`data:`；空流或无效JSON返回错误而非假成功。
- 每次OpenAI请求显式`max_tokens=4096`，是输出token上限，不是业务使用次数限制，也不是按金额预算或保证不触发供应商未知TPM。没有改密钥、账号额度或财务参数。
- API错误事件兼容保留message并增加error_code/upstream_status/retry_after_seconds白名单元数据；一次error后明确done并结束，不再继续工具/长期记忆处理。

## C03 最新消息与失败反馈

- 原前端从后往前凑成功轮次，可能漏掉本次用户消息；改为最近20个用户轮次，保留最新问题，排除流中/取消/失败助手输出。
- 原后端DB历史最后一条是user时不追加新问题，上一轮供应商失败后再提问会丢失；现在历史读取发生在保存本次输入之前，始终追加本次最后user原始内容，保留多模态输入。
- 创建对话失败显示安全错误并解除busy；创建期间不重复发送。部分回答后的失败单独显示alert，不再被已有正文掩盖，也不把部分失败结果混入后续前端上下文。
- SSE支持CRLF包分隔，首次error/done后终止并取消reader，完成回调只执行一次。

## 实际文件

- `backend/app/services/ai/providers/openai_provider.py`
- `backend/app/api/ai_chat.py`
- `frontend/src/stores/ai-float.ts`、`frontend/src/stores/ai-chat.ts`
- `frontend/src/components/ai/floating-assistant.tsx`、`message-renderer.tsx`、`model-selector.tsx`
- `frontend/src/components/market/ai-chat-panel.tsx`、`frontend/src/types/index.ts`
- 新 `backend/tests/test_chat_provider_resilience.py`、`frontend/src/stores/chat-response.test.mjs`
- 新 `scripts/test-chat-response-postgres.py`、`scripts/probe-configured-chat.py`；B02隔离harness仅report_label白名单扩展chat。
- 子计划 `docs/superpowers/plans/2026-10-19-chat-window-and-response.md`、本记录、主计划检查点。无迁移、无新依赖、无提交或新分支。

## 命令及真实结果

1. `backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_chat_provider_resilience.py -q`：初始5 failed（`logs/chat-response-red.log`）→初始修复5 passed；再补网络超时用例。前端初始默认宽度/最新问题2 red，追加部分输出/创建失败/CRLF终止反例也已红绿；曾缺AbortController和ESM图表加载导致夹具错误，修测试环境后再验证，没有把环境错误当业务red。
2. `backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_chat_provider_resilience.py backend/tests/test_ai_chat_execution_safety.py backend/tests/test_ai_provider_thinking.py backend/tests/test_provider_urls.py backend/tests/test_ai_tool_execution_policy.py -q`：**85 passed**，`logs/chat-response-regression.log`。
3. 前端目录 `node --import ../logs/node-alias-register.mjs --test (Get-ChildItem -Recurse src -Filter *.test.mjs | ForEach-Object FullName)`：**415 passed，0 failed/skip**，`logs/chat-response-node.log`；新聊天store/消息组件7项通过。
4. 前端目录 `node node_modules/typescript/bin/tsc --noEmit --incremental false`：**退出0**，`logs/chat-response-tsc.log`。
5. `backend/.venv/Scripts/python.exe -X utf8 scripts/test-chat-response-postgres.py`：**55检查通过，私有schema清理true**，`logs/stock-completion-chat-pg-20261019-113705.json`。真实JWT/权限→ASGI→真实上下文/PG持久化→真实gateway/Provider→HTTP传输夹具，429一次结束、再次提问成功、最新输入恰好一次、401/他人404、未写订单流水、账户原样。不mock身份/DB/模型解析/权限；外部模型传输为明确夹具，不能称真实供应商连通认证。公共业务写/上游/付费调用0。
6. `backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests -q --junitxml=logs/chat-response-backend.xml`：**1 failed、2469 passed、3既有skipped、625 warnings，220.84秒**。失败为既有`test_cancelled_running_job_keeps_compute_slot_until_actual_process_stop_is_acknowledged`：认领到任务后用owner服务取消，owner查询404。该测试假定首个被认领任务属owner，生产查询有created_at/id排序；目前不能仅凭日志断言完整因果。未改该测试/排序/权限。本轮未修改研究服务，单独 `backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_stock_research_job_service.py -q` **8 passed**（`logs/chat-response-unrelated-rerun.log`）。保留全量失败，不报全量绿；后续单独确定性排序/测试夹具审查。
7. `backend/.venv/Scripts/python.exe -X utf8 scripts/probe-configured-chat.py --run`：预算仅一次、32输出token、20秒超时、纯“连接成功”提示、不带工具/私有上下文、无重试。**实际0调用**：发现deepseek-flash匹配2条，进一步只读确认2个不同owner/2个provider，安全停止，未随意选别人密钥；`logs/chat-response-live-probe.log`、`model-ambiguity.log`。当前IAB在127.0.0.1首页及localhost工作台均无登录会话，已请用户登录后继续。不重发密钥、不虚称连接通过。
8. READ ONLY/5秒查询确认AI任务仅1 paused，挖掘/主播空、股票研究均终态；核对本项目local_service.py backend进程身份后停止，仅执行 `./scripts/run-local.ps1 -TimeoutSeconds 60`，退出0，五服务ready。`logs/chat-response-before-reload.log`、`reload.log`。随后后端health/前端market HTTP200；健康检查不是金融数据或完整功能认证。

## 兼容、金融口径、成本及回退

- 无表结构/数据迁移；不更改原行情、来源、因子、回测、资金/账本、策略或实虚盘权限。股票聊天保护仍保留，不借此开放未实现的股票交易工具。
- SSE保留旧message字段，新增可选元数据及前端可选error字段；原API参数不破坏。max_tokens输出上限可能缩短长回答，尤其思考型模型，需要后续按模型能力单独配置而非无界放开。
- 密钥仅可由既有运行配置内部解密，未输出/拷贝到日志。诊断探针因归属不明确未外呼；受控测试新增模型费用0，不将原后台运行成本也说成0。
- 回退只需还原本批代码并重载；数据库无回滚步骤。浏览器旧宽度仍兼容；旧代码会恢复狭窄默认和原错误行为，不应清空其他用户设置。不要回退此前只读启动门。
- 未执行：登录后实际窗口/真实供应商回答、完整发布构建/生产灰度。上游TPM/RPM配额由供应商控制，仍可能失败；本轮不是取消其速率限制。

## 人工路径

登录期货工作台→打开AI浮窗→标题栏“宽屏阅读”，应完整显示长消息和发送/关闭按钮；再次点击恢复原宽度。窄屏和缩放后窗口仍可见。

选本人渠道→先发“只回复连接成功”→如429应看到中文TPM/RPM原因及供应商等待建议，而不是原JSON；不要连续猛点。如果成功再问新问题，确认不是重复回答前一轮。图表分析仍需本人当前盘有可追溯行情，缺数据不得生成点位。

## 下一轮三包

1. 用户登录后实际DOM/宽屏/窄屏视觉验收，限定一次最小模型请求、记录脱敏结果；身份未确认前不调用任何其他用户渠道。
2. 若最小请求成功但带工具请求被限流，按真实返回/供应商文档和显式预算核对当前模型TPM/RPM、输入/输出token与工具上下文；不盲目重试、不私换源、不删权限边界。
3. 单独审查全回归研究任务认领次序反例，建立确定性夹具验证，不放宽owner权限；继续已有GP种群与可信期货数据/研究后续包，不能混称本轮完成。
