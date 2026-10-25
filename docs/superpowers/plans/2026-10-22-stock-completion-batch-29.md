# B29 期货瞬时提醒的市场展示隔离（独立共享兼容包）

**Goal:** A股页面不再出现/播报期货大单和期货任务成交的瞬时提醒，返回已确认期货市场后原提醒仍有效。
**Architecture:** 不改后台事件生成、推送/盘路由、成交/资金/任务或持久通知表。仅在共享前端的期货瞬时提醒消费入口和展示组件增加当前市场门禁；切市场同步清理两类瞬时队列，不改总弹窗偏好或未读。独立包列明共享例外，不能称为所有期货代码完全未动。
**Tech Stack:** 既有TS/React/Zustand/Node/pytest、真实私有PG/HTTP、独立Next构建，无新依赖/DDL/上游调用。

## 原因与边界

B28本人截图已记录A股历史页显示AU2612大单。原main布局全市场常驻Market WS；big_order_tick及前端成交扫描进入fireBigOrderAlert，只判断用户阈值和总弹窗，不判断当前市场；两个提醒组件也无市场条件。偏好owner仅用户+sessionRevision，市场切换不增加revision，故队列不会随市场自动清理。

后台big_order_tick来源是期货orderbook，task_order_tick是现有期货AI任务成交推送；不通过代码正则猜市场。通用notification未记录市场，仍维持原消息/未读和声音行为，不擅自隐藏资金/系统/风险消息；其市场/盘Schema演进另包。

冻结：backend全部、认证/WS/布局/配置、金融函数、期货订单资金任务、AI网关/主播/语音定时报价引擎。共享例外仅4个生产文件：stores/market.ts、stores/notification-preferences.ts、components/notifications/big-order-toast.tsx、task-alert-toast.tsx。新增lib/futures-alert-policy.ts及测试，修改原notification-preferences测试。先保存4文件原字节回退副本/哈希，严格验证其他保护文件无漂移；双市场回归不通过不部署。

## 四工作包

- [x] P1 先红后绿：真实原WS dispatcher及成交扫描的市场门禁。当前user.market_type=futures且有会话、非切换中才呈现/播报大单和任务瞬时提醒；stock/unknown/pending不消费限频/去重，恢复后同笔事件可按原规则提醒。通用notification未读/声音、期货quote/trade缓存及任务记录刷新保留，不改变订单执行或阈值/颜色公式。
- [x] P2 先红后绿：市场变更/切换pending时同步清两个瞬时队列，保留通用消息弹窗、未读、偏好和未完成偏好写。两个组件自身即使遇到存量队列也拒绝stock/pending展示；期货旧渲染正常。
- [x] P3 全量前端、TSC、完整期货关键回归和通知/市场后端回归，真实私有PG偏好迁移/HTTP/用户隔离检查（只做QA，不新增生产迁移）。独立只读复查，不以mock代替数据库，不测试真实交易/付费模型。
- [x] P4 独立最终build、还原生成配置原字节、保护hash和已声明共享文件差异核验，核对自有3001进程后发布；本人已有登录会话检查A股/期货切换与历史读取、恢复A股预览，记录未发生事件的观察限制，不用假WS注入或伪造金融订单验收。

## 兼容、迁移及回退

无DDL或后端协议变更。期货稳定态的阈值、弹窗总开关、语音/提示音独立开关、颜色、限频、指纹及刷新保持；只新增身份/市场展示门禁，stock和切换中被丢弃的瞬时提醒不回放，实际历史/消息不删除。不得暂停期货WS来掩盖问题或停止真实提醒生成。

回退保留原后端与全部数据：恢复4共享文件及旧前端产物（逐字节副本在logs/stock-b29-before-*.bin，manifest记录SHA）；恢复旧弹窗行为而不是恢复资金/任务。只管理已核实的本对话3001进程，不重启8002或恢复既有paused/user AI任务。0新增模型/数据源预算。系统其他未完成包继续，不停止自动化。

复查补充：仅缓存user/token不够；门禁还须loaded===true且!hydrating，已用恢复中不消费去重反例先红后绿。最终详情见`docs/STOCK-COMPLETION-BATCH-29-VERIFICATION-2026-10-22.md`。本轮只完成上述两类瞬时提醒展示隔离，通用持久消息、主播及定时报价语音未在本包变更。
