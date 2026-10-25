# 聊天、通知和透明登录执行包

用户已确认执行2026-10-21-preview-chat-notification-remediation.md，并新增登录背景虚化但能看见数据页面。按四个工作包顺序执行；不改期货资金/撮合/订单/策略或已有自动AI任务，不提交/建分支。

## 包1：聊天错误闭环与轻量路径
- [ ] 新TS流解析反例先红：错误元数据、无终态EOF、取消/失败/完成互斥；规范超时、限流等待和请求编号，兼容旧回调。
- [ ] 新普通对话模式仅一次文本调用，不执行检索/摘要/工具；旧分析模式保持旧行为。新增持久请求幂等表、明确参数hash/本人/JWT盘/状态，未知执行不得自动重放，记录费用unknown。单次输出256和总时限90秒。
- [ ] 版本化扩展迁移及真实私有PG验证；原安全门禁不移除，股票聊天仍未开放，不假称研究工具齐全。

## 包2：提醒是否弹出
- [ ] 通知展示偏好新增布尔列，默认true兼容，旧消息生成字段/通知事实不变；严格本人输入/保存回读。
- [ ] 本人隔离store、铃铛快捷开关与个人中心共用；关闭清除普通/大单/任务弹窗，不标已读，不断开行情。偏好初始化失败不强弹，保存失败显错/回滚，换号不串。
- [ ] 先红后绿、真实PG及原通知/期货安全回归。

## 包3：登录虚化及加载减负
- [ ] 登录页保留原表单/登录逻辑，背景只调用原公开showcase接口，展示公开看板/来源时间/真实空或错误；不挂需要登录的dashboard、不开新匿名私有数据接口，不用假K线/假收益填背景。
- [ ] 小程度背景虚化、透明登录卡，桌面/窄屏可读；不滚动透传、不操作背景、不覆盖浏览器密码管理。
- [ ] 隔离初始化失败，非首屏助手按需加载，行情金融订阅不改变；浏览器真实截图核验。

## 包4：独立构建、回归和运行验收
- [ ] 先迁移兼容扩展，再正常后端只重启本对话管理的已核验进程；保持手动暂停AI任务不恢复。迁移失败不切服务。
- [ ] 独立Next构建目录与production预览端口，不能破坏现有.next或抢占服务；完整产物/资源/HTTP/浏览器通过才展示，不忽略EPERM。
- [ ] 每包受影响测试、完整后端/Node/TSC、私有PG及HTTP；核对明确共享白名单和原保护差异。新增支付/源调用默认0；真实短对话限一次256 tokens且需本人登录，失败停止不自动重试。
- [ ] 保存真实文件/命令/失败/未验收、费用和回退。旧SQL兼容新增列/表；回退代码不删除幂等记录/真消息，若实际付费未知不能因回退重复发起。

授权白名单：frontend/src/lib/ai-stream.ts、stores/ai-chat.ts、types/index.ts、components/ai/message-renderer.tsx、components/market/ai-chat-panel.tsx、app/(main)/layout.tsx、components/layout/top-navbar.tsx、stores/notifications.ts、stores/market.ts（仅三类弹窗展示门禁）、components/ai/floating-assistant.tsx（按需加载）、components/profile/alert-settings-panel.tsx、app/(auth)/login/page.tsx、next.config.ts、tsconfig.json（Next独立构建自动格式化及生成类型路径，不放宽检查）；backend/app/api/ai_chat.py、services/ai/gateway.py、services/ai/providers/openai_provider.py、services/ai/providers/claude_provider.py、models/user_notify_setting.py、schemas/notify.py、api/users.py及各自新增独立模块/测试/迁移/运维工具。期货金融与共享认证逻辑不在白名单。

## 2026-10-22执行检查点

以上复选项保留最初验收清单，不以写完代码替代通过。实际逐项结果和命令见 `docs/UX-REMEDIATION-VERIFICATION-2026-10-22.md`。
- 包1：普通对话、幂等/取消/错误及迁移已实现并经私有PG和浏览器异常路径验证；实际本人模型仅调用一次，返回供应商429，正常回答验收未通过，不自动重试。
- 包2：弹窗总偏好实现并完成本人关闭→刷新保留→恢复原开启的浏览器验证；消息生成、未读与独立声音开关不变。
- 包3：透明登录、公开脱敏背景、按需助手和独立初始化已实现；浏览器额外复现会话恢复竞态，现私有子页及偏好等待loaded，重刷A股看板不再出现旧请求错误。1280宽及472窄视口均查看。
- 包4：本机两项扩展迁移已应用并校验；独立production构建3001，保留原3000开发服务和原模拟任务paused/user。完整Node仍有40失败，不能宣称全量发布验收通过。未接真实账户、未升级依赖、未提交/建分支。
