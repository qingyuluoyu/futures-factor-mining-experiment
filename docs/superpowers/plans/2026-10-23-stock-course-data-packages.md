# 第一轮：A股可信数据四工作包

> **For agentic workers:** 使用executing-plans，逐包遵守总包和根AGENTS。复选框是完整步骤验收门禁，局部实现按最新检查点核对。停机期间不得发采集、启服务或应用迁移。

执行04更新：新增D4读取前置的有界JSON观察Panel、所选因子/精确自定义修订依赖预检，以及未生产挂载的目录API/版本查看组件。218后端/678前端及TSC通过，当前清单1113；仅既有股票archive.py有声明修改，期货/共享冻结未变。D2/D3/D4仍部分完成，JSON诊断不等于Parquet/五年覆盖；预检不授予资格，财务仍须真实日历/PIT对齐。015/017未应用、016任务/ML未实现、无源/模型调用或服务重启。详见`docs/STOCK-COURSE-EXECUTION-04-2026-10-23.md`，原完整步骤复选框不勾选。

执行03更新：D2私有产物/追加来源/固定观察清单服务、015兼容迁移及D3财报PIT/历史股票池纯逻辑已实现并离线验证；35目标与202受影响测试通过。没有真实源调用、PG应用或新数据公开入口；归档只支持已规范化daily/valuation/financial JSON，数据集强制observation_only，Parquet、真实供源认证/缺口审计、资格分层、D4任务及本人验收仍缺。D2/D3不能勾整包完成，D1保留执行01部分实现，后续不要重写已存在的archive/PIT/universe；详见`docs/STOCK-COURSE-EXECUTION-03-2026-10-23.md`。

执行01更新：D1请求/预算Schema与D2内部JSON产物存储已实现并离线验证，两个完整包仍部分完成；真实适配器/并发预算预留、DB登记/迁移/Parquet和权限链路未完成。以下复选框仍作为整步验收门禁保留，不因局部代码勾选整步；具体实际文件/命令见`docs/STOCK-COURSE-EXECUTION-01-2026-10-23.md`。

**Goal:** 给研究/ML提供真正可重新读取的历史时点数据，不再只保存当前库指纹。
**Architecture:** 保留原daily archive/v1研究；新增独立多源归档及dataset注册，Parquet为受校验的本地中间产物，不替代PG元数据。
**Tech Stack:** 原PG/SQLAlchemy/Pydantic、Pandas/NumPy；Parquet引擎锁版审查后新增，不能因本计划先安装最新版。

列式读取设计依据官方[ParquetFile接口](https://arrow.apache.org/docs/python/generated/pyarrow.parquet.ParquetFile.html)，实施时使用列裁剪/批迭代等已核验接口；文档页面版本不是本项目依赖锁定证明。初次旧Parquet总览文档链接返回404后已改用该官方API页，不以坏链接当已验证能力。

## D1 供应商契约、授权与有限预算采集

**文件：** 新建`backend/app/schemas/stock_provider_contract.py`、`backend/app/services/stock_research_v2/provider.py`、`backend/tests/test_stock_provider_contract.py`、`docs/STOCK-PROVIDER-CONTRACT.md`。只读参考`stock_eod_sync.py`、`stock_daily_archive.py`，不改原期货/共享客户端。

**契约：** `ProviderRequest(kind, instruments, start, end, cursor)`；kind为daily/valuation/financial/industry/universe/calendar/benchmark/action/status之一。`FetchBudget(max_requests,max_rows,deadline_at,max_cost,currency)`，默认max_requests=0；价格未知且无法证明在预算内时拒绝出网。`fetch_page(request,budget)->ProviderPage(rows,next_cursor,provenance,usage)`；rows先标准化再进入D2。provenance含源/接口方法/授权边界、市场、标准标识、单位、复权、采集时间；不能把Teajoin兼容接口当官方Tushare账号。

九类数据分别准入：已获得文档/授权/预算的daily可以独立规范化归档，financial缺失只阻止依赖它的动作，不阻止所有离线研究开发；未核验类保持不可用，不用其他接口推测替代。

- [ ] 先写`test_zero_budget_makes_no_network_call`、`test_timeout_consumes_attempt_and_does_not_retry_4xx`、`test_unknown_unit_and_truncated_pagination_fail_closed`。模拟恶意源文本只能作为字段，不能变工具指令。
- [ ] 跑`python -m pytest backend/tests/test_stock_provider_contract.py -q`记录真实红；再实现严格Schema、现有凭据只通过配置读取、超时/有界分页/最多2次可恢复重试（每次计预算），日志只记请求号/错误类/行数/费用状态。
- [ ] 缺接口文档时测试仅本地脱敏样例，明确未认证源Schema；不写“对接成功”。文档必须填写每类接口历史范围、字段/单位/修订/分页/交易日历、权限和费用，未提供项标阻断原因而非猜值。
- [ ] 绿后再跑原`test_stock_research_dependencies.py`；获用户预算且允许服务运行后才做一次指定证券/日期真实HTTP，记录真实请求数/成本，失败不循环。

**验收/回退：** 同输入标准化可重复，9类输入缺字段/冲突/过期/上游失败分错；源返回不可直接入计算。未授权不开放采集入口；新适配器未挂载前删除入口引用即可回退，无旧数据副作用。

## D2 不可变来源/修订归档与安全产物

**文件：** 新建`backend/migrations/20261023_015_stock_research_datasets.sql`、`backend/scripts/migrate_stock_research_datasets.py`、`backend/app/models/stock_research_dataset.py`、`backend/app/services/stock_research_v2/archive.py`、`backend/app/services/stock_research_v2/artifacts.py`、`backend/tests/test_stock_research_dataset_migration.py`、`backend/tests/test_stock_research_archive_v2.py`。参考现有bookmark迁移，不修改/复用其版本号。

**表/契约：** `stock_research_sources`追加型源批次/修订关系/源内容hash；`stock_research_datasets`记录manifest和资格；`stock_research_artifacts`记录artifact_id/所属scope或公共市场来源、根内相对路径/hash/大小/格式/写入状态。版本表专用，不能覆盖旧来源。`archive_page(page)->ArtifactRef`、`read_verified_artifact(ref,scope)->bytes`；D1输入完整通过才入归档。

- [ ] 红测试：同证券报告期不同披露/修订时间均保留；重复相同源批次幂等、同键不同hash409；路径穿越/链接逃逸/损坏hash拒绝；未登记临时产物不能被读取。
- [ ] 跑`python -m pytest backend/tests/test_stock_research_archive_v2.py backend/tests/test_stock_research_dataset_migration.py -q`；按总包015新增表/检查约束/索引/版本checksum，禁止变旧表和启动时回填。
- [ ] 实现临时文件独占写、fsync/原子rename与DB登记状态机，文件失败/DB失败可幂等修复；不接受用户绝对路径或任意URL。单产物/批次上限写Schema并单测，不把源响应全文写日志。
- [ ] 运行迁移静态/SQLite非核心逻辑测试；用户允许后用隔离PG真实apply两次/check、校验和冲突、事务失败、文件损坏、权限读取。准备`scripts/verify-stock-datasets-local.py`仅私有schema用明确QA数据，禁止写public伪行情。

**验收/回退：** 旧daily archive和研究摘要不变；跨文件/DB不一致必失败而不是放行。回退关闭新读取，保留新表/文件；孤儿修复按任务和hash归属前向收敛，不整目录删除。

## D3 历史股票池、披露前沿和数据资格

**文件：** 新建`backend/app/services/stock_research_v2/point_in_time.py`、`backend/app/services/stock_research_v2/universe.py`、`backend/app/services/stock_research_v2/qualification.py`、`backend/tests/test_stock_point_in_time_v2.py`、`backend/tests/test_stock_universe_v2.py`。元数据记录在D2分区/manifest；新增关系表需要独立迁移版本，不擅改已应用015。

**契约：** `select_asof(records,decision_at)->rows`；`universe_at(dataset,date,pool)->list[standard_id]`，pool可全A历史可用全集/中证800历史成分/固定自选清单，必须保存版本与成员生效区间。`qualify_dataset(manifest,required_fields)->QualificationReport`返回缺口、原因、每字段资格和不可使用动作；未知原因不能映射停牌。当前industry/name无历史证据时只research_only。

- [ ] 红代码样例（实现该fixture后才执行）：

```python
def test_later_revision_cannot_change_old_decision(pit_fixture):
    rows, decision = pit_fixture.before_revision()
    expected = select_asof(rows, decision)
    assert select_asof(rows + pit_fixture.later_revision(), decision) == expected
```

- [ ] 同时写退市前仍在池、退市现金处置缺证据拒绝认证、新上市不得回填上市前、行业生效日边界、公告仅日期下一交易日可用、晚修订不覆盖历史、未知证券隔离、整日缺价不当休市等失败测试；跑`python -m pytest backend/tests/test_stock_point_in_time_v2.py backend/tests/test_stock_universe_v2.py -q`看红。
- [ ] 实现按可得时间筛选后选报告前沿/当时修订，行业/成分按有效区间，严格证券标识和交易日历；方法版本/来源/排除原因完整返回，不给回测假资格。
- [ ] 绿后用D2实际归档可追溯样本做源证据对账，不能以同源两表一致声称独立真实性。没有退市/历史成分或财报修订源时，相关资格明确受阻，其他已覆盖功能可研究使用但不开放完整验收按钮。

**验收/回退：** 对未来披露/后上市/历史删样本的反例有效；查不到来源不猜。新资格逻辑仅v2，回退不降低旧结果限制或抹掉排除原因。

## D4 有界Parquet研究数据集及读取入口

2026-10-25承接UI第四轮及执行12HOLD，恢复三个股票独立包：①45秒/0重试上限获取原锁版PyArrow wheel，SHA一致后仅隔离target安装，真实列式写读/精度/缺值/损坏测试；②私有登记/回滚及读取携带已验证源元数据、缺类别字段明确失败，仍仅observation_only，不对齐成每日财报因子；③018显式迁移runner，默认只读检查、先验015完整结构/校验和，再完整反射018并事务登记。白名单为既有columnar.py及两个columnar测试，新增股票018 runner/迁移测试；期货、共享运行配置、旧迁移和015/016/017代码冻结。禁止应用迁移、启动服务、公开新入口或伪称PG通过。初次真实引擎7测试中3失败因QA观察日期晚于fixture采集时间，仅修正测试样例采集日期，保留生产时间校验。

恢复结果已实现待验证，最终87受影响后端通过。详情`docs/STOCK-COURSE-EXECUTION-12-RESUME-2026-10-25.md`，最新1145保护与补充SHA均使用`stock-course-execution-12-resume-accepted-*`；旧HOLD为历史。实际有界worker列式链/PG迁移并发/线上/本人/历史数据资格仍未闭合，D4复选框不勾满；不得因真实Parquet测试通过提升observation_only资格。

执行13范围（实施中）：三个相依包①固定claim内JSON父清单与Parquet登记同事务、双fencing、幂等jobID；②显式列式IPC复用原有输入/时间/输出/Windows Job边界，不从任意文本选择执行器，原JSON默认不变；③父级验证列式receipt的用户/盘/固定父版本/幂等jobID后才复用原finish_dataset发布与释放/取消。白名单为既有claim_execution.py/dataset_process.py/dataset_worker.py/process_supervisor.py/columnar.py，新增列式模块入口和股票管线测试。旧API/016表/工作流保持原DatasetRef结果语义，Parquet是经验证的物理表示，不据旧租约宣称列式能力；未接新的生产capability、任务生命周期和PG验收前保持关闭。零数据/模型调用、不重启或应用迁移；离线成功必须明确与真实跨进程PG分开。

执行13已实现待验证：147受影响/默认无Arrow JSON28/原生Job Parquet监督4通过，最新1146主清单及8项补充SHA、实际失败修复、资源与回退见`docs/STOCK-COURSE-EXECUTION-13-2026-10-25.md`。真实PG/生产生命周期/capability/页面及金融来源资格未验收，D4整包仍不勾选；显式列式物理表示不改变旧任务结果语义或数据资格。

**文件：** 新建`backend/app/schemas/stock_research_v2.py`（总包核心类型）、`backend/app/api/stock_research_v2.py`、`backend/app/services/stock_research_v2/datasets.py`、`backend/app/services/stock_research_v2/panels.py`、`backend/tests/test_stock_research_panels_v2.py`、`frontend/src/lib/stock-dataset-api.ts`及`.test.mjs`、`frontend/src/components/factor-research/stock-dataset-picker.tsx`及`.test.mjs`。允许修改`backend/app/api/stock_factors.py`一处include_router、`factor-research-workspace.tsx`新增v2只读入口；仅D4整链路验证后挂载。新增pyarrow或既有同类Parquet引擎须锁版本/许可证核验，不升级核心依赖。

**契约：** `build_dataset(source_refs,pool,range)->DatasetRef`、`load_panel(dataset:DatasetRef,fields,dates)->PanelBatchIterator`，按字段/日期/证券分区读，固定排序/缺值/类型/单位；暖机数据区间和实际回测区间分开进入manifest。GET `/datasets`、`/datasets/{id}`、`/availability`严格分页/权限；POST `/datasets/jobs`异步幂等建集，不在GET/普通HTTP内长计算。

**任务基础设施同包：** 新建`backend/migrations/20261023_016_stock_research_v2_jobs.sql`、`backend/scripts/migrate_stock_research_v2_jobs.py`、`backend/app/models/stock_research_v2_job.py`、`backend/app/services/stock_research_v2/jobs.py`、`backend/app/services/stock_research_v2/worker.py`、`backend/tests/test_stock_research_v2_jobs.py`。旧stock_research_jobs只允许analysis/backtest，不强改它的kind约束。新stock_research_v2_jobs按总包状态机存owner/mode/kind/input_hash/idempotency_key/request_id/lease/attempts/deadline/result_refs/error；kind为dataset/analysis/backtest/ml_dataset/ml_train/prediction/experiment，只有已注册并验证的kind才能接收。`submit_job(scope,kind,parameters,key)->job_view`、`cancel_job(scope,job_id)->job_view`及`worker.run_once()->bool`清晰接口，事务claim/CAS、租约/启动恢复、进程取消/超时不得留孤儿线程。worker不直接从HTTP启动，未发现有效worker租约时availability=false且拒新任务；本机启动和正式部署接线留E5独立兼容验收，未经许可不启服务。临时内测需显式运行该worker模块，不创建新微服务或消息基础设施。

- [ ] 红：相同产物两次读返回相同有序值和hash；原文件更改但旧版本读必须409不能自动新建；列裁剪/分批结果等价；非法字段/额外列/NaN类别/错误单位/暖机不足/用户私有范围冲突拒绝。
- [ ] `python -m pytest backend/tests/test_stock_research_panels_v2.py -q`红后实现有界iterator/Parquet内容清单、原子产物/恢复和资格页面。不得一次性materialize全部全市场面板后再检查内存。
- [ ] 单独运行`python -m pytest backend/tests/test_stock_research_v2_jobs.py -q`，覆盖两个并发claim不重复执行、同键异参409、跨用户/盘404、取消/租约过期/worker重启不重复外部采集。无权限参数不得先产生文件或启动子进程。
- [ ] 前端只允许选实际dataset及清晰资格，显示源/数据区间/缺口/版本，不自动改成最新。旧账号/盘/版本响应丢弃，失败不显示0覆盖。
- [ ] TSC/Zod/组件目标测试绿；用户允许运行后执行私有PG/原HTTP并发/权限/取消恢复、真实固定版本加载及浏览器读资格；无数据源授权时不伪造合格dataset。性能记录真实本地IO/峰值，不把超时限制当性能结果。

**验收/回退：** 指纹对应可加载的实际不可变内容，不是只给当前库打hash；v1保持。元数据表/Parquet缺失则新availability=false；回退移除一处股票挂载和入口，保留表/产物，不能破坏原目录/回测。

**功能效果补充：** 依总包8.2返回按动作/所选字段的能力矩阵，不把缺一个财务字段等同所有研究不可用。DatasetRef资格仅摘要，服务端重新解析FactorRef/中性化/权重/执行依赖；不能信客户端自报backtest_ready。新增测试`test_momentum_does_not_require_financial_fields`、`test_selected_financial_factor_missing_is_not_silently_dropped`、`test_rotation_requires_historical_industry`。数据选择器展示可用日期/股票池/缺口与下一步能做什么；用户必须确认固定版本后才把参数带入研究表单，不自动换最新。
