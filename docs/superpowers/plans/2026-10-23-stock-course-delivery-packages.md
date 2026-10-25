# 第四轮：扩展实验与正式验收五工作包

> **For agentic workers:** 使用executing-plans，当前五包均未实施；先完成依赖包。停机期间不启服务或写实库，不把用户要求做全功能解释为可绕权限/预算/正式验收。

**Goal:** 交付正交合成、行业轮动、敏感性研究和可复现课程报告，并实测五年性能/发布回退。
**Architecture:** 统一使用D4固定数据、R1统计、R3执行/R4基准和D4有界任务；实验不复制第二套金融引擎，不因不同实验提供不同成本口径。
**Tech Stack:** 原PG/Parquet/NumPy/React；不为扩展新增分布式训练、模型服务或可执行用户脚本。

**发布顺序修订：** E1–E3仍在完整交付范围，但不能阻塞E4/E5的CORE验收。E4/E5分别跟踪CORE、CORE_ML、FULL；任何一个子范围通过只解锁其实际已完成入口和报告，不勾选整包完成。ML仍必做；只有FULL全部通过才报告总计划完成。

## E1 因子正交化与可解释合成

**文件：** 新建`backend/app/services/stock_research_v2/composites.py`、`backend/app/schemas/stock_factor_composite.py`、`backend/tests/test_stock_factor_composites.py`、`frontend/src/components/factor-research/stock-composite-editor.tsx`及测试；定义版本保存在R2已有自定义因子表的已声明kind=custom|composite契约（R2建表时包含该受校验字段，不能后加无迁移列）。

**契约：** `CompositeSpec{factors:list[FactorRef],directions,method:weighted_sum|sequential_residual,weights,order,preprocess_version}`；`build_composite(spec,decision_panel)->FactorValues`。初版只确定性人工权重与按保存顺序逐截面回归正交；权重归一化绝对值和>0、系数/顺序显式。数据驱动学权重必须作为M2训练产物，不能用全区间IC挑最优权重再回测同区间。

- [ ] 红：共线/样本不足拒绝或明确缺值，正交残差与前序暴露内积在数值容差内；顺序变化产生新版本；原正方向/负方向和PE/PB资格掩码不能被其他分数恢复；缺版本不自动换最新。
- [ ] `python -m pytest backend/tests/test_stock_factor_composites.py -q`红后实现确定性OLS和规范化保存；复用R1中性化可识别性原则，保留非有限/排除原因，不用全部股票未来数据估计权重。
- [ ] editor保存→FactorRef引用→R1 IC→R3回测→历史恢复，模型/复合因子内含引用环、深度/节点超预算拒绝；权限和幂等复用R2，不能因合成开放任意表达式执行。
- [ ] 原HTTP/私有PG/组件/TSC和真实固定样本验证，结果含原始相关性/正交后相关性/权重/顺序；未证明盈利就明确样本结果，不包装稳定策略。

**回退：** 关闭composite新建，历史版本仍可解释；原内置及custom版本保持。

## E2 历史行业轮动

**文件：** 新建`backend/app/schemas/stock_industry_rotation.py`、`backend/app/services/stock_research_v2/industry_rotation.py`、`backend/tests/test_stock_industry_rotation.py`、`frontend/src/components/factor-research/stock-industry-rotation.tsx`及测试；股票v2实验任务参数增加受Schema限制的rotation类型。

**契约：** `RotationSpec{dataset:DatasetRef,industry_version,score_factors:list[FactorRef],top_industries,top_n_per_industry,industry_weight:equal|mktcap,rebalance:week|month,PortfolioRequest}`；`industry_signals(spec,decision)->IndustrySelection`保存当时成员/聚合方式/样本数和分数，行业内等权或市值加权必须明确，不按未来收益排名行业。

- [ ] 红：历史行业迁移边界、未来上市/退市成员、单行业样本不足、缺分类不回填当前行业、不在次日可交易性已知后补挑行业、重复证券权重合并、行业与个股上限/现金约束。
- [ ] `python -m pytest backend/tests/test_stock_industry_rotation.py -q`；实现D3 historical membership→当日因子聚合→固定行业选择→R3订单/费用→R4基准，缺历史行业证据时qualification失败，不用当前分类跑五年假轮动。
- [ ] 页面显示每期行业分数/历史成员/目标行业权重与实际权重、拒单/偏离原因、基准。保存全部参数版本和实验输入，不直接操作账户。
- [ ] 私有PG/原HTTP/本人只读真实样本轮动报告及无未来函数反例通过后才开放；数据不足保持隐藏入口和明确依赖状态。

**回退：** 禁止新rotation实验，保留已存版本和交易产物，不改通用行情板块/期货策略。

## E3 有界参数敏感性与实验登记

**文件：** 新建`backend/migrations/20261023_019_stock_experiments.sql`、`backend/scripts/migrate_stock_experiments.py`、`backend/app/models/stock_experiment.py`、`backend/app/services/stock_research_v2/experiments.py`、`backend/tests/test_stock_experiments.py`、`frontend/src/components/factor-research/stock-sensitivity-panel.tsx`及测试。

**表/契约：** 实验组owner/mode/固定数据/冻结候选清单/父子任务/选择规则/指标摘要/取消状态。`ExperimentSpec{base_request,grid:dict[allowed_param,list[value]],selection:validation_only,budget}`；候选允许持股数/周月调仓/手续费/滑点/正交顺序/已登记ML参数。单实验初始≤20候选、同时执行≤1、总时限≤1800秒，单ML候选仍受M2上限；提交前计算笛卡尔数量和最坏资源预算，超出422，不把总次数当每日用户额度。

传统因子网格只依赖R3/R4，不要求先装LightGBM；只有包含ML参数/模型的网格依赖M2/M3。按前端已选实验类型显示参数，不将训练参数混进普通因子回测。预算为默认安全初值，目标机测量后可按版本调整；未测量前不可当保证时长。

- [ ] 红：21候选拒绝且无子任务；同键双提交一个实验；父取消后不发新子任务，运行子任务取消/CAS；跨用户/盘读/取消404；输入同数据/参数可复用既有结果但不能越权或跨方法复用。
- [ ] 红：最终test不能用来选择最好参数再报告未见测试；每个试验包括失败项，缺结果不能当0收益排序；只根据验证段选择，test只用于最终报告。未做统计校正时明确多重比较风险，不捏造稳健性。
- [ ] `python -m pytest backend/tests/test_stock_experiments.py -q`，红后019 migration/check和D4 parent/child幂等调度实现。网格不在HTTP线程同步循环，不无上限发训练；机器学习候选总数计入父预算。
- [ ] UI展示全部候选/热力图/费用和失败原因/选择规则；真实PG两worker竞态、取消/恢复、预算、权限、整组报告落库与前端刷新恢复验证。不能只画一张预设热力图。

**回退：** 停收新实验/取消本组任务，不删共享研究结果；019表/历史实验保留，旧普通任务不受实验取消影响。

## E4 完整明细、图表和课程交付报告

**文件：** 新建`backend/app/services/stock_research_v2/reporting.py`、`backend/app/api/stock_research_v2_reports.py`、`backend/tests/test_stock_reports_v2.py`、`frontend/src/lib/stock-reports-v2-api.ts`及测试、`frontend/src/components/factor-research/stock-reports-v2.tsx`及测试；新增`docs/STOCK-FACTOR-DEFINITIONS-V2.md`、`docs/STOCK-COURSE-EXPERIMENT-REPORT.md`。不把未经验证的实验结论写进正式报告。

**契约：** GET `/jobs/{id}/trades?cursor&limit`读取R3完整产物游标分页（默认20/max100）；GET `/reports/{id}`返回输入/数据/方法/模型/基准/成本/限制与结果引用；显式导出manifest/JSON/CSV及可打印HTML，服务控制输出大小，文件下载验证本人scope并使用服务器指定路径，CSV单元格防公式注入。不要求额外PDF服务。

报告范围：CORE含至少两组真实传统因子及基准/方法/偏差核验，R4后即可实际交付；CORE_ML在M4后加训练/验证/测试和真实ML对照；FULL增加E1/E2/E3全部实验。尚未完成的报告部分不显示伪造图/数值；导出manifest带`acceptance_scope`和实际可用部分，不能用CORE报告冒充ML交付。

- [ ] 红：超过500笔也能完整分页且不重/漏；产物版本变更409，跨scope404，超限参数422，非法游标/路径/hash拒绝，用户名称`=...`导出不执行公式；旧v1 truncated报告不能伪装恢复完整成交。
- [ ] `python -m pytest backend/tests/test_stock_reports_v2.py -q`红后实现服务/严格Zod/完整交易、拒单、费用、持仓明细；图表负收益真零轴、IC衰减真实点/缺值、组合与指数对齐，缺值不画0。
- [ ] 真实保存同数据/同池/同费用至少两组传统因子（建议价值质量与动量低波，实际结果可负），再加一个ML样本外对照；固定输入产物/版本/参数及选择过程，包含九因子定义、公式、来源/单位/方向/缺值/经济含义、有效性图和偏差陷阱。
- [ ] 报告必须给IC/分组单调性的统计或明确无单调结论、净值/指标/成交成本/基准差异/真实样本覆盖、训练验证测试切分与失败实验；API不存在时不引用假链接。用户允许后浏览器打印/导出、PG/HTTP权限和历史兼容验收。

**回退：** 新报告入口可关闭，原历史/收藏/JSON/CSV保持；不得为扩大导出删除原大小/权限保护。

## E5 五年性能、复现、发布与回退总验收

**文件：** 新建`scripts/verify-stock-course-acceptance.py`、`backend/tests/test_stock_course_acceptance.py`、`docs/STOCK-COURSE-ACCEPTANCE.md`、`docs/STOCK-DEPLOYMENT-V2.md`、`docs/STOCK-ROLLBACK-V2.md`。允许独立兼容设计后调整股票worker启动接线`backend/app/services/stock_research_worker.py`；如需compose/共享配置必须单独列出精确hunk、老期货调用影响、回退和回归，默认仍禁止，不因本计划自动授权替换主进程配置。

**验收场景：** 固定真实300股、完整五年输出区间、周/月与9因子/预处理/三权重至少覆盖代表性组合；有历史日历/价量/估值/财报/成分/退市/基准资格，不用61日扩复制。ML训练单独列资源，不偷换成必须5分钟训练；课程5分钟针对单次本地回测。

CORE验收仅要求该范围已启用的迁移/worker/功能完整及正式运行边界通过，未实施ML/扩展必须保持不可用，不能为等它们拖延核心报告；CORE_ML和FULL依次补齐。每个范围都要求真实数据、权限、金融、发布和人工证据，不是演示放行。完整17包与续做自动化停止仍仅看FULL。

- [ ] 编写验收manifest核验测试，缺任一资格/版本/真实日期行/固定成员政策必须失败。`python -m pytest backend/tests/test_stock_course_acceptance.py -q`先红，再实现显式读验收产物的验证器，禁止预置PASS或仅检查HTTP200。
- [ ] 在记录CPU/内存/磁盘/OS/依赖/并发/缓存状态的同一目标机测冷/热本地读，完整IO→计算→结果落库/产物3次，每次≤300秒、无OOM/交换失控/跨任务资源泄漏。初次供应商下载另记，外部成本不隐藏。超时则保留失败/剖析查询、row group、分块/子进程内存，不盲增容量/删安全检查。
- [ ] 两次独立重算固定数据/参数/方法结果按总包容差一致，不能只命中旧结果；ML同环境固定seed/线程/依赖两次预测/模型指标核对，跨硬件/库版本不一致标明限制。比较引用独立参考和真实指数，统计误差与市场表现差异分开。
- [ ] 真实PG扩展迁移全顺序apply/check/重复执行/校验和错/锁超时/旧版本读；新worker schema未就绪不接单、重启/取消/断网/费用超限恢复明确，旧研究/期货保护hash/认证权限/模拟资金/订单/AI确认/因子实验室关键回归。付费AI任务仍paused/user；不通过无限模型探活验证股票ML。
- [ ] 前端完整build/TSC/组件/本人浏览器：固定数据→内置/自定义→预处理→IC→组合/基准→ML→对比/历史/下载；两个用户/盘/市场切换不能串数据。股票v2后台入口接线及新capability升级必须有操作者、审计、退回路径，不改期货入口。
- [ ] 演练关闭新capability/停收新任务→等待或受控取消→切旧股票入口→旧结果仍可读，保留兼容表/产物；还原精确hunk且期货运行验证通过。未获启动许可/没有本人登录/缺真实数据或运行环境时，发布验收明确受阻，不虚构通过。

**完成定义：** 总包17包的实现/契约/权限/金融/迁移/回退/测试/真实数据/人工验收均闭合，课程四项验收逐项附证据，无已知阻断才可说完整完成。只有届时按用户约定停止本对话“A股系统续做”续做自动化；计划编写、单元测试通过或本地启动不是停止条件。
