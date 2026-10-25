# 执行16：A股本人模型历史只读入口

**状态：已实现待验证。** 只读股票入口、严格客户端、列表/详情/报告与异常恢复已接线，离线目标和兼容用例通过；原PG、原HTTP、本人浏览器与发布验收未运行。不称训练工作台或课程ML整包完成。

## 范围和真实数据流

- 新增 `frontend/src/lib/stock-ml-models-api.ts`、`.test.mjs`，`frontend/src/components/factor-research/stock-model-library.tsx`、`.test.mjs`。
- 仅修改股票专用 `frontend/src/components/factor-research/stock-research-entry.tsx`、`.test.mjs`；根确认白名单后先保存字节备份 `logs/stock-ml-history-ui-backups/` 和旧SHA `logs/stock-ml-history-ui-backups.json`。原公共Workspace的精确key、token、scope、initialTab、title保持；不改公共导航、期货、共享认证/布局/配置。
- A股已验证用户主动点击“查看本人模型历史”后才挂组件和GET availability；默认Flag关闭时明确未开放，只显示原因，不读模型列表、不把空列表当成功、不POST训练。初始页不会读ML能力或模型。
- 正式契约来自已落盘 `backend/app/schemas/stock_ml_registry.py`、`stock_ml.py`，路径 `/api/stock/research/v2/models/availability`、`/models?limit=20&offset=0`、`/models/{id}`、`/models/{id}/report`。API agent负责后端，根负责股票router挂载，本子包未改后端。
- 本人当前盘分页→显式详情→固定参数/数据与模型版本/各窗口训练验证测试日期/知识截止/训练环境与树数/特征gain→显式读取已存报告。报告为原始JSON证据的只读折叠预览，尚非完整可视化ML回测结果页。读取不触发评估、训练或付费聊天。隔离模型仅显示登记详情，不给报告读取动作。

## 安全、金融口径和恢复

客户端Zod严格拒绝额外字段、非法枚举、非法日期、NaN/Inf、错误特征集合或SHA、参数树边界、fold数量/参数/数据不一致、知识截止进入测试段、ID错配与详情Scope不一致。HTTP使用Bearer、no-store、12秒超时及调用方AbortSignal；流式限制8MiB，报告只接受深度≤30/节点≤100000的有限JSON。服务端8MiB预览上限/409需要导出错误保留为明确失败，没有假导出入口。

组件使用身份对象、操作nonce与AbortController，并即时检查auth store的loaded/token/sessionRevision/stock市场/stockResearchScope；因此父组件尚未重渲染时，旧会话回包也不能落地。关闭详情或面板取消传输，忽略晚到结果；换用户/盘/市场或同token新登录后清旧面板，必须再次主动打开，回到旧身份也不自动重开。

503等可恢复错误只允许用户显式重试原操作及原offset；400/401/403/404/409/422不重复确定性请求。401/403/404清旧模型详情和报告；临时失败保留上次已读列表和页码并明确标注。后页变空有“返回模型第一页”，offset最大100000不越界。React开发effect重复执行路径已红绿验证，取消首个能力请求后仍能完成主动打开的读取。

没有修改金融公式、训练标签、费用、旧结果或数据资格。页面明确普通聊天不是机器学习训练、score不是收益/上涨概率、重要性不是因果；QA尚未完成真实数据验证、结果仅用于研究，最终测试未封存，不能声称独立样本外有效。产品范围保持模拟与日线回放。

## 实际验证

前端目录实际命令：

```powershell
node --experimental-strip-types --import ../logs/node-alias-register.mjs --test src/lib/stock-ml-models-api.test.mjs src/components/factor-research/stock-model-library.test.mjs src/components/factor-research/stock-research-entry.test.mjs src/lib/stock-custom-factor-api.test.mjs src/components/factor-research/stock-factor-editor.test.mjs src/lib/auth-session.test.mjs
node node_modules/typescript/bin/tsc --noEmit --incremental false
```

最终 **40通过，0失败/取消/跳过**，日志 `logs/stock-ml-history-ui-accepted.log`；TSC退出0，日志 `logs/stock-ml-history-ui-tsc-accepted-final.log`。没有运行生产构建，不触发tsconfig构建副作用。40包含新模型目标、股票原入口、自定义因子与会话兼容验证，不冒称全前端或浏览器验收。

根目录另实际执行：

```powershell
node --experimental-strip-types --import ./logs/node-alias-register.mjs logs/stock-ml-history-ui-contract-check.mjs
```

退出0，`logs/stock-ml-history-ui-contract-check.log`。输入由后端agent真实QA service登记/ASGI响应保存的 `logs/stock-ml-history-ui-backend-contract.json`（229600字节），四类响应均通过真实前端request/schema，包含真实2fold LightGBM4.7.0模型与 `evaluation:null` 报告；此检查替换fetch仅回放现成响应，不连接原HTTP、不发新训练，不是生产PG验证。

红日志保留 `stock-ml-history-ui-api-red.log`、`...-component-red.log`、`...-entry-red.log`，分别先证实客户端/组件/显式入口缺失；初次API命令末尾Get-Content覆盖了shell返回码，日志仍明确6失败，不把该shell退出0当测试成功。行为反例另有 `...-strict-red.log`、`...-retry-red.log`、`...-session-red.log`；随后修复effect重放、确定性请求不重试、auth store即时失效。初次API绿色尝试因QA对象共享parameters引用造成反例同时修改内外参数，改独立JSON副本后6通过，保留 `...-api-green.log` 的失败与 `...-api-green-2.log`。首次TSC指出Command合并union未充分缩窄，改为四个明确判别分支后最终TSC通过，不关闭类型检查。

一次自审覆盖旧Workspace精确props/key、用户/盘/市场与晚回包、默认能力/非0模型假成功、日期/finite/模型身份、只读报告和重试边界。根合并时将QA文案明确为“尚未完成真实数据验证，结果仅用于研究”，保留模拟范围，并明确JSON预览不是完整ML结果页；文案后重新运行40目标及TSC均通过。最终6源码/测试SHA见 `logs/stock-ml-history-ui-accepted-files.json`，先前final-files保留为文案前证据；旧文件精确diff见 `logs/stock-ml-history-ui-entry.patch`。未更新总清单、AGENTS或总计划。

**未执行：** 原PG迁移/并发、原HTTP、本人浏览器、线上期货回归、构建与部署/回退实测；未启动项目服务、应用迁移、安装依赖、获取金融数据或调用模型。外部预算/费用0；新增成本仅用户主动读取时有限GET/本地JSON显示。

## 人工路径、兼容和后续

获运行许可后以A股本人登录进入 `/factor-research`、`/factor-lab` 或 `/backtest`，核对初始不发models请求；主动打开→默认未开放→关闭。能力由正式迁移与资格验收后开放时，核本人列表/翻页/详情/报告；人为503后显式原页重试，401/403/404后旧详情消失；报告读取期间关闭或切换用户/盘/市场，旧报告不得重开。该路径本轮未执行。

无数据库迁移、旧API或训练方法变化。回退先撤销股票入口本轮精确diff，再移除4新增前端文件；不要整文件覆盖后续用户修改，不删除历史模型/报告或迁移表。备份及SHA可用于逐字节核对。

下一轮四包：019真实PG迁移和只读权限验收；016持久训练任务与能力门禁；固定数据/精确FactorRef到显式训练和取消恢复UI；真实规则/源、原HTTP与本人浏览器/全系统发布验证。不得把本只读历史当作训练任务已开放。
