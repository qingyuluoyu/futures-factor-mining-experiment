# 第三轮：A股LightGBM排序四工作包

**执行15最新：** 显式扩展/滚动多fold、样本分布/排除、真实排序模型/预测保存、独立RankIC/NDCG/分组已实现待验证；根新增私有不可变模型/预测→同R3成交账本。修复规范JSON排序后版本map误拒而不放松显式矩阵顺序。联合330/最后数据异常修复8目标通过，真实两fold512MiB Job/回收通过，全前端714通过但没有正式ML页面；详情`docs/STOCK-COURSE-EXECUTION-15-2026-10-25.md`。019模型/最终测试封存/持久任务/API/UI、经济标签/真实源/五年仍未完成；不重造fit/rolling/评价/成交函数，不把exploratory改已认证。服务停、默认依赖隔离、0外部调用不变，最新SHA用执行15accepted。

> **For agentic workers:** 使用executing-plans，遵守总包/停机/资源和期货冻结要求。机器学习是本地有界训练，不调用聊天模型替代，不把期货GP算作本包完成。执行14已部分实现M1/M2计算核及M3候选预测，整包未完成；沿下面检查点继续，不重复原生训练。

**执行14检查点：** 日期query/并列等级/缺失候选保留、标签purge、一个明确切分、真实LightGBM4.7.0原生train(objective=lambdarank)/Narwhals2.27.1锁版隔离overlay、CPU2/固定种子/验证early stopping、原生模型/内容SHA/Scope/数据/特征管线版本/知识截止/样本外预测及计算报告已实现待验证。176受影响/默认无ML旧链85通过，真实512MiB Job内小规模合成QA训练通过，1146旧主SHA不变。见`docs/STOCK-COURSE-EXECUTION-14-2026-10-25.md`。标签目前仅未认证价格收益；滚动fold、经济总收益、任务/模型DB/测试封存、可成交回测基准、API/UI/真实PG/本人/五年均未完成，不勾选整包。018现为列式，ML模型/封存迁移用未占的019；不升级默认期货依赖/配置，不重启服务。

**Goal:** 固定历史输入训练可追溯股票排序模型，接通样本外预测、模拟组合和Web结果。
**Architecture:** D4版本数据与R1特征作为输入，复用D4任务/隔离进程/产物和R3执行器；新增模型元数据，不新增训练微服务、GPU集群或自动无限调参。
**Tech Stack:** 原Python/PG/React，LightGBM本地CPU；依赖准入通过后显式锁版本。先检查既有能力/许可证、Windows Python3.12和目标部署wheel、传递依赖/漏洞/训练资源，独立记录，不在普通功能包升级框架。

官方接口核对（仅作实现依据，不代表已安装/验证）：[LGBMRanker分组接口](https://lightgbm.readthedocs.io/en/stable/pythonapi/lightgbm.LGBMRanker.html)、[CPU确定性参数](https://lightgbm.readthedocs.io/en/stable/Parameters.html)。按交易日query group组织样本，组大小之和等于样本数；CPU deterministic配固定行/列构建方式，锁定环境仍须实测，不能承诺跨系统逐位相同。

## M1 样本、标签及防泄漏切分

**文件：** 新建`backend/app/schemas/stock_ml.py`、`backend/app/services/stock_research_v2/ml_dataset.py`、`ml_split.py`、`backend/tests/test_stock_ml_dataset.py`、`test_stock_ml_split.py`；注册D4的ml_dataset任务kind，产物走D2，不复制一套认证/数据库连接。

**Schema：** `MLDatasetSpec{dataset:DatasetRef,features:list[FactorRef],label_horizon:int[1,120],train_range,validation_range,test_range,split:expanding|rolling,rolling_train_sessions,rebalance:week|month}`。每样本保存instrument_id、decision_at、label_start、label_end、原始未来收益/离散相关等级/缺失标签原因，特征只来自decision_at前可得数据。训练标签按同日未来收益排序为0–4整数，规则/并列顺序版本化；原未来收益只作标签和评估，不进特征。

**效果复审后的标签细则：** 同一未来收益不能仅因证券排序被分到不同标签；分位边界重复时允许少于5级，并保存边界/有效等级数，全同或不足2个有差异样本的query组不训练。经济标签使用R3同信号/执行窗口的总收益口径（含已知公司行动/退市结算，明确是否扣成本），标签结束按交易日历而不是“下一有价日”移动。训练可评价样本、预测候选、实际成交分别存manifest，后来缺标签/退市不能提前删掉预测候选；训练排除的原因和日期/行业分布必须公开，不能掩盖选择偏差。

默认实验按总包8.5的36/12/12月分割，实际日期、purge和样本量显式展示；不能声称五年输入产生了五年未见测试。代码用最低query组数学资格校验，另报告有效交易日数/组规模/标签分布/训练与测试缺失率，不以大量同日股票当长期独立样本证据。

**接口：** `build_ml_dataset(spec)->ArtifactRef`；`make_time_folds(samples,spec)->list[FoldManifest]`，每fold完整保存训练/验证/测试日期和样本hash、剔除原因/数量及warmup。每fold训练标签区间必须在验证起点之前结束；验证标签用于调参但不得跨最终测试起点；最终测试不能反向调参。滚动预测的每fold模型只能训练于相应决策时点已经完成标签的样本。

- [ ] 红样例：

```python
def test_training_label_must_finish_before_validation(fold_fixture):
    folds = make_time_folds(fold_fixture.samples, fold_fixture.spec)
    for fold in folds:
        assert max(row.label_end for row in fold.train) < fold.validation_start
        assert set(fold.train_ids).isdisjoint(fold.test_ids)
```

- [ ] 红：禁止随机打散日期、同证券未来样本泄漏、label作为特征、重叠20日标签污染、标准化/填充用全样本fit、训练后行业/退市名单改变旧样本、组样本数错误、标签含NaN或未来价格缺失；缺标签不能补0。并列/全同标签和组规模不足显式不训练。
- [ ] 新增`test_equal_returns_keep_equal_relevance`、`test_corporate_action_consistent_total_return_label`、`test_future_missing_label_does_not_filter_inference_candidates`；不以随机顺序分开并列收益、不用未来标签完整性筛去将来亏损/退市样本。
- [ ] `python -m pytest backend/tests/test_stock_ml_dataset.py backend/tests/test_stock_ml_split.py -q`，红后实现先时序分割/剔除重叠，再仅训练拟合需要拟合的转换。允许当前决策截面已知值的rank/zscore，但不得使用以后日期统计。
- [ ] 接真实固定D4产物时保存样本和fold manifest，不写回原行情，严格owner/mode权限、幂等/取消/超时、产物路径与hash。未到达样本最低要求给422和范围/原因，不自动缩短目标区间或用全量训练冒充样本外。

**验收/回退：** 任何未来披露/测试标签变更不改变训练特征/参数；fold只用合法历史。关闭ml_dataset新建即可回退，不影响普通因子研究。

## M2 有界CPU训练和模型版本

**文件：** 执行16已创建`backend/migrations/20261025_019_stock_ml_registry.sql`、`backend/scripts/migrate_stock_ml_registry.py`、`backend/app/models/stock_ml_registry.py`，同名Schema/service/API已接；后续正式任务及封存采用另行核定未占迁移，不覆盖019。已有、`backend/app/services/stock_research_v2/ml_train.py`、`ml_process.py`、`backend/tests/test_stock_ml_train.py`、`test_stock_ml_model_migration.py`；执行14依赖已用`backend/requirements-stock-ml.txt`及隔离target锁版验证，不再重复修改已有训练核心；正式部署先做独立兼容准入，不修改默认期货venv/pyproject/uv.lock。

**元数据：** `stock_ml_experiments`（019已实现，每实验最多5fold元数据，未取得资格认证）存owner/mode、数据/特征/标签/fold版本、训练参数/seed/库版本/运行平台、迭代/最佳验证分数、耗时/峰值资源、模型ArtifactRef/状态。时间字段分开存`created_at`（本次真实训练完成/登记时间）和`training_knowledge_cutoff`（用于训练的特征及标签已可获得的最晚时点）；历史回测不能把文件今天创建当作当时实际存在的模型，也不能因此禁止所有合法的离线历史重训。D4 task表存训练任务，不重复建一套无幂等异步表。

最终测试范围封存/消费审计尚未实现，须后续独立版本登记；019只做私有模型历史，不以字段false/哈希自行认证。以下封存需求仍需完整履行。封存身份基于同一owner/mode下底层测试数据内容、历史股票池和标签涉及时间范围；复制dataset、换名字/seed/参数不得把已消费测试伪装为新独立测试，重叠范围给污染警告。测试结果通过评估任务产生时追加事件，单纯GET历史报告不发计算/不改业务状态。测试消费后继续调参允许作为探索性实验，但独立样本外资格必须降为已见，不必禁止所有后续研究。

**契约：** `TrainingSpec{ml_dataset_artifact,fold_id,objective:lambdarank,seed,parameters,budget}`；参数白名单`num_leaves,max_depth,learning_rate,n_estimators,min_child_samples,reg_alpha,reg_lambda`，不接受用户任意LightGBM kwargs/文件路径/自定义Python目标。初始安全边界：CPU线程2、同worker训练1、每次最多5候选、树≤500、深度1–8、leaves≤min(63,2**max_depth)、运行≤600秒；预算不是用户每日次数。实际数据/主机容量不足拒绝并说明，不申请无边界内存。

模型配置固定CPU、seed与所有相关子seed、deterministic=true、force_col_wise=true、显式线程数；排序按日期/instrument稳定。原生文本模型和JSON manifest保存，禁止从用户文件pickle/joblib反序列化。early stopping仅看训练内验证段，最终测试段不能出现在训练API的eval输入。

- [ ] 红：同键同时训练只执行一次、跨用户/盘无权读模型、未锁定依赖capability=false、额外参数/过预算422、未来模型版本或缺hash拒绝、取消/超时后CPU子进程确实退出、不完整模型不能completed、租约丢失不提交结果。
- [ ] `python -m pytest backend/tests/test_stock_ml_train.py backend/tests/test_stock_ml_model_migration.py -q`，红后实现隔离子进程/无shell参数注入/资源预算/训练日志摘要，实测依赖和同环境复现。关键训练集成测试使用真实小规模LightGBM CPU训练，不用mock分数冒充模型可用；依赖缺失就不开放，也不能skip后称完成。
- [ ] 019真实PG迁移/并发/失败恢复和模型产物校验需用户允许；至少两个fold实际训练，保存最佳验证选择与未见测试边界。禁止自动用测试表现选择seed/超参数。

**验收/回退：** 可取消、有真实模型/版本/执行记录，预算可证明；跨版本模型不静默加载。关闭ml_train新接收，保留旧模型/产物和019表，回退依赖仅在没有正在运行任务且旧功能回归通过时进行。

## M3 样本外预测→选股→同规则组合回测

**文件：** 新建`backend/app/services/stock_research_v2/ml_predict.py`、`ml_evaluate.py`、`backend/tests/test_stock_ml_predict.py`、`test_stock_ml_portfolio.py`；v2 API新增POST `/models/{id}/predictions`、GET `/models/{id}`及分页模型列表，仍走Scope/幂等/任务服务。

**契约：** `predict_oos(model_ref,ml_dataset_artifact,fold)->PredictionArtifact`含模型/特征/数据版本、决策日/证券/score及fold；`evaluate_prediction(prediction,PortfolioRequest,BenchmarkSpec)->MLComparisonResult`含RankIC/分组/NDCG、R1指标、R3执行流水、R4基准与相同数据传统因子对比。score是排序分数不是概率/承诺收益。历史滚动回测依据每fold的`training_knowledge_cutoff < decision_at`约束，仅用此前可得样本离线重训；真实created_at保留今天的实际值，不能回填成历史日期。若未来开放实际前向模拟推理，还必须模型实际完成时间不晚于决策时间；不混淆两种模式。

- [ ] 红：特征顺序/单位/版本缺失409不补造；模型只接受同owner/mode合法数据；in-sample请求不能标oos；训练知识截止/标签边界晚于决策拒绝，而合法历史重训不因真实created_at在今天被误拒；改测试标签不改预测分数。加载仅来自已认证hash的本地原生产物。
- [ ] 红：高分但D+1停牌/涨停→原R3拒单/现金，不使用实际收益挑后补；预测与传统因子必须同池/区间/费用/执行/基准，不对齐则只并列警告不输出伪造可比差值。
- [ ] 跑`python -m pytest backend/tests/test_stock_ml_predict.py backend/tests/test_stock_ml_portfolio.py -q`；红后接入真实模型预测和R3/R4确定性引擎，不另写“ML特供”无成本净值，也不接账户交易接口。
- [ ] 真实固定产物两次预测+模拟结果对账，存完整预测/分组/订单/拒单/费用/模型版本。数据分组失败或部分日期无预测必须明确缺口，不把少数优胜日期拼成完整五年曲线。

**验收/回退：** 至少一轮真正样本外预测和滚动时序实验完成，收益可负；不存在必须优于基准的验收要求。禁新prediction入口可回退，旧研究/模型数据不删。

## M4 Web训练工作台、历史和固定评测

**文件：** 新建`frontend/src/lib/stock-ml-api.ts`及`.test.mjs`、`frontend/src/components/factor-research/stock-ml-workspace.tsx`及`.test.mjs`、`stock-ml-report.tsx`及`.test.mjs`、`backend/app/api/stock_ml.py`、`backend/tests/test_stock_ml_api.py`、`backend/tests/test_stock_ml_evaluation.py`、`backend/tests/fixtures/stock_ml_eval_cases.json`。股票workspace/stock v2 router挂载模块，不动共享导航/期货页面；新选项只在股票capability全部就绪后显示。

**入口与状态：** 选固定数据→特征/标签→可视化训练/验证/测试日期→预算与参数→显式提交→pending/running/阶段/取消→模型版本→样本外回测→报告/历史。刷新恢复本人任务，token/盘/市场/用户切换清除旧数据。训练提交不能由聊天文本或模型自行确认。

- [ ] 红：未知参数/模型状态矛盾/跨用户资源/取消后晚响应/并发双击/刷新重复请求不产生第二付费或训练任务；前端Zod严格解析，非有限指标/空曲线不能显示成功。
- [ ] `python -m pytest backend/tests/test_stock_ml_api.py backend/tests/test_stock_ml_evaluation.py -q`与前端三测试/TSC；固定评测集包括正确排序、反向/无效特征、缺数/过期/冲突、泄漏构造、超时/损坏模型、注入字符串/越权/超长输入。确定性计算与可选解释文本必须分离，未调用模型不能伪造token/费用。
- [ ] 页面明确样本外区间、次数/候选选择过程、分数含义、重要性不是因果、策略研究限制；同图看传统因子/ML/基准并能导出版本证据。
- [ ] 同数据/池/费用/执行/区间下至少对照单因子、等权方向合成和真实指数；显示逐滚动窗口而非仅一条累计收益，附RankIC/NDCG、换手/成本/回撤和特征重要性、未预测/未评价/拒单数量。收益差/IC弱为真实结果，不能删失败窗口或保证跑赢。
- [ ] 测试封存/已消费标记在训练和报告页可见；新增`test_cloned_dataset_does_not_reset_holdout_history`、`test_parameter_change_after_holdout_is_exploratory`、`test_report_get_does_not_trigger_evaluation`，未使用最终测试的状态必须有审计证据，而非前端布尔值。
- [ ] 用户允许后真实PG/原HTTP/本人浏览器完成一次小预算训练→预测→回测→取消异常路径和报告恢复。依赖/服务未就绪时显示明确原因，不显示假训练进度或占位收益。

**验收/回退：** 前后端及真实CPU训练的完整垂直链路，固定评测通过、无未标记泄漏。关闭股票ML能力/移除股票挂载，不影响期货AI/GP或普通A股因子；正式发布仍由E5把关。

**执行16最新：** 019实际文件为stock_ml_registry，私有模型登记/原生协议核验/GET历史API及股票显式打开library已代码接通；联合122后端、最后21目标、737前端/TSC通过，仍未应用019或启动服务/训练任务。详情和1178/33接受清单见`docs/STOCK-COURSE-EXECUTION-16-2026-10-25.md`。当前016并非通用队列，训练/组合任务须另包兼容；不再按历史占位文件重造模型CRUD/复用019号，也不把原始JSON预览算完整可视化训练工作台。
