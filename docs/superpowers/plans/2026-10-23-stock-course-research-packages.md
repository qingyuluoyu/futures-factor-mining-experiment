# 第二轮：课程核心四工作包

**执行15最新：** R3主板原价D→D+1/整手/T+1/费用/量参与/持仓/公司行动账本与全产物、R4严格指数对齐/同引擎等权买持/两策略比较已实现待验证；接私有Parquet/PIT和模型候选，联合330/最后异常修复8目标通过。见`docs/STOCK-COURSE-EXECUTION-15-2026-10-25.md`、R3/R4独立说明。仅计算QA，正式资格/任务/API/UI/真实指数/五年未验收；费用2022-07-01后有界、仅00/60主板。以下复选框保留整包验收，不再沿历史“R3/R4未开始”重复实现，但也不能据新函数勾完整。最新SHA只用执行15accepted主/补充。

**执行02检查点：** R2的定义保存/新版本编辑、分页历史/软停用、失败原键恢复/会话隔离已接通股票专用前后端并通过离线目标及回归；017迁移只创建未应用，入口需用户点击才读capability，未迁移时不开放编辑。数据资格、FactorRef接IC/组合回测、真实PG并发/约束/迁移与浏览器验收均未完成，故R2复选框仍不勾选。详情`docs/STOCK-COURSE-EXECUTION-02-2026-10-23.md`；不再重复实现已存在的CRUD，但也不能把内部resolve+DSL样本计算当完整选股回测。

> **For agentic workers:** 使用executing-plans，遵守总包类型/错误/状态机和停机门禁。不把v1既有功能或计划中的预期结果当新验收。执行01后R1/R2为部分完成：R1真实页面流转/分组图/旧方法说明与独立metrics、R2内部安全解释计算已实现；新任务/数据/自定义CRUD和IC/回测接线仍未完成，R3/R4未开始。详情见`docs/STOCK-COURSE-EXECUTION-01-2026-10-23.md`，原复选框按完整验收而不是局部实现勾选。

**Goal:** 将已有研究计算补齐为支持自定义因子、真实约束和基准的可复现股票研究链路。
**Architecture:** v2与旧研究并存；纯股票指标/DSL/执行/基准服务独立，复用经验证的预处理和因子公式，不修改共享期货metrics。
**Tech Stack:** 现有Python/Pandas/NumPy、PG任务/产物、React/Zod；不增加任意表达式执行依赖。

## R1 九因子、IC及统计口径验证

执行14（已实现待验证，仅内部计算链；实际176后端/默认旧链85通过，真实LightGBM受限训练通过，详见docs/STOCK-COURSE-EXECUTION-14-2026-10-25.md）：四包①复用原九因子/预处理，新增固定版本自定义FactorRef计算与资格掩码、历史行业截面；②统一因子值接IC/IR/衰减/分组/理论多空，严格先分层再检查未来收益，不以未来缺价换成员；③按真实传入交易日轴构建带label_end的ML样本、并列标签与日期purge，预测候选不按未来标签删掉；④锁版LightGBM CPU排序/原生模型/样本外预测与组合研究运行结果。新增股票独立factor_values.py、analysis.py、ml_dataset.py、ml_train.py、research_experiment.py及类型/测试，不改旧股票/期货公式或共享配置，不接未认证源。模型用官方原生train(objective=lambdarank)而非sklearn包装（原环境没有sklearn，避免扩栈），配独立锁版overlay，不升级现有NumPy/SciPy；含真实训练测试，未做真实PG/数据资格/生产接线前保持内部关闭。不会把理论分组多空冒充可成交回测；R3/R4与M3完整账本仍按原包继续。

**文件：** 新建`backend/app/services/stock_research_v2/metrics.py`、`factor_values.py`、`analysis.py`、`backend/tests/test_stock_metrics_v2.py`、`test_stock_factor_values_v2.py`、`test_stock_analysis_v2.py`；新建`frontend/src/components/factor-research/stock-method-assumptions.tsx`及测试。只读参考原catalog/engine/METHOD和共享metrics；允许在股票`stock_research_v2.py`路由及schema增加analysis和方法接口。

**契约：** `compute_metrics(returns,nav,turnover,risk_free_annual)->MetricsV2`；`compute_factors(dataset,factors: list[FactorRef])->factor_artifact`；`analyze_v2(dataset,factor,preprocess,horizon,groups)->AnalysisResultV2`。结果记录factor_versions、244、rf年/日换算、ddof、信号/执行/收益窗口、覆盖/排除样本、IC/RankIC/IR、衰减/各组样本数/收益和理论多空限制。无效值使用null+reason，不给固定巨大夏普/Calmar。

- [ ] 红参考代码：

```python
def test_sharpe_and_volatility_share_sample_std():
    values = [0.01, -0.02, 0.03]
    result = compute_metrics(values, [1.01, 0.9898, 1.019494], [], 0.0)
    expected_std = float(np.std(values, ddof=1))
    assert result['annualized_vol'] == pytest.approx(expected_std * np.sqrt(244))
    assert result['sharpe'] == pytest.approx(np.mean(values) / expected_std * np.sqrt(244))
```

- [ ] 再写零波动/单收益/负NAV/全负收益/无回撤、年rf换算、费用进入首期收益、胜率与换手分母、全部9因子独立公式、PE/PB非法不回填、共线/缺行业中性化、前视后缀不改过去因子、分组正/负单调与并列不足样本等红测试。
- [ ] 跑`python -m pytest backend/tests/test_stock_metrics_v2.py backend/tests/test_stock_factor_values_v2.py backend/tests/test_stock_analysis_v2.py -q`；红后实现纯函数和v2方法版本，旧报告按旧方法展示，不重算/改历史。IC重叠窗口IR明确仅描述统计，不编造显著性。
- [ ] 前端显示方法/风险收益/交易日数/排除计数/零值原因，负分组柱以真实零轴和符号表示，不仅用颜色区分；旧MiniLineChart可保留，不为换框架重做图表。
- [ ] 第一层后用户允许运行时，在固定D4数据上每因子至少10个有效证券日期点独立核对，含原来源/计算输入/计算式/容差；持久任务→原HTTP→结果页全链路及旧v1回归。没有足够真实数据则只标代码完成待验收。

**回退：** v2独立模块/结果，关闭新analysis capability；v1方法及期货默认完全不变。

## R2 自定义因子安全DSL完整切片

**文件：** 新建`backend/migrations/20261023_017_stock_custom_factors.sql`、`backend/scripts/migrate_stock_custom_factors.py`、`backend/app/models/stock_custom_factor.py`、`backend/app/schemas/stock_custom_factor.py`、`backend/app/services/stock_research_v2/expressions.py`、`custom_factors.py`、`backend/tests/test_stock_factor_expressions.py`、`test_stock_custom_factors_api.py`；新建`frontend/src/lib/stock-custom-factor-api.ts`及测试、`frontend/src/components/factor-research/stock-factor-editor.tsx`及测试；股票研究workspace新增FactorRef选择，保留原固定ID入口。

**Schema：** `CustomFactorCreate{name,expression,direction,description,idempotency_key}`；保存owner/mode/id、不可变version、规范化AST/依赖/公式版本，修改产生新版本而不是覆盖被历史引用版本。删除只禁用新选用，历史仍可读。GET列表/版本分页；POST validate只解析不运行模型或写业务；保存/禁用按总包幂等和权限。

**语言：** 正式白名单字段仅D4元数据确认的9因子基础字段/close/turnover/marketcap；运算`+ - * /`、有界常量、`abs/log/clip/rank/zscore/lag/rolling_mean/rolling_std`。`lag`仅整数1–244；rolling窗口2–244，当前和过去数据；expr长度≤4096、AST节点≤128、深度≤16、引用字段≤16。禁止属性/下标任意对象/字符串代码/导入/函数定义/eval/exec/open/SQL/未来负lag。除0/非正log产缺失并记录计数，不补造有效值。

- [ ] 红样例：

```python
@pytest.mark.parametrize('expr', ['__import__("os")', 'close.__class__', 'lag(close,-1)', 'rolling_mean(close,1000000)'])
def test_untrusted_expression_never_executes(expr):
    with pytest.raises(ExpressionError):
        compile_expression(expr, allowed_fields={'close'})
```

- [ ] `compile_expression(text,allowed_fields)->CompiledFactor`提供规范化AST/hash/字段依赖/最大lookback；`evaluate_expression(compiled,panel)->FactorValues`只在有界隔离研究进程内执行，禁止用Python eval解释AST。写合法表达式与独立手算、重复版本、跨用户/盘/IDOR、并发幂等、超预算/取消等红测试。
- [ ] 跑`python -m pytest backend/tests/test_stock_factor_expressions.py backend/tests/test_stock_custom_factors_api.py -q`；实现后做017迁移静态，再授权下私有PG并发/历史引用/软禁用/前向恢复。股票FactorRef统一接入R1/R3，禁止只做编辑页不让回测引用。
- [ ] 编辑器提供字段/单位/可用期/语法和错误定位，不允许模型自动保存/执行。保存与版本显式确认；切用户/盘/市场/旧请求结果清理，历史参数恢复必须固定原版本，版本失效不得改最新。

**验收/回退：** 自定义因子保存→选择→IC→回测→重启/刷新历史引用完整，不是只有“表达式有效”。回退仅关闭custom capability，保留定义/结果/旧版本表，v1内置因子不受影响。

## R3 日线可成交组合与完整账本

**文件：** 新建`backend/app/schemas/stock_portfolio_v2.py`、`backend/app/services/stock_research_v2/execution.py`、`portfolio.py`、`costs.py`、`corporate_actions.py`、`backend/tests/test_stock_execution_v2.py`、`test_stock_portfolio_v2.py`；新建`frontend/src/components/factor-research/stock-execution-controls.tsx`及测试，v2路由增加backtest任务。只读复用现有stock_replay规则/数据检查，原模拟盘/v1研究及其账本均不改。

**输入：** `PortfolioRequest{dataset:DatasetRef,factors:list[FactorRef],pool_version,start,end,top_n,rebalance:week|month,weight:equal|mktcap|factor,initial_cash,cost_version,execution_version,risk_free_annual}`；初始资金正数/金额精度校验，无充值次数额度。`simulate_portfolio(request,panels)->PortfolioResult`记录每日现金/持仓/净值、目标/订单/拒单/成交/费用/公司行动流水及可分页ArtifactRef；权重/订单确定性排序、同数据同参数不受随机hash顺序影响。

- [ ] 红：信号D→D+1开盘；当日新买T+1不可卖；停牌不成交；一字涨停不买/一字跌停不卖；缺status/limit/原价/调整因子时资格失败而非0/1；整手/卖余股按版本规则；成交量参与率不超限且源量单位精确转换；现金不足先保留现金不透支；未来可交易性不改变D选股名单。
- [ ] 红：动态名单中的退市前持仓保留，退市现金处置有事件才结算；分红/拆股经济价值保持，不能混raw/qfq重复计算；手续费/税费按交易日期有效规则、最低佣金及逐笔金额舍入；未知规则不按当前政策回填历史。规则表依据授权文档核验，不在本计划猜具体税率。
- [ ] 跑`python -m pytest backend/tests/test_stock_execution_v2.py backend/tests/test_stock_portfolio_v2.py -q`，实现股票独立纯确定性执行和R1指标；未知缺价停止已认证结果，合法停牌若有有效估值规则可持有并标陈旧，不把任意缺价当停牌。日线无法确定排队成交的情形明确保守拒单/限制，而非伪造分钟路径。
- [ ] 接D4任务/产物：交易流水分区原子保存，不再trades[-500:]丢明细；HTTP/Schema/前端保存所有费用和拒单参数。用户允许后私有PG真实生命周期/取消/CAS、资金守恒和完整分笔对账、本人只读查看，不触发真实或模拟账户下单。

**验收/回退：** 三权重、周/月、参数更改有可解释结果；cash+position value=nav，成交量/现金/可卖量不为负。v2结果与原账户隔离；回退禁新backtest-v2任务，保留研究账本/产物，不碰期货资金和模拟账户。

## R4 可信基准、对齐和独立参考验证

**文件：** 新建`backend/app/services/stock_research_v2/benchmarks.py`、`comparisons.py`、`backend/tests/test_stock_benchmarks_v2.py`、`test_stock_reference_backtest_v2.py`；新建`frontend/src/components/factor-research/stock-benchmark-panel.tsx`及测试、`docs/STOCK-BENCHMARK-METHODS.md`。只读使用D2指数分区/D3历史成分；股票v2 Schema增加BenchmarkSpec，不改旧两报告比较。

**契约：** `BenchmarkSpec{instrument_id,version,kind:price_index|total_return_index|equal_weight_buy_hold,execution_version,cost_version}`；`compare_to_benchmark(result:ExperimentRef,benchmark:BenchmarkSpec)->ComparisonResult`返回真实对齐日期/起始资产/各自收益和超额/跟踪误差/缺口；数据不重叠或口径不相容409，不前填未知指数/把策略曲线复制当基准。

- [ ] 红：指数缺一天/版本不符/价格vs全收益差异/策略起始含费用；等权300组合不应强行等于市值加权指数；不同日历拒绝静默内连接缩区间；指数/组合使用同执行窗口。
- [ ] 独立参考实现放`backend/tests/reference_stock_buy_hold.py`，不调用R3内部计算函数；对固定原价/持仓/费用小样本手算单股和等权买持， NAV计算参考容差1e-10、展示1e-8；市场指数差异单列归因，不能指定随意收益误差门槛“验收通过”。
- [ ] 跑`python -m pytest backend/tests/test_stock_benchmarks_v2.py backend/tests/test_stock_reference_backtest_v2.py -q`红后实现；固定真实基准来源/许可证、同假设买持对账和差异报告，需要真实D4数据时停机门禁仍有效。
- [ ] 前端策略/真实指数同屏、显式价格/全收益及成本/区间，正负超额和缺口真实显示；历史结果保存基准版本和引用，不改变已保存报告。执行原报告比较/恢复等股票回归与期货保护hash。

**验收/回退：** 指数显示、参考误差与策略超额三者分开；完整来源/公式/日期可重算。新capability关闭即回退，旧报告不篡改、不额外发起模型。

## 本轮功能效果修订约束

- R1在新模块实施前列出原catalog/engine可复用纯函数及原测试；不复制没有方法变化的因子/预处理/IC实现。存在统计差异才用新版本适配，不改原期货helper。
- R2/R3/R4共用一个股票研究配置流程和已确认DatasetRef/FactorRef，前端分步骤而非各自再建一套股票池/日期/版本选择；返回编辑保留输入，换版本显式重新确认资格。
- R3推理/选股池与未来收益标签缺失集合分离；新增`test_future_missing_label_does_not_change_signal_universe`，防止未来退市/停牌结果被提前用于选股。
- R4完成后允许E4/E5的CORE范围真实验收，不等行业轮动、ML或敏感性全部完成才导出课程报告；不能因此勾选全部17包或删除ML必做要求。
- 增加一条完整用户验收：从股票池/数据覆盖选入，选因子并做IC，保留同版本参数转组合，加入真实基准，回到参数修改后生成新任务，最后比较两个保存版本。每次新建只显式用户动作触发，刷新/返回不自动计算。
