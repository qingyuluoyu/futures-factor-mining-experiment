# R4独立指数及组合比较计算：2026-10-25

状态：**已实现待验证**。本轮追加第5包，提供严格契约及内部计算，未认证真实指数来源或完成R4课程验收；未挂生产任务/API/UI/Flag，未连接PG、启动服务、迁移、调用金融数据或付费模型。

## 新增文件与接口

- `backend/app/schemas/stock_benchmark_v2.py`：指数、基准和两策略报告契约。
- `backend/app/services/stock_research_v2/benchmark.py`：`compare_to_index(strategy, index)`、`run_equal_weight_buy_hold(request, panels, initial_signal=None)`。
- `backend/app/services/stock_research_v2/comparison.py`：`compare_strategies(left, right)`、`save_comparison(report, ArtifactStore)`。
- `backend/tests/test_stock_benchmark_v2.py`、`backend/tests/test_stock_comparison_v2.py`。

冻结R3全部9个源码/测试SHA未变，见 `logs/stock-parallel-benchmark-r3-preservation.json`；没有修改旧功能、期货、共享配置或其它执行者文件。R4自审前3个源码字节备份见 `logs/stock-parallel-benchmark-backup.json`；新增5个源码/测试最终SHA另记 `logs/stock-parallel-benchmark-final-files.json`，不替换总清单。

`BenchmarkSpec` 显式包含标准指数ID、price_index/total_return_index、总收益税前/税后再投资口径、CNY/CN_A/Asia-Shanghai/1d/点、供应商、来源/授权/方法/日历引用、数据版本、外部qualification、执行锚。当前指数ID语法仅支持000xxx.SH/399xxx.SZ，不宣称支持所有指数体系；ID语法和引用字符串均不能代替身份/来源验证。指数价格口径和全收益口径不能混填，空白引用亦拒绝。

`IndexSeries` 提供同一来源版本有序唯一的每日open/close；日期复用explicit_date，不猜epoch或截断datetime；价格有限且为正，bool/NaN/Inf/0/负数拒绝。compare_to_index严格要求日期列表与完整PortfolioResult逐项一致、同日历引用，不取内连接、不前填、不填价格。缺一天、缺任一收盘、缺首次可执行日开盘或未知口径均失败。

## 金融窗口与比较含义

R3区间首日D为现金且收盘后形成决策。指数也首日NAV=1，从D+1原价开盘指数点数锚定，随后每日NAV=当日收盘点数/该开盘点数。这样不会把D收盘至D+1开盘的指数变动算进首次只能D+1交易的策略比较。锚点开盘缺失不降级成前收盘。区间首次非现金NAV的外部组合不能套此窗口。

策略及指数均使用股票v2同244年化、同年无风险收益到日换算、样本标准差ddof=1的metrics；策略指标从每日真实资产/净值/收益重算，检验cash+position+receivable=equity及NAV/收益链一致，不直接信任报告中缓存的数字。R3首次费用保留在对应执行日。

每日日表明确区分：`return_difference` 为两个累计净值之差，即累计收益的算术差；`relative_wealth_return` 为两个净值比减1。跟踪误差基于两者日收益算术差的样本标准差×sqrt(244)，信息比率使用该日差均值/样本标准差×sqrt(244)。相同策略的主动波动为0，信息比率为null并给原因，不输出无穷大。

指数不是可直接成交的有费用资产。价格指数排除分红，策略按明确净额事件保留分红/应收；全收益指数的再投资/税务假设也可能不同于策略现金账本。因此报告永久保留`economic_excess_available=false`、明确口径标签与警示；数值差可作描述，不能解释成可交易总收益超额。source_qualification只是上游输入记录，即使填verified，报告仍只为qa_only或calculation_only，不凭引用自动升级认证。

## 真实费用等权买持及两策略比较

`run_equal_weight_buy_hold` 从区间首日明确历史股票池候选生成等权目标，支持1至300个成员；将初始成员分数归一为相同值，避免按因子排名再次截断/筛选。只生成一次初始explicit决策，保留同数据、Scope、初始现金、费用、事件和日历，直接调用冻结R3 `simulate_portfolio`：次日停牌仍留在目标中并拒单，不补选，不按未来幸存者删池。真实费用、量限制、T+1、余股、应收/退市和缺证据边界均由同R3执行器处理，没有第二个零费用交易引擎。

`compare_strategies` 要求两端都completed且没有blockers、相同owner/盘、完整DatasetRef、pool_version、证据、实际日期/起止区间、初始现金、费用参数/版本、执行版本、metric方法、无风险收益和QA口径。错配时明确失败，不以归一化曲线掩盖资本或费用条件变化。允许因子/模型候选、top_n、调仓、权重不同，完整保留左右参数和输入/内容SHA供解释。

报告保存所有参数、指标、完整比较日表、来源方法/授权引用、实际指数点数及内容SHA。`save_comparison` 复用既有原子内容寻址JSON ArtifactStore，确定性保存且不截断明细。私有DB归属/任务登记仍由后续现有Archive/业务事务接线负责，不能直接把内部StoredArtifact当授权URL。超过现有产物预算明确失败，尚未实现大比较报告分区分页。

## 实际验证、失败与QA产物

在backend目录执行：

```text
.venv/Scripts/python.exe -X utf8 -m pytest tests/test_stock_benchmark_v2.py tests/test_stock_comparison_v2.py -q
.venv/Scripts/python.exe -X utf8 -m pytest tests/test_stock_benchmark_v2.py tests/test_stock_comparison_v2.py tests/test_stock_costs_v2.py tests/test_stock_execution_v2.py tests/test_stock_portfolio_v2.py tests/test_stock_corporate_actions_v2.py tests/test_stock_metrics_v2.py tests/test_stock_research_artifact_store.py -q
```

初次新功能20红（缺新实现），实现后20绿；一次自审补5个用例，空白证据和不等初始现金2红后修复。最终**95通过、0失败、0跳过，0.84秒**，含25个R4目标、54个R3目标及指标/产物回归。真实输出见 `logs/stock-parallel-benchmark-red.log`、`...-first-green.log`、`...-selfreview-red.log`、`...-final-regression.log`。

额外通过Python stdin调用实际R3、指数及两策略比较，使用ArtifactStore保存3份完整QA内容，清单 `logs/stock-parallel-benchmark-qa-study/qa-manifest.json`，输出 `logs/stock-parallel-benchmark-qa-save.log`。其中均显式qa_only且real_market_data=false，不能作为真实A股报告或课程验收。首次手工QA保存路径使用Path.absolute保留`..`，触发既有ArtifactStore路径规范门禁；调用端改用Path.resolve后保存成功，没有放宽存储安全检查或修改R3。

未执行：真实指数/行情来源与许可证认证、真实PIT/成分/公司行动、独立手算实现对真实原价样本对账、原PG/任务登记/HTTP/UI/本人验收、五年容量、生产构建/部署/回退。未新增第三方依赖，金融数据与模型调用费用0。

## 人工路径与后续四包

可在上述新测试用明确QA夹具手算：策略1万元、首日现金1、次日R3建仓扣费用；指数次日open=110/close=121，应为1.1而非1.21；最后close=115.5为1.05。等权两只10元股各买400股，各收5.04费用，最终资产9989.92；与单股策略9994.91的净值差0.000499。删除指数一日或anchor open须失败，不能出现缩区间比较。本人在线验收入口本轮未开放。

兼容影响为新增内部模块，无旧API、账户、DB或迁移变化。回退仅停止未来新调用，保留已写报告/历史；没有伪造已上线回滚步骤。

下一四包：①可信授权指数及原价/事件数据适配和日期/口径资格；②冻结真实小样本的独立参考实现与资金/费用逐笔对账；③接现有私有持久任务/报告登记、取消预算和完整课程比较页面；④按运行授权执行原PG/HTTP/本人及五年容量验收，未闭合前生产能力继续关闭。
