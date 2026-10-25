# 预览、AI、提醒与透明登录：真实交付检查点

2026-10-22 B26更正：下文全量Node“540测试/40失败”是当时真实命令结果，但漏用了项目已有 `logs/node-alias-register.mjs`，不能据此视为40产品缺陷或继续待修。B26从frontend按原命令 `node --experimental-strip-types --import ../logs/node-alias-register.mjs --test @tests` 复核，修改前577测试全通过；详见B26记录。保留原失败证据，不删除或弱化测试。

## 完成状态

部分完成。通知偏好和登录/加载改动已通过本轮链路验证；普通AI安全执行及错误链路通过，但本人供应商正常回答仍被429阻断。全量Node未全绿，不能宣布全系统或正式发布验收完成。

## 实际改动文件及数据流

完整保护范围及逐文件前后SHA256：`logs/ux-preservation-verified.json`。这不是“所有共享文件零修改”：本轮用户批准了共享聊天、通知和UI兼容包。对B25的1018原保护文件验证，期货撮合、资金、订单、策略、认证、行情金融计算没有范围外变化；原始保护清单未覆盖或重置。

### 包1：有界普通对话和错误闭环

- 后端：`backend/app/api/ai_chat.py`、`backend/app/services/ai/plain_chat.py`、`backend/app/services/ai/gateway.py`、`backend/app/services/ai/providers/openai_provider.py`、`backend/app/services/ai/providers/claude_provider.py`。
- 前端：`frontend/src/lib/ai-stream.ts`、`frontend/src/stores/ai-chat.ts`、`frontend/src/components/market/ai-chat-panel.tsx`、`frontend/src/app/api/ai/chat/[[...path]]/route.ts`。
- 新schema与CLI：`backend/migrations/20261021_014_plain_chat.sql`、`backend/scripts/migrate_plain_chat.py`。
- 默认普通文本路径：本人精确模型行及市场/JWT盘校验→请求UUID持久claim→一次供应商文本调用→增量SSE→本人结果/usage落库→查询终态。普通模式不做RAG、embedding、摘要或工具；最多3000字节历史、16条、256输出token、90秒；不自动重试。默认未改变工具分析旧预算。
- 唯一请求ID及同人running部分唯一索引，防重复提交/并发收费；断网/取消/过期收敛unknown、费用unknown，不能声称供应商没有计费。GET仅本人同盘可读，过期GET也收敛状态，不重新执行。
- provider结束/截断/格式错误不再冒充成功；结构化429/等待秒数/request_id传到前端。显式aclosing关闭嵌套provider；AnyIO持续取消下有界shield清理及终态保存，最多5秒，失败日志及GET过期兜底。
- 普通模式不再显示实时工具建议，禁用知识库上传和旧工具任务继续入口；工具分析仍可显式选择。股票聊天旧409与隐藏入口不解除，未修复旧会话market/mode持久绑定，不宣称股票Agent已接通。

### 包2：弹窗展示与通知事实分离

- 后端：`backend/app/models/user_notify_setting.py`、`backend/app/schemas/notify.py`、`backend/app/api/users.py`（仅notify-settings）、`backend/app/services/notify/preferences.py`。
- 扩展：`backend/migrations/20261021_013_notification_popup.sql`、`backend/scripts/migrate_notification_popup.py`、`backend/scripts/verify_notification_popup_pg.py`。
- 前端：`frontend/src/types/index.ts`（NotifySetting可选兼容字段）、`frontend/src/stores/notification-preferences.ts`、`frontend/src/lib/notification-popup-policy.ts`、`frontend/src/stores/notifications.ts`、`frontend/src/stores/market.ts`（仅三类展示条件）、`frontend/src/components/layout/top-navbar.tsx`、`frontend/src/components/profile/alert-settings-panel.tsx`。
- 新布尔notify_popup_enabled默认true保持旧行为；本人严格布尔PUT，唯一user创建防并发。关闭仅清消息/大单/任务展示队列，不关消息生成、不标已读、不关声音或行情连接。
- 共用store按user/sessionRevision隔离，合并读取、串行写、过期响应丢弃、失败回滚、跨tab更新；初始化失败不强弹，旧后端不支持字段时禁用开关并解释。

### 包3：透明登录及加载减负

- `frontend/src/app/(auth)/login/page.tsx`、`frontend/src/components/landing/auth/login-dashboard-background.tsx`：透明卡片/适量模糊背景，原登录数据盘和表单不变。背景仅请求已有公开showcase，credentials=omit、8秒、最多4样本；只保留公开数量/采集时间，不展示私人资金、持仓、身份或报告，不伪造行情图。
- `frontend/src/app/(main)/layout.tsx`：allSettled隔离初始化，股票不拉期货代码树/合约名，保留全局行情/通知连接；移除重复未读初始化，顶部原轮询保留。浏览器刷新复现旧会话结果错误后，新增loaded gate：会话校验后才挂载私有页和偏好，deps含userId/revision，未改认证实现。
- `frontend/src/components/ai/floating-assistant.tsx`：面板动态按需加载，关闭时不挂重型聊天树。
- `frontend/next.config.ts`、`frontend/tsconfig.json`：独立build目录、preview非standalone；普通构建仍standalone。Next生成的preview类型include及格式化是明确兼容例外，没有关闭TS检查。没有解决或宣称已解决旧Windows standalone EPERM。

### 包4：运行与验证工具

- 新`script`：`scripts/test-ux-plain-postgres.py`、`scripts/check-ux-preservation.py`、`scripts/verify-ux-runtime.py`。
- 前端测试：`frontend/src/lib/ai-stream.test.mjs`、`frontend/src/lib/chat-proxy.test.mjs`、`frontend/src/lib/ux-loading.test.mjs`、`frontend/src/stores/ai-session.test.mjs`、`frontend/src/stores/chat-response.test.mjs`、`frontend/src/stores/notification-preferences.test.mjs`、`frontend/src/components/landing/auth/login-dashboard-background.test.mjs`。旧VM补AbortSignal真实平台能力，没有删除或弱化断言。
- 后端测试：`backend/tests/test_plain_chat.py`、`backend/tests/test_chat_provider_resilience.py`、`backend/tests/test_notification_popup.py`、`backend/tests/test_notification_popup_migration.py`。
- 进程规格：`logs/ux-production-frontend.spawn.json`，通过原local_service隐藏启动。只停经过CommandLine核验的本对话backend/3001进程；保留3000开发服务，没有恢复付费任务。

## 实际命令和结果

所有命令从仓库根执行；build在frontend执行，env为WENYUAN_BUILD_DIR=.next-wenyuan-preview-v3、WENYUAN_PREVIEW_BUILD=1、NEXT_PUBLIC_CHAT_SAME_ORIGIN=1、BACKEND_URL=http://127.0.0.1:8002。

1. `backend/.venv/Scripts/python.exe -X utf8 scripts/test-ux-plain-postgres.py`：最终27项检查通过，真实私有PG+ASGI，认证边界和provider为显式fixture，不是本人JWT/付费接口替代认证。覆盖幂等、并发、本人/盘/市场拒绝、过期GET、取消/嵌套资源关闭、usage及费用未知；schema已清理，public业务写入0。direct gateway aclose先红再绿，证据`logs/ux-gateway-close-red.log`→`logs/ux-plain-private-pg-final.log`，不能把先前取消测试直接绿说成红。
2. `backend/.venv/Scripts/python.exe -X utf8 backend/scripts/verify_notification_popup_pg.py`：7组通过，真实隔离PG，schema清理，public写入0。
3. 两个migrate CLI分别`--apply`及`--check`：本机5433兼容扩展成功、重复检查成功，证据`logs/ux-notification-migration.log`、`logs/ux-plain-migration.log`。notify检查数据库READ ONLY；plain检查不写数据但仅SET LOCAL/只读查询/事务咨询锁，不虚称数据库强制READ ONLY。
4. `node --test`本轮七个相关测试文件：70通过，0失败，详见`logs/ux-client-affected-final.log`。流/偏好/登录/初始化各自反例日志保留。
5. 全量前端：`$tests = @(Get-ChildItem frontend/src -Recurse -File -Filter '*.test.mjs' | ForEach-Object { $_.FullName }); node --test @tests`：540测试，500通过、40失败，日志`logs/ux-node-full-final.log`。主要为Node直接加载TS时无法解析@别名和原测试运行环境；本轮未改这些范围外生产模块，也不能断言未跑基线的全部失败都是既有。无跳过/关闭检查/放宽断言。
6. `node frontend/node_modules/typescript/bin/tsc --project frontend/tsconfig.json --noEmit --incremental false`：最终结果记录在`logs/ux-tsc-final.log`。初次proxy Uint8Array BodyInit类型失败已以ArrayBuffer修正，失败日志保留。
7. `node node_modules/next/dist/bin/next build`：三个隔离目录均真实构建成功；v2修会话恢复竞态，v3修普通模式建议与上传入口，未覆盖默认.next或正在运行的build目录。`logs/ux-production-build-v3.log`；这不是部署standalone发布认证。
8. `backend/.venv/Scripts/python.exe -X utf8 scripts/verify-ux-runtime.py`：登录/工作台HTML、4个静态资源200，匿名受保护聊天/偏好401，原main生命周期及暂停任务状态核验，见`logs/ux-runtime-verification.json`。首次脚本错误期望匿名capabilities为200，实际正确401；已按真实API契约修正预期，没有放宽权限。
9. `backend/.venv/Scripts/python.exe -X utf8 scripts/check-ux-preservation.py`：原1018文件范围外漂移0；保留原校验失败/真实共享差异，不重置旧baseline。显式允许22共享修改及新独立文件，完整前后哈希记录。
10. `backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests -q`：最终2800通过、3项原有skip、741 warnings，173.62秒，exit0；`logs/ux-backend-full-final.log`。没有新增skip或删除测试，旧AsyncMock等警告未顺便重构。

## 本人浏览器与模型验收

- 用户自行登录3001，未发送/读取/打印密码、密钥、JWT。实际关闭提醒、刷新保留关闭、恢复原开启；消息未读3保持。新会话gate后再次刷新A股看板：有真实快照/缺口说明，无“会话已变更”旧请求错误。
- 1280×720登录截图`logs/ux-login-preview.png`和约472窄视口实际检查；表单滚动、背景不可操作、不暴露私人数据。截图前清空临时预览表单，未改服务器凭据，未退出本人工作台。
- 期货本人普通对话只发送一次“请只回复：闻苑连接成功。”，没有工具/检索/摘要或自动重试。request_id=11faa4f4-706c-4867-9ede-c56a30642fc5，供应商429/provider_rate_limited，GET及数据库均failed，usage=null、cost_state=unknown。不能把它说成密钥错误、余额不足、0成本、已正常回答或可绕过TPM/RPM。
- 单次输出上限256；实际费用未知，未调用第二次或自动换渠道。原AI交易任务仍1项paused/user，未启动新交易/订单。测试后恢复用户A股工作台和原提醒开启。
- 本机production登录HTML采样约15.5ms/24.1ms，dashboard约8.8ms；不是浏览器FCP/INP、并发容量或服务SLO。旧开发首编译约秒级，本轮没有以HTTP200冒充所有界面响应快。

## 金融、迁移和回退

本轮没有行情/复权/费税/资金/撮合/策略公式变化，不导入行情，不扩大数据授权。原main生命周期自带既有新闻/行情调度可能继续外部请求，不能将整个后台运行宣称0数据源成本；本轮新增验证工具和fixture调用上游0。

两项迁移只新增字段/请求表及版本表，不删除旧结构，事务/短锁超时。回退旧应用可以保留新列/表，但不得删除幂等记录/已付费状态或因回退自动重发；失败采用前向修复。通知默认true兼容，前端字段可选；plain能力不兼容时拒绝调用模型。正式生产推广需DBA按实际环境重新审核，CLI有loopback5433保护。

本机预览回退：只停止核实CommandLine的3001进程，保留3000开发服务可访问；也可用此前v2产物及相同backend启动3001。代码回退需按明确共享文件逐项恢复，不覆盖用户后续改动；现无git提交可一键revert，不虚构版本回滚。不要删除schema或真消息。

## 剩余风险和下一轮3包

1. 供应商控制台核验该具体模型TPM/RPM和账户可用配额；待用户确认额度/预算后再做一次短普通对话，失败不重试；正常回答和token成本验收仍未通过。
2. 在独立测试配置中修复前端全量Node的TS别名/浏览器环境加载契约，先证明失败来源，再回归金融关键前端测试；不为绿灯改生产鉴权/金融逻辑。
3. 工具分析仍有旧embedding自动选择、长历史/摘要和聚合调用预算问题；按独立兼容包分离嵌入配置/异步研究并增加总费用边界。股票聊天和旧会话market/mode持久绑定留在原主计划，不解冻期货资金/撮合。完整发布/回退及压力测试未执行。

人工复验：打开http://127.0.0.1:3001/login看透明背景，自行登录→铃铛开关/刷新→A股工作台真实快照和缺口→切期货打开普通对话查看请求状态。不要为复验启动AI交易或反复点模型测试。
