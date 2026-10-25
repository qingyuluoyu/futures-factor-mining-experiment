# 第四批：历史研究完整操作闭环 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付可操作的保存报告导出、两组研究对照、历史配置回填新研究，而不是再增加未接通菜单。

**Architecture:** 沿用已持久化的研究任务和AuthContext/ResearchJobService权限，不重算行情、不新建表。新独立报告router纳入main现有路由装配；前端历史页与研究页接通。三包均覆盖实际入口、权限、错误与测试。S04/P01/R02/D01仍保留在主计划，先交付无需危险启动DDL或外部费用的题目二报告链路；本批不开放任何聊天写操作。

**Tech Stack:** FastAPI/Pydantic、SQLAlchemy、现有Next/Zustand/Zod、pytest/Node，本地隔离PG。

## 全局约束

- 只模拟及日线研究/回放；期货历史页面与账户逻辑不改。报告始终user+JWT mode隔离，只允许读取完成且确有结果的任务。
- 新字段兼容旧结果；未知旧值显示未记录/null，不补造基准/成本/收益/数据版本。
- 不拉数据/模型，不重导、不提交/分支、不读密钥、不重启遗留DDL主进程。无数据库结构变更。
- 未实现不可变输入/PIT、基准及全市场真实历史，不通过导出/对照冒充这些已认证。

## B04-1 报告文件导出垂直切片（后端独立分工，前端主执行者）

**Files:** 新 `backend/app/services/stock_report_artifacts.py`、`backend/app/api/stock_research_reports.py`、必要新typed schema及tests；主执行者注册 `backend/app/main.py` 路由。新前端 `lib/stock-report-artifacts.ts`、`components/factor-research/research-report-actions.tsx`及tests，接入history和workspace真正结果入口。

**HTTP:** `GET /api/stock/research/jobs/{job_id}/export?format=json|csv`。复用 `service_dependency` 和 `execute` 的资源权限/feature readiness/错误模型。JSON `{export_version:'stock-research-export-v1',job:{...details},result:{...saved}}`，原method/params/result/data_manifest保留。CSV稳定section/key/date/code/name/value/extra字段，含元数据、参数、指标/IC、NAV、分组和全部调仓明细，不只导出屏幕60条。

- [x] 先红测试：匿名401、其他用户/盘404、pending/failed不可导出409、非法format422、无结果不可伪造；JSON保存值一致；CSV中文/逗号/换行/前导0和公式注入安全（字符串危险前缀加单引号，真实数值负数保留）。
- [x] 服务端只从保存报告产生下载，no-store/request_id/nosniff；文件名固定安全UUID。8MiB结果上限+明确导出边界，无路径输入/任意文件读取。
- [x] 前端捕获token/scope、AbortController、20秒边界和下载前会话校验，换用户的迟到文件不得触发浏览器下载；ObjectURL立即回收，错误可见。
- [x] 真实PG/HTTP保存报告导出、权限及CSV；Node按钮行为；只读不收费、不改账本。

## B04-2 两份保存研究对照（同一后端分工，前端主执行者）

**HTTP:** `GET /api/stock/research/compare?left_id=UUID&right_id=UUID`。只接受不同的两份本人同盘已完成、同kind任务；不同kind/同id明确422或409。仅从原结果取指标和曲线，不新调用计算模型、不重新下载数据。

**Response:** `{comparison_version:'stock-saved-comparison-v1',kind,left:{job,result},right:{job,result},compatibility:{comparable,same_method,same_data_version,same_workflow,same_requested_period,warnings},metrics:[{key,label,unit,left,right,delta}],curves:{label,left:[{date,nav}],right:[{date,nav}]}}`。left/right result仅有当前kind需要的有界摘要：metadata、params、metrics/IC、factor_policies，完整trades不重复返回。所有结果与数量上限需typed校验。

- [x] 先红：user/mode/未完成/同id/混kind；相同输入数据指纹与方法/流程/区间才 `comparable=true`；不同版本仍可并列读但不计算delta，显式warning，不称A/B因果结论。
- [x] 百分比delta为百分点，比例/IC/夏普delta为原单位；未知值null、非有限拒绝或明确旧值不可比较，绝不当0。没有基准曲线，不伪造“超额”。
- [x] 前端历史页可选两份完成任务（跨分页保留同scope选择、最多2、可清空），显示保存参数/版本差异、指标并列表及各自NAV；不可比时明确原因不绘制假共同基准，不自动创建任务。
- [x] 真实PG/HTTP同版本与不同版本结果、权限；Node真实事件触发与迟到比较响应隔离。

## B04-3 历史配置回填新研究（主执行者）

**Files:** 新 `components/factor-research/research-restore-panel.tsx`、`lib/stock-research-restore.ts`（如有必要）及tests；在历史报告提供 `/factor-research?restore=UUID`；工作区经真实 `getStockResearchJob` 读取本人同盘参数。

- [x] 先红：有效UUID/非法/其他用户404、加载失败、换号/换scope迟到、已有运行任务时不可覆盖、旧因子已失效、不合法schema不回填。
- [x] 显示来源job/method/参数，与当前方法区别提示；用户明确点击“应用到新研究表单”才回填，不能自动提交/启动，不复用原幂等键；原历史结果不改。
- [x] 所有日期、因子、horizon/groups、预处理、周/月、持股数、权重、费滑完整恢复并执行现有coverage预检；新建仍需用户按原提交按钮，服务器严格schema和当前目录再次校验。
- [x] 工作区不再把同一个已保存job的轮询响应重复覆盖用户编辑；配置恢复不能突破已有pending/running/未知提交状态的幂等保护。

## 分工、验证与回退

- 后端报告分工独占新report service/router/schema/tests，不改main、旧job API/service或frontend；主执行者负责所有前端、新router注册、真实PG和集成回归。
- [ ] 每包先业务red→最小实现→green，最后全后端/全部Node/TSC、独立复核，必要浏览器实际点击（若未完成明确列出）。**代码/全回归/浏览器已执行；双域独立复核未全部完成，见交付记录，不勾假完成。**
- [x] 独立PG schema下实际任务完成/重开/下载/对照，跨用户/盘拒绝，结束清理；不把fixture当真实行情或本批当全系统认证。
- [x] 保存交付记录、实际文件/命令/通过失败/未执行/金融单位/兼容/成本/人工步骤；主计划父包R06/R07等保持未整体完成。
- 回退可隐藏新入口和撤回只读router，旧result/params未变，数据库无迁移；原后端安全发布仍受P01阻塞，不凭本机测试重启。

下一轮保持四包：S04完整持久确认、P01安全发布、R02旧研究入口归一、D01盘后数据状态。题目二报告侧新缺口有明确证据再增包，不随意增加未接通菜单。

## B04-4 用户要求完整运行后的安全启动切片（追加）

用户追加“项目完整运行一下”。实际前端3000/后端8002/专用PG5433均停止；旧main启动会更新任务、重分类行情并DROP旧列，不能直接启动该路径。

- [x] 先红：main lifespan不得调用旧_init_tables；只读schema检查发现缺表/缺列必须失败且不执行DDL/DML，已有数据保持不变。
- [x] 只替换启动入口为已注册ORM的只读表/列存在检查，PG事务read_only/statement_timeout/lock_timeout有界；旧兼容迁移代码保留但不自动执行。没有DDL/数据迁移，新库仍需后续正式版本化基线迁移，不自动create_all。
- [x] 使用现有Windows隐藏启动器启动专用PG、Redis、后端、行情桥和前端；只复用核实身份进程，不强杀端口占用者，不显示秘密。研究/归档迁移继续只check。
- [x] 真实PG只读校验、启动日志、HTTP健康/匿名权限/新报告路由登记和实际浏览器页面；明确行情缺失、授权或外部服务阻塞，不把服务运行称全功能完成。
- [x] P01仅启动止损已完成，不把所有遗留DDL迁移/发布回退/新库初始化标完成。代码回退不得重新启用自动破坏性启动；失败时保留库并修前向兼容。
