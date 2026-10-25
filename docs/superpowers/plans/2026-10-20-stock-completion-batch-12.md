# B12 A股研究按真实依赖预检和加载 Implementation Plan

> **For agentic workers:** 使用executing-plans/TDD在当前目录顺序实施四包，不建分支、不提交。复查遵循requesting-code-review。不得覆盖B11未部署代码，不中断正在运行的AI任务。

**Goal:** 修复仅选20日动量也被无关估值/财报面板挡住的研究容量问题，使表单预检、实际异步读取及保存清单使用同一依赖契约；不提高限制、不减少样本、不补价。

**Architecture:** 保留现有FastAPI/SQLAlchemy/Postgres/Pandas与异步研究worker。新增确定性DependencyPlan，价格始终完整读取所选日期和证券；仅查询/透视所需估值/财报字段，财报仍调用既有7字段择期器，不改变公告日严格滞后规则。旧默认全字段snapshot和coverage-v1保持兼容；新UI在服务明确声明selective_coverage后走coverage-v2并展示依赖，旧服务保守v1，选择变化立即废弃旧预检。

**Tech Stack:** 现有Python、pytest、SQLAlchemy、Pandas、TypeScript/React/Zod/Node test；无新增依赖或数据库迁移，无来源/业务模型调用。

## 边界与事实

- B11最新公开数据：300证券五年面板2302800格超过200万，实际预检和snapshot均无条件读取4估值+4财报；ret_20d只需要close。不是日/月次数配额，不能删除资源保护。
- snapshot的数据指纹仍非不可变档案/PIT/授权认证；本轮不改变前复权、股票池、收益/手续费/财报公式，不补缺量价、不抓未收盘。
- 市值中性化和mktcap加权必须引入total_mv，行业仍使用当前元数据并披露偏差；未知因子/重复/非法权重先拒绝，不能静默降成价格因子。
- full默认v1内容hash不变；选择性v2指纹显式绑定实际输入字段，不同因子但相同输入可比较，输入不同不能假称同版本。旧报告不修改；旧同步研究暂不优化，不伪称R02完成。

## B12-1 依赖契约与纯函数

文件：新增`backend/app/services/stock_factors/dependencies.py`、`backend/tests/test_stock_research_dependencies.py`。

- [x] 先写反例：ret_20d/vol_20d只需close；PE/PB/turnover仅相应估值字段；财务四因子只需各自字段；中性化和mktcap权重额外total_mv，行业不额外造字段。
- [x] 接口`resolve_dependencies(factor_ids=None, *, mktcap_neutral=False, weight='equal')`返回冻结类型，None表示旧全字段兼容；空/未知/重复/字符串假列表/非布尔/未知权重明确422。输出固定字段顺序，纯函数独立测试。
- [x] 共享`panel_cells(price_cells, valuation_cells, has_financial_rows)`按实际透视字段数计数，不修改现有阈值。

## B12-2 实际读取/worker和财务兼容

文件：修改`snapshots.py`、`engine.py`的_build_market_data、`stock_research_process.py`，新增/扩展依赖测试。

- [x] 红：低阈值仅动量不应被无关辅助行阻塞；市值/财报依赖不能省；加载后的所需面板与full严格相等，未选坏字段不作为本次输入；选到坏字段仍拒绝；依赖不同hash不同且同输入等价因子hash一致。
- [x] 新可选factor_ids/mktcap_neutral/weight参数；SQL仅请求选中列，其余固定7字段适配位置用明确未请求null承载、不透视不参与计算。不存在估值/财报依赖时不查询该表。全字段默认保持原行校验/哈希。
- [x] _build_market_data新增可选透视列（默认全量），不改时间对齐、缺失策略、财报frontier或公式。manifest选择性snapshot版本/实际字段/格数明确，源码row来源不冒称认证。
- [x] 异步子进程从已验证参数确定plan，不能由模型自由给列名；legacy同步接口和旧报告兼容。真实PG调用calculate验证转发，原worker子进程和重启演练仍另列未执行。

## B12-3 coverage-v2与前端闭环

文件：修改`coverage.py`、`api/stock_research_jobs.py`、`frontend/src/lib/stock-research-coverage.ts`及workspace/panel与对应测试。

- [x] 先红：预检与snapshot按相同依赖计数；v1原契约不变；新GET coverage-v2必填有界重复factor_ids、布尔mktcap_neutral及weight枚举，认证/股票市场/非法输入仍401/409/422，来源0，无DDL。
- [x] v2额外dependencies元数据，仅统计所需辅助表；估算和硬上限必须一致。返回within_limits仍不等于能成交/样本充分。
- [x] 前端旧query仍走v1，带显式因子走v2并严格Zod校验；query键绑定tab、因子、中性化和权重，任何变化立即失效旧预检并取消晚到响应，历史参数恢复亦重新预检，不自动新建。
- [x] 面板显示实际依赖与所用格数；新增交互反例，保留取消/重试/换号/日期恢复及旧数据兼容测试。

## B12-4 真实验证及检查点

文件：新增`scripts/test-stock-completion-batch-12-postgres.py`，B02报告名仅加b12；按需新增只读容量探针，不改生产数据。

- [x] 私有真实PG/JWT/HTTP：v1/v2、同事务只读、full与选择性所需面板/计算结果一致、市值和财报公告日边界/坏数据/未知因子、用户/市场/盘权限、原账本/资金不变并清理schema。
- [x] 实际本地五年只读coverage及reader耗时/进程峰值内存观测，不调用来源或写生产报告；缺价或后续金融失败如实保留，不把容量通过宣称五年回测完成。
- [x] 目标红绿、完整后端/Node/TSC、独立复查、只读服务状态；AI仍运行则不重启后端。重载前新观测AI已paused且无活动任务，才核实PID6820并安全重载为13760。新生产build/人工浏览器未执行须明示。
- [x] 保存真实文件/命令/通过失败未执行/金融口径/无迁移的回退/费用/风险及下一3—5包；核心未全验收继续自动化，不停止任务。
- [ ] 原StockResearchWorker真实子进程/机器重启和本人浏览器、完整五年无缺价结果及发布构建验收；尚未执行或仍阻塞，不能以本轮内部四包代替。

## 真实检查点

详见`docs/STOCK-COMPLETION-BATCH-12-VERIFICATION-2026-10-20.md`。目标101/后端2600通过（3既有skip、704warnings）、Node452/TSC0、私有PG70检查清理通过。实际五年只读加载2.747秒、峰值约271.5MiB；回测仍因600221/2022-09-29缺价失败，未造价/删样本。服务已安全重载，GP仍flagfalse/public008009未迁移。全系统仍部分完成，续做保留。
