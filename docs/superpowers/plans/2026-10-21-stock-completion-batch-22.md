# B22 股票估值与披露历史 Implementation Plan

> 使用writing-plans / executing-plans / TDD，当前会话顺序四包，逐包复查；不提交/建分支，不改期货及共享认证/网关/布局/配置。先验证B21-final 996保护，既有审查代理均已结束，没有并发实施者。

**Goal:** 将个股详情固定“最多8期财务”和单日估值，扩成有截止日期、真实分页、清楚来源限制的估值/披露历史，而不是增加空页面或新数据源。

**Architecture:** 新股票独立`GET /api/stock/market/fundamentals`，父股票门禁继承；新schema/service/router，旧stock_market.py仅import/include。同一只读可重复读事务，目录定位+分类计数+至多101行分页三个业务SQL，未来截止拒绝、记录有限值校验和错误区分。旧详情/财报/估值API和研究引擎不变。详情财务tab挂新独立组件，保留原摘要/8期兼容展示；新组件捕获当前token/用户/市场/盘，显式查询，不自动付费或下载。

**Tech Stack:** 现有Pydantic/SQLAlchemy/FastAPI、React/Zod/Node tests、真实隔离PG5433。无DDL、依赖、配置、源/模型调用、public业务写。

## 范围与金融约束

- 已核实当前stock_details只返回最多8期和最新估值，虽底层旧API可分页，用户详情入口无历史翻页/截止；本批打通这个独立股票切片。来源9/19原因和五年/PIT仍阻塞，不能把本包说成已修补数据或完成全研究。
- 仅ASCII六位代码、目录存在且交易所SSE/SZSE/BSE（标识code.exchange）；kind=financial/valuation、必填ISO as_of≤上海今天、limit1—100/offset0—100000，无额外参数。
- 财报display按report_date倒序；只显示publish非空、report≤publish≤as_of。同日公告可展示，**不是研究当日可交易许可**；研究原publish<决策日不改。原表只有每报告期一行、无修订版本，明确不能重建历史真实可得时点/PIT。
- 分类source_rows=eligible_rows+unknown_publication+invalid_dates+later_rows。unknown优先null；invalid为report>publish；其余publish>as_of才later。估值只有eligible/later，负PE/PB保留真实显示但注明不得直接当有效价值因子；null是缺值，不补0，非有限值整页失败，不静默丢弃。
- 分页固定as_of、稳定日期/id排序，但不是不可变版本，导入修订仍可导致offset漂移；明确限制，不承诺跨请求快照。最多50000单证券源行，超界422；单SQL5秒/锁2秒/接口12秒。无全表返回。

## 工作包

### B22-1 有界只读历史接口
Files: 新`backend/app/schemas/stock_fundamentals.py`、`backend/app/services/stock_fundamentals.py`、`backend/app/api/stock_fundamentals.py`，`backend/app/api/stock_market.py`只import/include；新`backend/tests/test_stock_fundamentals.py`。
- [x] 新红测：真实SQLite源表、财报未知/日期倒置/未来公告分类、同日可展示、分页并集、缺值/负值保留、坏字段失败、目录未知/非法交易所、未来/超额及严格Schema。
- [x] 实现`read_fundamentals(factory, query, *, today)`三聚合/分页SQL（目录、计数、页），核验PG只读可重复读；返回query回显/标准证券标识/分页/分类/单位/旧来源未认证/方法/时点/明确非PIT。接口身份市场/no-store/trace/超时/脱敏错误，不调用上游。
- [x] 先红后绿，旧接口测试不改断言，数据库权限/错误不降为零成功。

### B22-2 用户可操作的历史入口
Files: 新`frontend/src/lib/stock-fundamentals-api.ts`、新`frontend/src/components/market/stock-fundamentals-history.tsx`和各自tests；`stock-quote-detail.tsx`仅import/挂载新组件；原`stock-quote-detail.test.mjs`只加child stub并新入口断言。
- [x] client先红：日期/数量/分类恒等式、kind行Schema、排序唯一和截止、单位/来源/PIT/证券/query回显；捕获token/no-store/20秒/取消、不重试，404未部署/503不显示假空表。
- [x] 面板先红：默认不发请求，选择财报/估值及截止日期后显式读取，前后页每页20、空/加载/错误/重试及来源限制；参数/身份/盘/证券变化隐藏并取消旧结果，晚到不覆盖，不自动提交研究或交易。
- [x] 股票财务tab实际挂载，旧摘要保留；有限负值和null显示区别，亿元/元/百分数明示；达到offset边界停止翻页，不越界循环。

### B22-3 PG/HTTP回归及保护
Files: 新`scripts/test-stock-completion-batch-22-postgres.py`、`scripts/check-stock-b22-preservation.py`及guard测试；原PG harness仅加b22标签。
- [x] 私有schema真实PG/JWT/HTTP验证目录/金融分类/有限负值/null/分页、匿名401/期货409、参数422/unknown404/DB503、只读/no-store/trace、公表账本任务全行摘要不变及清理。不得写public序列/业务。
- [x] 目标与完整后端、全部Node、TSC、保护哈希；失败先查原因，不动冻结期货或跳过测试。独立只读review（skill明确允许）后处理明确重要问题。

### B22-4 实库只读和检查点
Files: 新`scripts/test-stock-completion-batch-22-live-readonly.py`、本计划、交付及主/保护计划。
- [x] 现有真实库挑一只已存证券，固定本地股票上下文ASGI读取财务/估值各两页，记录实际覆盖/缺失/版本局限，0源/模型/public写；不是生产JWT/运行服务验收。前后public账本/任务/目录/EOD/旧归档摘要、旧日线计数首尾样本比较。
- [x] 保存文件/真实命令/红绿失败未执行/金融口径/迁移回退/成本/人工步骤/下一四包。没有生产部署/构建/浏览器/人工双市场就仍未发布，不报告全系统完成，不删续做。

## 验证命令与回退

`backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_stock_fundamentals.py backend/tests/test_stock_b22_preservation.py -q`；新增PG/实库只读脚本；完整`pytest backend/tests -q`；frontend内全部Node与`tsc --noEmit --incremental false`；原/最终保护verify。

无数据库迁移/写回。回退只移除本包子router include与详情新面板import/挂载、新独立文件；原8期/单日摘要及原API仍可用，不整文件恢复覆盖他人改动。下一四包：来源9/19原因与预算；历史PIT/锚点/五年缺价；可信版本DSL基准/回放扩池；独立运行构建发布/双市场人工验收。
