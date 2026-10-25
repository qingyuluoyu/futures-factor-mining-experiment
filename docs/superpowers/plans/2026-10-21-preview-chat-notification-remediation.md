# 预览速度、AI对话、提醒开关 Implementation Plan

> 使用systematic-debugging完成本轮代码调查，后续用executing-plans、test-driven-development、verification-before-completion顺序实施。当前用户要求是分析代码和提供方案；本轮没有修改生产代码或运行配置，不代表这些问题已修复。共享兼容范围待用户确认后再实施，不能仅凭旧续做自动化解冻这些目录。不提交或建分支。

**Goal:** 提供不拖慢操作的正式本机预览、可诊断且费用受控的AI对话、可由用户决定是否弹出的通知，保留期货原金融行为。

**Architecture:** 沿用Next/React/Zustand、FastAPI/SQLAlchemy、现有Redis和模型适配层。将共享前端/聊天/通知改动作为独立兼容包，不夹带股票数据整改，不改期货资金、撮合、订单、策略、交易时段或盘隔离。先隔离构建和验证，再显式切换预览；AI普通对话与工具研究分离，通知“生成/保存”和“展示/声音”分离。

**Tech Stack:** 现有Next15/React19、Node测试、Python/pytest、真实私有PG/HTTP；不新增框架、队列基础设施或供应商。

## 已确认的证据

### 1. 预览慢：有开发编译开销，不是所有后端接口都慢

- `frontend/package.json:7`的dev是next dev --turbopack；当前运行记录也明确不是生产build。前端日志实际出现/dashboard编译918ms、/ai-trading 2.1秒、/trading 1529ms等，不只是代码注释。
- 本机本轮串行匿名HTTP：/dashboard首次1545.6ms、紧接着32.3ms；/login首次175.8ms、第二次52.0ms；注册状态同域15.8ms、后端直连11.6ms，health121.2ms。`logs/three-issues-http-timing-20261021.json`。这是单次本机HTTP样本，不是本人登录后浏览器FCP/INP，也不是容量或SLO认证。
- `frontend/src/app/(main)/layout.tsx:48`将通知、未读、大单/任务偏好及合约名初始化绑在Promise.all；顶部又会首次拉未读。`layout.tsx:97`无市场区分启动全局行情连接，`:106`拉期货代码树；即使股票模式也会执行。属于有证据的额外工作候选，具体占用需登录后性能记录，不能仅凭代码推定所有卡顿原因。
- 主布局静态import全局助手/搜索等模块；关闭浮窗不等于这些代码不进依赖图。可拆分按需加载，但行情/资金关键订阅不能随意延迟或节流成错误盈亏。

### 2. AI失败：存在链路问题，不能全部归咎于Key

- 用户先前截图的inference exceeds tpm/rpm limit是供应商速率限制，不是余额充足就不会出现，也不能靠删除本系统业务次数限制绕过。现OpenAI适配器已经识别429/402/认证/空返回/超时并不自动重试；不是完全没做错误处理。
- `backend/app/api/ai_chat.py:162`读历史，`:168`长期记忆检索，`:183`知识库检索，均在首个模型响应前串行await。两个检索分别调用embedding；`backend/app/services/ai/embedding.py:96`选择本人第一个启用的openai兼容渠道，不检查embedding能力，固定请求text-embedding-3-small、单次30秒。支持聊天并不表示支持该embedding模型。
- 本机仅做配置聚合：3个启用的兼容渠道，已注册名称含embedding的模型为0；不能据此断言所有渠道一定不支持embedding，但足以说明当前没有显式嵌入模型配置。未解密、打印或试用密钥。
- `backend/app/services/ai/memory.py:45`达到阈值后实际await摘要，虽然注释写“不阻塞”。同文件加载全部会话消息后才截近20轮；会话越长，查询/摘要/输入成本越不受控。普通一句问答也可能在回答前等待多次外部调用。
- `backend/app/api/ai_chat.py:242`阶段1没有启动/进度/心跳事件，增强上下文失败在阶段2的try外；可能表现为首包前长时间无反馈或直接断流。每次聊天可至多5轮工具/模型，每次固定max_tokens4096，尚非整次聊天的聚合费用上限。
- **真实本地复现1**：后端SSE带error_code/provider_rate_limited/retry_after_seconds=17/upstream_status=429，`frontend/src/lib/ai-stream.ts:289`只交付message字符串给onError，结构化信息丢失，界面无法可靠倒计时/分类处理。
- **真实本地复现2**：无done、无error、无正文的EOF，`:235`依然调用onDone，前端可能空白结束、误以为完成。见`logs/three-issues-sse-reproduction-20261021.json`，执行真实TS代码+内存SSE，不是实际供应商对话。
- 本机前端文件配置指向127.0.0.1:8002的WS，SSE函数会由其导出HTTP直连；因此**不能把当前失败一概说成Next反代30秒超时**。未读取运行进程环境/已编译bundle，本轮未登录触发付费对话，也未取得用户最近一次失败的request_id，故不能断言每次都是同一种原因。
- 股票聊天当前明确409并隐藏旧入口，是已有安全门禁，不可为“能聊天”直接复用期货账户/工具。本方案不顺带解除此门禁。

### 3. 提醒没有总弹窗开关：生成开关不能代替展示开关

- `frontend/src/components/layout/top-navbar.tsx:180`铃铛目前只跳消息中心，没有弹窗开关。
- `backend/app/models/user_notify_setting.py:12`只有trade/fund/price_alert生成开关及sound，缺普通消息popup字段。
- `frontend/src/stores/market.ts:433`收到普通notification就showPopup、增未读、播放提示音；`frontend/src/stores/notifications.ts:67`无条件设置popup。
- 大单/任务已有各自popup_enabled，但没有与普通消息一致的总展示策略；不能宣称所有提醒都无开关，也不能用关闭成交通知来替代关闭弹窗（那会改变消息是否生成）。
- 布局的Promise.all任一初始化失败会跳过提示音设置应用，保留默认开启；偏好读取失败不应被当作用户选择开启。

## 全局门禁

- 原1018保护清单本轮前后零漂移。后续每包白名单需先核实，涉及共享模块的改动必须独立留痕、受影响期货回归、逐项回退，不改原金融公式或账本。
- 不强停/抢占现有3000/8002，不在运行中的默认.next目录直接构建；不随手升级Next/React/模型SDK。
- 不新增供应商调用作为调查手段。将来的真实模型验收先明确预算：首轮仅1次短纯文本请求，输出上限256，不能触发embedding/摘要/工具/自动重试/自动恢复任务；任一失败保留request_id后停止。需要更高预算另行确认。
- 用户密码/Key/JWT不能出现在日志；模型/提示词/方法版本、来源、token和成本未知时必须标unknown，不能记为零。

## 包1：快速且可回退的预览构建

**Files:** `frontend/next.config.ts`、`frontend/src/app/(main)/layout.tsx`，新增受控本机preview构建/启动工具及相关测试；组件拆分只限经性能记录确认的大模块。

**实施步骤**
- [ ] 先记录本人登录后的各页首访/二访、API、首屏/主线程和WS流量；先区分编译、bundle、HTTP查询、图表渲染，不凭感觉全站重构。
- [ ] 给生产预览构建独立输出目录，默认.next和现dev不动；锁定现依赖，明确build与start的BACKEND_URL/公开基址一致。遇到既有Windows standalone EPERM先修发布产物，不忽略错误后称生产包可用。
- [ ] 按需加载重型助手/搜索等非首屏内容；初始化分离，通知偏好加载失败不连带其他结果，去重未读拉取。股票不请求期货无关代码树/行情，先核对所有市场/盘切换与资金价格消费者，不能关掉期货需要的订阅。
- [ ] 隔离端口先做匿名性能和资源验证；当前后端CORS只预置3000，不能直接声称3001上的旧直连SSE可用。结合包2同域通道验证，或使用明确许可的本机origin配置，不加通配符/不放宽鉴权。

**验收/回退:** 必须有真实build产物、资源200/客户端可交互、已登录关键页性能及期货功能回归；只提出测量后确定的改善目标，不承诺所有页面固定毫秒。用户确认后才切换常用入口；失败保留原dev及旧产物，回退只涉及本轮前端进程/构建，不停后台调度或恢复AI。

## 包2：AI流式传输与错误闭环

**Files:** `frontend/src/lib/ai-stream.ts`、`frontend/src/stores/ai-chat.ts`、`frontend/src/types`实际StreamEvent/ChatBubble定义、聊天状态/气泡组件；必要时新增独立`frontend/src/app/api/ai/chat`流式适配及兼容能力契约；`backend/app/api/ai_chat.py`只改聊天生命周期/事件边界。

- [ ] 将两处已复现问题写成正式红测：元数据传递、空EOF/部分正文后断流、终止事件一次性、取消和错误不互相覆盖。
- [ ] 新增兼容可选结构化错误载荷，旧onError(message)调用可继续；界面分401/403、402、429、400、网络/首包/静默/总超时、协议/空响应、市场未开放。不返回原上游秘密或堆栈。
- [ ] 服务端先发已受理/初始化进度及心跳，首包前异常也进入规范error+终态；客户端收到EOF却没有合法终态则标不完整，不以onDone作为成功证据。保留部分回答并明确中断，未生成的部分不冒充完整。
- [ ] 明确同域流式入口或经验证的直连选择，不能对SSE使用arrayBuffer/text整段缓冲；认证失败不盲重放已提交的POST，不从当前新账号借旧token补发。
- [ ] 首包、静默和总时限分别定义，用户可取消；各事件带同一request_id；监测真正完整流耗时/首token，不只记录HTTP头返回耗时。

**验收/回退:** SSE分片/中文/CRLF/部分结果/取消/断网/无done/供应商错误/401/429/旧后端均有确定状态；新旧事件消费者兼容，跨用户/市场/盘旧回包丢弃；先内部flag开放，失败退旧展示但不移除安全门禁。自动重试保持关闭。

## 包3：普通对话轻量路径、上下文与模型预算

**Files:** `backend/app/api/ai_chat.py`、`services/ai/memory.py`、`embedding.py`、`gateway.py`、providers及对应前端模式入口；必要时新增独立chat执行记录表/版本化SQL迁移，先定契约再编码。

- [ ] 提供明确“普通对话”“工具分析”“知识库增强”模式；新UI普通对话不启工具/RAG/长期记忆外调，不给假实时报价。旧客户端/已有任务保持原兼容路径，不能直接改变所有期货Agent或关闭研究工具。
- [ ] 嵌入渠道/模型独立显式配置并校验能力，无配置或向量服务失败时明确“未启用检索”，不往聊天渠道硬塞text-embedding-3-small，不补零向量伪装有效结果。
- [ ] 历史SQL有界分页/最近窗口，按token预算裁剪并保留来源/关键信息；摘要与向量更新脱离前台首包关键路径，使用现有任务机制、幂等、最大时长/次数/费用，不新增无监管后台create_task。
- [ ] 每次请求的模型输出上限按用途设置，同时限制整次编排的总token/工具次数/总时间；429保存冷却状态并展示倒计时，余额、供应商RPM/TPM与本地业务次数区分。不得恢复业务日/月限额或无限重试。
- [ ] 接通实际供应商usage事件、每个子调用与整次聚合用量/耗时，价格表和模型/提示词版本明确；usage或单价缺失则费用unknown，不能因流未结束就记作免费。预算前置保守预留、结束对账，取消/断流存在未知收费时不得无脑重放。
- [ ] 普通对话用户触发模型前，检查本人模型行/市场/JWT盘/渠道状态；补服务端request_id+幂等键+参数hash执行记录，重复/未知状态查询不重付费。重试/切模型必须用户明确操作且有费用边界；不能用模型自确认交易。

**验收/迁移/回退:** 固定评测覆盖普通问答、无检索配置、限流、权限、超时、注入、长历史、费用未知、重复提交；真实私有PG验证执行幂等/并发及原期货任务不变。新表先expand再兼容发布；旧代码可运行，回退关闭新路径但保留幂等记录/未知执行状态，不删除账本。确认预算后才做一次真实短对话，不声称未测供应商已通。

## 包4：右上角“弹出提醒”偏好完整切片

**Files:** `backend/app/models/user_notify_setting.py`、版本化迁移、`backend/app/api/users.py`通知子接口和`backend/app/schemas/notify.py`；前端types/api、notifications/popup偏好store、top-navbar、个人中心提醒面板和3类Toast入口。

- [ ] 先写默认/关闭/开启/保存失败/换账号/重登/跨标签/偏好加载失败反例；确认所有notify-settings调用方。
- [ ] 新增`notify_popup_enabled`只决定自动展示；默认true兼容旧用户，旧生成开关/站内消息保存/未读/风控审计不变。现有提示音、任务及大单开关保留，界面明确“总弹窗开关+分类开关”关系，声音独立可选。
- [ ] 右上角铃铛新增“弹出提醒”快捷开关和“消息中心”入口，个人中心同一数据源同步；关闭即清理现有弹窗/队列，但不是标记已读或删除历史。不禁止交易确认对话框、权限错误及页面内关键告警。
- [ ] 服务端仅本人设置、严格bool和兼容Schema；PUT必须返回已保存真实值，旧后端不支持时禁用新控件并说明，不显示假成功。多次切换有顺序/幂等处理，保存失败回滚并显错；晚到旧账号请求不能覆盖当前设置。
- [ ] 偏好未加载/读取失败不默认大声弹出，不追溯轰炸旧消息；加载按用户隔离，退出/换账号清理。WS继续收存与同步未读，不通过断开整个行情WS实现静音。

**迁移/验收/回退:** 兼容新增列/默认值，评估PG版本、表规模和锁，先迁移再上新代码；保留旧字段，旧代码可读写，新UI能力探测。真实私有PG验证两用户/默认/保存/非法值/重复及原通知生成语义，浏览器验证两类市场、三类弹窗、刷新/重登/跨标签。回退新UI/新字段使用，不立即DROP列，不修改期货资金/订单通知事实。

## 包5：共享变更独立回归及发布门禁

- [ ] 每包核对明确白名单，不重设原哈希掩盖变化；记录原清单差异及新增兼容基线。冻结期货资金/撮合/订单/策略/原行情口径，任何必须修改的共享点先出兼容反例。
- [ ] 运行受影响Node/pytest/TSC、完整构建、真实隔离PG/HTTP；关键资金/撮合/回测、账号/市场/盘、AI确认安全和通知入库回归不缺项。旧测试缺覆盖的两处新SSE缺陷必须新增反例，不能只引用基线绿。
- [ ] 用户自己在页面登录后完成本人逐页操作与一次已授权短对话，检查第一字/完整结果/错误/取消/通知开关/刷新；不索要密码Key、不恢复暂停AI任务、不触发真实资金。
- [ ] 确认发布顺序、前向修复、老前端/老后端兼容、日志脱敏和实际费用；写验收记录和未完成项。任何外源失败明确分离，不以改提示语或空响应当修复。

## 本轮已执行与未执行

- [x] 直接相关代码、所有核心链路调用方与模型/通知字段已调查；实际HTTP冷/热对比和真实TS流解析两问题复现，证据见上述logs。
- [x] 现有基线：AI确认/Provider/通知/pubsub 75通过；Provider错误韧性6通过；Node AI会话15通过、无跳过。命令与日志：
  - `backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_ai_chat_execution_safety.py backend/tests/test_ai_provider_thinking.py backend/tests/test_notify.py backend/tests/test_ws_notify_pubsub.py -q` → `logs/three-issues-backend-baseline-20261021.log`
  - `backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_chat_provider_resilience.py -q` → `logs/three-issues-provider-baseline-20261021.log`
  - `node --test frontend/src/stores/ai-session.test.mjs` → `logs/three-issues-client-baseline-20261021.log`
- [x] `scripts/check-stock-b23-preservation.py verify logs/futures-preservation-b25-final.json`：1018无漂移。生产代码/配置/DB结构/用户状态未改；仅新增本计划及诊断日志，产品模型/源调用0、public写0。
- [ ] 包1—5实施、生产build、真实迁移、本人登录浏览器性能与付费对话均未执行；本轮没有“解决完成”。当前慢与失败的全部生产发生比例仍需request_id和本人性能记录。

**执行建议顺序:** 包2先修“失败可诊断/不会假完成”，包3确保普通聊天轻且有成本边界；包4独立通知偏好；包1完成可用预览产物，最终包5统一发布验收。构建调查/隔离可先行，但同文件不要并行覆盖。后续可分两轮，每轮3—5包含独立验证，不能为了赶速度一并解冻期货金融模块。
