# 完整整改第一批：身份、资料、研究参数、AI分盘取价

> **For agentic workers:** REQUIRED SUB-SKILL: executing-plans；按本文件顺序先红后绿并逐包复查。禁止提交/分支。共4个包，服务于主计划S01、S02、R01、S03，不代表主计划其余包已完成。

**Goal:** 消除已复现的切市场跨会话回写，替换假个人资料，让题目二已有参数真正可调，并修正模拟AI任务取错盘行情。

**Architecture:** 保持现有store/API和研究/期货服务，最小兼容扩展。UI组件按现有Card/Input/Select风格，纯行为测试通过现有TS转译+Node VM/React renderer；后端沿pytest和隔离PostgreSQL验证。

**Tech Stack:** 既有React/Zustand/TypeScript、Node test；FastAPI/Pydantic/SQLAlchemy、pytest、PG5433。无需新依赖。

## 包1 / S01：切市场异步请求

**目标文件**：修改 `frontend/src/stores/auth.ts`；创建 `frontend/src/stores/auth.test.mjs`。检查调用方 `lib/api.ts`、`hooks/auth.ts`、market switch组件。

**契约**：保持 `switchMarket(market: "futures" | "stock"): Promise<void>`。同一会话的偏好写请求应串行，UI立即体现最后意图；仅当前会话/最新意图可以回写；无用户/无token时不得发请求。保持错误回滚原用户其他最新字段，不吞掉后端失败状态成为成功提示。

- [x] 1. 加实际store行为测试：退出后失败不复活；A退出B登录后成功/失败不污染B；同一用户重新登录或恢复token也视为新会话；两个方向连续请求最后意图胜出；失败保留期间更新的邮箱/姓名字段；无登录不调用API。
- [x] 2. 执行 `node --test frontend/src/stores/auth.test.mjs` 保存 `logs/stock-completion-b01-auth-red.log`；预期上述旧代码断言失败，不接受module missing作为red。
- [x] 3. store闭包增加会话代次和请求序列；login/logout/restore使旧队列失效；按会话串行发偏好更新，已过期尚未发出的请求跳过，已发出的响应不得污染新会话。只允许同会话最新请求回写市场字段；保存已确认偏好供失败恢复。
- [x] 4. 目标测试green；原 `auth-session`、hooks/auth、market-routes、语言草稿会话回归；tsc。验证token刷新与会话更换区别，成功请求后的后端偏好与最后意图一致。
- [x] 5. 复查localStorage、退出/换号、请求失败、连续点击、跨标签边界；没有后台身份授权绕过之类过度宣称。跨标签全局顺序若未实现列剩余风险。

## 包2 / S02：真实资料读写

**目标文件**：修改 `frontend/src/app/(main)/profile/page.tsx`、`lib/api.ts`、必要时 `backend/app/api/users.py`；新增 `components/profile/personal-info-panel.tsx`及测试、`lib/profile-state.ts`及测试、后端资料API目标测试和隔离PG脚本。

**已核实契约**：`getMeApi(): Promise<User>`；`updateUserApi(data: Partial<User>): Promise<User>`仅提交phone/email/avatar；User具有id/username/contact/real_name_verified/created_at，username不可在此接口改。后端 `PUT /api/users/me`已有phone/email唯一性，返回schema的mode默认live需要核对调用上下文，不能让更新虚拟会话变live。

- [x] 1. React/状态行为反例：真实用户名/UUID/注册日期显示、无认证不显示已认证、未知时间不造日期；编辑取消恢复、空值策略按schema、不允许用户名/角色写入；重复保存、旧会话返回/失败不更新新身份；响应不能覆盖当前市场/模式。
- [x] 2. 目标测试red；检查既有可编辑字段不支持清空时UI不伪称支持；后端虚拟模式资料响应反例至少失败一个断言。
- [x] 3. 提取真实组件，真实加载/重试；编辑仅phone/email，avatar未接上传则禁用说明。保存显示进行中/409/422/timeout，成功只合并响应允许的本人资料字段；与会话绑定的API采用显式token/超时，不能在401后用另一会话重发写请求。
- [x] 4. 后端按AuthContext返回实际会话mode，保持原response字段。必要唯一冲突以事务/IntegrityError安全映射409，不先放宽schema。无新表，无清库。
- [x] 5. 隔离PG schema/HTTP验证：两个测试用户，资料持久化/重读、他人不被修改、重复邮箱/手机号409、无身份401、virtual字段不变、market不变；真实服务健康只读检查。不修改现有真实用户。
- [ ] 6. 前端目标green/tsc及相关API测试；浏览器独立QA验证加载、保存、刷新、取消、切市场。未执行浏览器时仅标实现/接口已验，不标人工通过。 **本步骤的代码/tsc已验，浏览器部分未执行，保留未勾选。**

## 包3 / R01：研究参数完整可操作

**目标文件**：`components/factor-research/factor-research-workspace.tsx`、`research-report.tsx`；新增 `research-preprocess-controls.tsx`及测试；扩展现有 `research-report.test.mjs`、`lib/stock-research-api.test.mjs`。金融函数/默认值不改变。

**契约**：使用既有Preprocess schema：winsorize=mad/3sigma/none；standardize/fillna/industry_neutral/mktcap_neutral boolean。实读当前前后端groups上限为10（不是初步审查中的20），UI提供5/10并保留从旧任务恢复的其他合法值2—10。analysis/backtest共用相同preprocess；报告组数来自保存任务，不来自当前表单。

- [x] 1. 控件行为测试red：每个开关/下拉onChange确实改变对应值、不丢其他字段；disabled不能交互；恢复10组及MAD关闭后完整显示；报告10组不是写死5；旧合法组数2可恢复。
- [x] 2. 控件复用schema类型，不新增自由字符串控制协议；字段合法值由schema parse，表单创建参数继续经researchCreateSchema。运行中参数fieldset全部禁用；报告保留原job参数标签，避免编辑后把旧结果看成新结果。
- [x] 3. 渲染控件并接原setter和submit闭包，去掉只读伪参数；组数选项与恢复兼容；错误/覆盖预检不被绕过，缺价仍明确拒绝。
- [x] 4. 目标Node green；后端研究schema/预处理/保存恢复相关测试；真实隔离PG验证params保存/读取一致（不额外调用来源），需要计算时仅真实本地可用短窗1分析/1回测、幂等/取消预算保留。
- [x] 5. 复查改参数→提交→持久化→重开；旧报告以旧参数显示，禁止把API支持当所有UI能力。基准、自定义表达式、五年仍未完成，保留限制。

## 包4 / S03：AI任务取价模式

**目标文件**：`backend/app/services/ai_trading/engine.py`，新增/扩展 `backend/tests/test_ai_trading_mode_isolation.py`；检查 `services/kline.py`、`paper/price_band.py`、`ai_trading/core/execute.py`调用方。

**契约**：既有 `get_kline_history(..., trading_mode="live")` 和 `resolve_last_price(..., trading_mode="live")`参数名实际读取后使用；不改变期货交易算法、强平规则或既有默认接口兼容。所有task取价必须显式normalized task.trading_mode。

- [x] 1. 先写反例：virtual单周期/多周期/停止强平都传virtual，live都传live；无参考价回落同盘已收盘bar；不请求模型/不真实成交。读取真实函数，替换外部取价边界捕获参数，不能只搜源字符串。
- [x] 2. pytest目标red，保存模式缺失的断言。
- [x] 3. 在获取bars前确定mode，并传每个K线/参考价调用；强平使用同task mode；避免覆盖task属性/账户，不改下单已有正确mode传递。
- [x] 4. 目标green+全部ai_trading/paper/kline相关回归；隔离PG以不同盘的受控测试历史/同一合约验证reader区分与服务响应，不写公共价格，不启动原worker。
- [x] 5. 检索engine所有get_kline_history/resolve_last_price调用，逐一复查；聊天工具模式遗漏和服务端确认仍属S04/S05，不因本包通过误报已修。

## 本批回顾、检查点和回退

- [x] 汇总每包实际red/green/集成命令、失败修复、文件和未执行验证；前后端全量回归及tsc；必要构建在隔离产物执行，不破坏原Next服务。
- [x] 后端修改若原服务未重载，使用隔离ASGI/HTTP验证并明确原运行服务尚未部署，不把源代码测试当在线修复。
- [ ] 无新依赖/公式/数据库结构；回退恢复目标文件代码，不删用户/行情/报告，不恢复整库。隔离schema仅在校验命名/所有权后原连接清理。 **未执行完整代码回退演练，保留未勾选；本批无数据迁移。**
- [x] 主动Teajoin/付费模型调用0；本地PG/CPU执行有上限。记录原调度独立运行，不宣称系统无外部活动。
- [x] 更新master相应状态；保存 `docs/STOCK-COMPLETION-BATCH-01-VERIFICATION-2026-10-19.md`。下一批按S04/S05/S06、R02、D01依赖推进，不重复实施本批已通过内容。
