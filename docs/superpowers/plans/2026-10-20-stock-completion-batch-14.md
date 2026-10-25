# B14 AI模型精确选路与渠道停用保护 Implementation Plan

> **For agentic workers:** 顺序executing-plans/TDD，禁止提交/分支、供应商调用和覆盖B13；独立审查额度仍不可用时不重试绕过，保留未验收项。

**Goal:** 修复界面选择模型行但后台按同名model_id任取第一行、忽略渠道归属/停用的根因，避免错误渠道费用和“配置正确却无响应”。不代替S04持久确认或S05数据库历史分盘。

**Architecture:** 增加可选model_record_id UUID，网关同时校验本人模型/本人启用渠道/一致的model_id；旧仅model_id路径只允许唯一匹配，歧义拒绝不猜渠道。前端先用捕获凭据验证服务端精确选路能力，旧后端不支持时不发送付费POST；保留旧streamChat字符串签名。复用现有表和SSE错误metadata，不改DB、供应商或金融方法。

**2026-10-20 20:43状态：** 以下四包已实施并本地/隔离验证；完整后端2614通过/3既有skip、Node470/TSC0、真实PG63通过并清理。未发布，独立审查/整项目运行/构建与人工验收仍阻断；详见`docs/STOCK-COMPLETION-BATCH-14-VERIFICATION-2026-10-20.md`。系统仍部分完成。

## B14-1 精确模型选路垂直链路
- [x] 读ChatRequest→SSE generator→gateway及memory内部调用、AIModel/AIProvider、前端选择/stream和测试；新反例先红，明确不信任请求中provider/user/盘。
- [x] 后端可选UUID传递至gateway，SQL强制双owner、active、model_id和可选row UUID，最多2行；精确/不存在/不一致/错owner/停用有稳定脱敏error_code，无解密或模型调用的拒绝路径。
- [x] authenticated chat capabilities提供owned-model-row-v1/no-store/request_id；前端带捕获row UUID，能力不匹配/404/失败时明确不可调用，不静默回旧model_id。旧唯一model_id和旧stream签名仍兼容。

## B14-2 旧调用方及摘要安全
- [x] 内部摘要和旧客户端没有row UUID时，最多2条匹配，歧义明确model_ambiguous、不付费选第一渠道；唯一启用本人渠道保持原行为。
- [x] 调整既有gateway测试的结果容器契约而不弱化安全断言；复测无工具摘要、禁用工具/自确认/错市场/错盘及401等原保护，不扩summary/记忆权限。

## B14-3 模型撤销与UI状态
- [x] 设置删除模型/停用渠道后，正在接收的旧模型流取消，选中行失效；select/restore拒绝已知停用渠道，未加载provider不猜active，以服务端为准。
- [x] 真实Zustand反例覆盖删除/停用后迟到SSE、选中行UUID不被同名替代、能力探测错误不POST、旧签名兼容；不自动探测供应商/重试付费模型，不恢复次数配额。

## B14-4 隔离验证与检查点
- [x] 真实私有PG/JWT/ASGI：同名双渠道精确命中、legacy歧义、停用/错owner provider关系/无效UUID/不一致拒绝、模型工厂调用计数；保留原账户/交易守卫，公共表不写、私有schema清理。
- [x] 目标红绿→受影响和完整后端/Node/TSC；只读确认服务现状，不抢3000、不默认启动计费业务，不把私有fixture当真实供应商；B13的EPERM/缺价/会话分盘仍列阻断。
- [x] 逐包本人复查并保存真实文件/命令/失败和未执行、API兼容、无DDL/公式改变、回退/费用及下一3—5包。系统仍部分完成，保留续做自动化。
