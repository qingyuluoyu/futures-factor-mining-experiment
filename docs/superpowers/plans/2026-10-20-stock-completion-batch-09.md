# B09 研究入口兼容归一执行计划

> 使用executing-plans/TDD顺序执行三包；无其他执行者在工作，不提交/建分支，不改B08有效快照，不新增来源或模型调用。

**Goal:** 为旧研究入口提供显式异步协商，复用现有任务队列/预算/权限/幂等，保留未协商旧客户端200契约；允许在消费者迁移后明确关闭旧同步计算。不声称R02已全量迁完。

**Architecture:** 复用FastAPI、现有ResearchJobService/worker/002迁移，不新增任务表或后台执行器。通过Prefer: respond-async和Idempotency-Key选择202；没有该偏好继续兼容200，不暗改返回类型。仅对新建任务检查worker健康，同key对账不应依赖worker仍在运行。

**Tech Stack:** Python/pytest/PG/Redis、现有Next客户端，不替换框架或依赖。

**状态（07:08）：** 三包已实现并通过本机第二层验证，完整交付见 `docs/STOCK-COMPLETION-BATCH-09-VERIFICATION-2026-10-20.md`。独立复查发现嵌套连接P2已修复并由真实单连接PG并发验证；修正后独立二次复核遇额度中断，发布前仍待补齐。生产构建/浏览器人工验收未执行，R02整体和主计划不打完成勾。

## B09-1 任务幂等对账独立于执行器健康
- [x] 阅读service create/queue_guard/各router调用、worker健康和API测试；先红重现已有任务在worker离线后同key返回503。
- [x] 在同一事务/队列锁内先校验既有owner+JWT盘+key+参数hash；相同请求返回既有任务，冲突409；仅新请求且不接单返回503，不插入任务。不做重试/重新执行。
- [x] 真实service/API测试正常/离线/冲突/不同盘/用户，无权限放宽，无金融计算改法。

## B09-2 旧入口显式异步协商
- [x] 盘点所有旧API调用方（frontend仅遗留未使用导出、CLI及测试；当前UI已用/jobs），新增可选Prefer/Idempotency-Key契约，非法协商/缺key 422。
- [x] 对旧analysis/backtest在显式respond-async时转换已校验参数到严格CreateResearchJob，复用现有readiness/service/提交/commit/日志路径，202+Preference-Applied+Location。不加载行情、不线程计算；相同key与新端点互认，单用户活动槽/全局预算/取消/报告完全相同。
- [x] legacy无协商仍200和原结果；未就绪/匿名/冻结/错误市场拒绝；异常不暗降级同步，无模型/来源调用。

## B09-3 可控旧同步退役及链路验收
- [x] 新stock_legacy_sync_enabled默认true保留旧客户端；明确false时未协商请求503+迁移链接+追踪，在加载行情之前拒绝；显式异步仍可用。提供弃用/alternate链接，不任意虚构Sunset日期。availability如实披露，不自动更改真实.env。
- [x] 真实隔离PG/JWT/HTTP：两个入口同key同任务、跨入口冲突、worker离线对账/不新建、并发槽、用户/盘隔离、取消及结果读取；执行受影响和完整后端、前端回归/TSC，清理私有schema/key。最终后端2519/3既有skip、Node449/TSC0、PG67通过。
- [x] 保存真实文件/命令/红绿与失败/迁移回退/0探针成本/人工待验收/剩余风险和下一包。07:06安全预检后重载，07:07HTTP/OpenAPI确认；生产构建及本轮浏览器未执行。

旧同步兼容路径还存在时R02只记部分完成；GP、真期货研究、S04/P01、13缺失/19未匹配、PIT/五年仍是后续任务。本轮不重复供应商诊断/恢复，不擅自启用catchup。
