# B13 AI客户端会话隔离与历史查询修复 Implementation Plan

> **For agentic workers:** 顺序使用executing-plans/TDD；requesting-code-review独立复查。当前目录无Git，不提交/建分支，不改密钥或中断运行任务。

**Goal:** 修复退出/换号/切市场盘后AI模型、私聊、迟到SSE及图表修改残留，以及历史分页重叠；清除旧文本“确认执行”假链路。不把本轮当作S04持久确认或数据库会话分盘完成。

**Architecture:** 复用现有auth.sessionRevision/JWT用户盘与Zustand，不改框架/DB。纯客户端scope只识别本人匹配JWT、市场和盘，常规同身份token轮换不重置会话；scope变化同步清理私有模型/聊天/图片并abort，异步入口及每个回调均检查捕获scope/请求代数。历史API保留原返回数组/页结构，添加有界列表分页并修正最后页重叠，前端追加去重。服务端敏感写继续关闭，旧确认只能说明未执行，不再发marker给模型。

**2026-10-20检查点：** 下述B13-1/2/3已实施并本地验证；B13-4的独立审查/完整运行和发布验收未通过，整系统部分完成。完整后端2610通过/3既有skip，Node466/TSC0，隔离PG65通过；standalone构建EPERM，8002等未运行，3000被非项目进程占用。详见`docs/STOCK-COMPLETION-BATCH-13-VERIFICATION-2026-10-20.md`，不得误标全验收完成。

## B13-1 模型/渠道私有缓存
- [x] 读所有调用方、API/http-session和auth订阅；新`frontend/src/lib/ai-client-scope.ts`纯契约及`stores/ai-session.test.mjs`先红：换号/退出/同账号新登录/盘/市场、JWT用户不匹配；普通token轮换不串身份。
- [x] `ai-settings.ts`订阅scope同步清空私有列表/测试结果，读请求最后发起者胜且旧finally不关闭新loading；写后迟到不刷新新账号、不记录旧测试结果；无自动付费探针。
- [x] selected model保存键按user/market/JWT盘，保存本人的模型row ID，旧全局键仅删除不迁移；只恢复当前已加载模型列表内的行。

## B13-2 聊天与流式回调
- [x] `ai-chat.ts`所有异步创建、切换、删除、列表、翻页捕获scope/请求revision；切换即时清空旧内容并阻断旧SSE，不向新账号发送旧prompt/图片。
- [x] `ai-stream.ts`增加可选captured accessToken，旧签名兼容；store传捕获凭据。对message/thinking/tool/indicator/confirmation/agent/error/done全事件统一守卫，旧error/done不能修改新请求busy。
- [x] UI加载依赖身份scope重触发；生成会话失败/断流仍可读且错误不进下轮上下文。新增真实store执行反例，不以源码字符串断言代替状态行为。

## B13-3 历史分页及旧确认保护
- [x] `backend/app/api/ai_conversation.py`列表limit1—100/offset0—10000，保留数组契约；messages按created_at/id稳定排序，page窗口end/start正确，最后页不重复、越界空页；本人校验保留。
- [x] `api.ts`getConversations可选分页参数；chat-history显示有界“加载更多”，store并发/切对话时不混页并按ID去重。
- [x] 旧resolveConfirmation不再发送`[action:confirm_tool]`给模型；UI显示服务器逐笔确认未开放/转手动交易，不谎称执行。服务器model confirmed=true守卫不解除；S04后续仍需版本迁移/状态机/幂等实做。
- [x] 后端会话表目前仅user归属，没有可信历史market/mode；本轮仅阻止客户端自动带入，不猜旧会话盘，不宣称数据库历史分盘完成。
- [x] 本人复查新增工具历史兼容子项：既有JSONB工具列表被dict-only Schema拒绝的根因已修，Schema兼容列表/旧对象；新旧页共用解码、异常对象显式提示，真实PG/HTTP数组验证通过，无DDL变化。

## B13-4 验证和交付
- [x] 目标红绿→完整Node/TSC及后端，真实隔离PG/JWT/HTTP验证他人404、分页并集不重叠、同时间戳稳定、列表边界；不调用业务付费模型或来源，不写公共账户/订单。
- [ ] 独立审查，缺陷补反例；只读服务/任务预检，无活动任务才核实PID安全重载。浏览器限制不绕过，人工登录及完整发布未做明示。
- [x] 保存实际文件/命令/通过失败未执行、无金融公式/DDL变化、回退、成本、风险和下一3—5包。五年缺价/GP/持久AI确认仍待做，不报告全系统完成或停续做。
