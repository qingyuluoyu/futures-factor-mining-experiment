# 股票日线可成交组合内核：2026-10-25

状态：**已实现待验证**。本子任务交付内部主板计算内核；R3完整产品、R4基准和真实数据验收未完成。未挂生产API/页面/worker，未连接PG、应用迁移、启动服务、调用金融数据源或付费模型。

## 本轮四包与文件

1. 类型契约与费用：新增 `backend/app/schemas/stock_portfolio_v2.py`、`backend/app/services/stock_research_v2/costs.py`、`backend/tests/test_stock_costs_v2.py`。
2. 次日撮合：新增同服务目录 `execution.py` 和 `backend/tests/test_stock_execution_v2.py`。
3. 组合、账本与保存：新增同服务目录 `portfolio.py` 和 `backend/tests/test_stock_portfolio_v2.py`。
4. 明确公司行动：新增同服务目录 `corporate_actions.py` 和 `backend/tests/test_stock_corporate_actions_v2.py`。

没有改旧股票回放、旧研究引擎、期货、认证、布局、运行配置、数据/ML模块或数据库。没有新依赖、Git提交或分支。子任务不更新总AGENTS/master/全局保护清单；合并清单由主执行者处理。新增文件的自审前字节备份见 `logs/stock-parallel-execution-backup.json`，最终9个源码/测试SHA见 `logs/stock-parallel-execution-final-files.json`。

## 实际数据流与集成契约

`simulate_portfolio(request: PortfolioRequest, panels: PortfolioInputs) -> PortfolioResult` 是纯函数入口，不写模拟账户，不发出交易请求。`PortfolioRequest` 固定 scope、DatasetRef、pool_version、起止日期、top_n、周/月/显式调仓、equal/mktcap/factor权重、初始现金、费用参数、版本和无风险收益；只有显式qa_only可使用观察数据做QA。正式调用至少要求DatasetRef为backtest_ready，但这个字段和证据引用仍不自行授予来源认证。正式API必须由可信数据适配器及已有资源权限路径校验，不能把用户勾选/字符串引用当认证。

`PortfolioInputs` 提供真实有序calendar、bars、signals、覆盖区间及原价/日历/状态涨跌停/公司行动/退市/历史池证据引用、明确事件。缺全局证据或费用时间范围无资格时，返回not_qualified、daily=[]、metrics=null。没有证据默认True，没有补因子1、停牌状态、成交价或退市收益0。结果永远标记qa_only或execution_calculation_only，不标稳定、已认证或可实盘。

ML或因子候选接线示例（候选必须来自D可得特征，不能按未来标签/可交易性预删）：

```python
SignalSnapshot(
    decision_date='2024-01-02',
    signal_version='fixed-model-feature-pipeline-sha',
    direction='high',
    candidates=[{'instrument_id': '000001.SZ', 'score': '0.73', 'marketcap': '123000000000'}],
)
```

缺分数的候选由模型边界明确处理，不补0。这里接收有限Decimal分数；mktcap权重要求选中成员的D市值齐备，缺失不补选。factor权重固定为方向排序后的线性排名权重n..1，不把任意负分数当负持仓或把模型分数当概率。high/low方向影响排序，分数并列按标准ID确定性排序。

周/月调仓固定为传入交易日历中每个ISO周/自然月的第一个交易日收盘，区间首日亦调仓。explicit仅使用明确传入的决策日。按D原价收盘、D资产和已知费用/滑点预算确定整手目标股数；D+1仅以传入日历下一交易日原价开盘撮合，不用D+1停牌/涨跌停/缺价改变D目标、不补选、不追单。隔夜明确公司行动或调整因子变化，保守拒绝原委托，避免用变价后行情继续错误股数订单。

每笔买入100股倍数；卖出可单独或合并卖尽全部余股，不能拆分余股；100万股上限以上目标拆确定性委托。T+1、FIFO成本基础、现金不得透支、最低佣金、卖出费用侵蚀现金、股/100股手精确转换、每证券同日累计参与率、停牌/退市/开盘不利涨跌停拒单均显式处理。只支持00xxxx.SZ/60xxxx.SH主板；其它板块明确拒绝。日成交量仅为事后参与率上界，日线不能证明开盘集合竞价容量或排队成交，因此不能当分钟路径或实盘成交认证。

输出所有targets、委托及成交/拒单、现金/持仓/费用/公司行动流水、每日现金/持仓市值/应收权益/总资产/净值/持仓批次、原股票v2 metrics。首日资本处于现金、D+1费用进入当日收益，不假设期末平仓；换手为当日实际成交名义金额除以前日资产，双边成交均计入。缺少持仓估值/调整因子、无法解释的因子变化或未知公司行动时，只保存完整日期前缀，失败当天的成交/流水不发布，完整区间metrics为null，禁止跨缺口造净值。

结果保留完整evidence、方法版本及流式规范input_sha256；bars/候选列表重排不改变语义摘要，同一输入/参数结果确定。`save_portfolio_result(result, store: ArtifactStore) -> StoredArtifact` 使用既有内容寻址原子JSON存储，保留全部ledger而非trades[-500:]。所有者/盘/数据版本保留在报告内容，跨用户报告指纹不同。该函数不进行DB私有登记；调用者必须复用已有Archive/任务权限和事务流程。产物超既有8MiB默认/64MiB最大预算会明确拒绝，尚未实现长期大账本的分区分页登记，不能声称五年产物性能通过。

## 公司行动和经济口径

`stock-explicit-corporate-events-2026-10-25.v1` 只接受可信明确事件和record/effective/settlement交易日期。record日收盘按真实持仓登记权益：

- 净现金分红：effective日形成按明确税后每股净额计算的应收，settlement日转现金；未猜股息税和支付日。
- 送转股：明确倍率增加的股份在到账前作为按真实原价收盘计量的应收股份，到账后释放；FIFO成本基础总额保持、旧取得日保留。分数股、反向合股及无法支持的持仓变动明确停止。
- 退市现金：明确事件生效日将对应持仓转换为明确现金应收，支付日入现金；有权威现金事件时后续没有市场日线也能结算。缺事件不能把股票从账本删除或当损失0。

rights/未知事件/税务不明/记录日早于本段初始账户或日期不完整不被伪装为已支持。初始账户只允许现金，没有存量持仓导入。更复杂的同时权益及税收/合并重组应在未来独立版本交付，当前不宣称全市场历史公司行动认证。事件本身和覆盖来源仍需数据层审核，算法不能仅因某条事件文本存在而证明全部历史完整。

费用版本 `stock-mainboard-costs-2026-10-25.v1` 有界支持2022-07-01至2026-10-25。佣金参数包含交易规费，避免再重复收取；过户费双向0.00001，证券卖出印花税在2023-08-28前0.001、起0.0005。各项逐笔按人民币分HALF_UP，成交额/现金为Decimal；未把未知历史政策套成当期政策，2022-07-01以前及2026-10-25以后即使零持仓也拒绝。佣金用户参数是研究假设，不是对其券商交割单认证。

官方核对资料（仅公开规则检索，金融数据/模型调用0）：

- [上交所投资及现行费用说明](https://one.sse.com.cn/onething/gptz/)，含过户费/佣金及规费包含关系。
- [中国结算大事记](https://m.chinaclear.cn/zdjs/dsj/about_dsj.shtml)，记录2022年4月28日过户费调整通知。
- [税务总局天津税率答复](https://tianjin.chinatax.gov.cn/11200000000/0300/030005/20220725150235434.shtml)，2022年证券交易税率。
- [印花税法](https://guangdong.chinatax.gov.cn/gdsw/dgsw_gkwj/2023-03/07/content_48aca236e3e046b88722793056faa9d4.shtml)，卖方单向。
- [上交所2023-08-28减半工作通知](https://www.sse.com.cn/aboutus/mediacenter/hotandd/c/c_20230827_5725662.shtml)。
- [上交所现行交易规则](https://www.sse.com.cn/lawandrules/sselawsrules2025/trade/universal/c/c_20260424_10816492.shtml)、[深交所主板投教](https://investor.sse.org.cn/knowledge/qa/t20230306_599093.html)和[余股申报规则](https://www.szse.cn/lawrules/rule/trade/current/t20150914_565054.html)。当前实现沿用已知主板整手/T+1/余股保守模型，不因引用现行规则声称核验所有历史交易细则。

## 实际验证与一次自审

在backend目录执行：

```text
.venv/Scripts/python.exe -X utf8 -m pytest tests/test_stock_costs_v2.py tests/test_stock_execution_v2.py -q
.venv/Scripts/python.exe -X utf8 -m pytest tests/test_stock_portfolio_v2.py tests/test_stock_corporate_actions_v2.py -q
.venv/Scripts/python.exe -X utf8 -m pytest tests/test_stock_costs_v2.py tests/test_stock_execution_v2.py tests/test_stock_portfolio_v2.py tests/test_stock_corporate_actions_v2.py tests/test_stock_replay_engine.py tests/test_stock_replay_data.py tests/test_stock_metrics_v2.py tests/test_stock_research_artifact_store.py -q
```

最后一条实际**135通过、0失败、0跳过，1.26秒**，见 `logs/stock-parallel-execution-final-regression.log`；54个新目标及81旧回归。所有行情、事件、证据均为明确QA夹具，不是实际A股样本或真实PG。

红绿和失败记录均保留 `logs/stock-parallel-execution-*.log`：新费用/撮合15红、新组合/事件20红；最初测试helper包导入写法错误导致2个collection错误，修为项目tests包导入后得到20个缺实现红。首轮手算佣金边界夹具16683.33元实际佣金5.004999应舍入5.00，修正测试输入16683.34验证5.005002→5.01，没有修改舍入规则凑断言。

一次自审发现并补红修复：失败日期账本原子性、隔夜公司行动原委托、完整来源摘要、DailyAccount epoch默认接受、明确退市现金事件不应依赖后续行情、typed model_copy不能绕过数量/参与率校验、未知费用区间零分配不能绕门禁。周/月调仓、三权重、方向、证据缺失、输入重排、跨用户产物和完整ledger保存均通过。日期统一复用explicit_date，拒绝epoch及datetime截断；bool/NaN/Inf不进入现金费用或分数，qa_only必须严格bool。

未执行：原PG/HTTP/worker幂等持久任务/本人浏览器/迁移应用/真实授权数据与公司行动认证/真实基准独立对账/300股五年性能/完整发布与线上回退。没有将独立计算内核、JSON产物或离线测试数量当作R3/R4全链路完成。

## 兼容、成本、人工验证与下一轮

无旧接口或DB迁移影响。默认旧功能不导入此链；回退为停止新调用/关闭未来v2能力，保留已写内容产物，不删除历史或触碰账户。额外外部金融/模型费用0，CPU计算和内容存储成本随条数增加，有现有数据/产物边界，真实大规模性能待测。

人工离线验证可运行上述4个新目标测试，并从test_stock_portfolio_v2.request/inputs获取明确QA契约，调用simulate_portfolio后逐日核对cash+position_value+receivables=equity；把D+1状态改为suspended应保持targets不变并产生拒单，把公司行动证据去掉应无曲线。本人在线路径尚未开放，不能要求用户在不存在的入口验收。

后续四包：①可信固定数据适配到本契约并复核全覆盖/PIT/市场及Scope权限；②D4/016持久任务、取消/预算与分区账本原子登记接通，不修改旧账户；③R4可信指数/同口径买持独立参考与完整课程报告；④模型候选复用本执行链、API/UI参数和结果流水接线，再按运行授权做PG/真实样本/本人验收。生产能力门禁在这些未闭合前保持关闭。
