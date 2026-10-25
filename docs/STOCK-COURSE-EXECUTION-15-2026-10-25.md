# 执行15：并行页面、真实排序、股票成交与比较的合并检查点

**总体状态：部分完成。** 本轮内部计算链已实现并通过离线验证，正式课程产品仍有未接通入口与真实验收，不能报告全系统完成。用户授权三个子agent并行，根负责数据和合并；互不覆盖文件，没有提交/建分支或启动原服务。

## 五个工作包与当前可用边界

|包|实际实现|未完成验收|
|---|---|---|
|A股页面流程|逐页矩阵覆盖20个实际main路由/页内流程；筛选409恢复清旧指纹并按已应用条件回第一页；父级刷新/取消晚到响应/后页空/offset上限/草稿摘要已修|本人浏览器/原HTTP仍未运行；不存在的ML、sim、reports独立页面没有造空路由|
|R3原价组合|D收盘候选与目标→D+1开盘撮合→整手/T+1/停牌/涨跌停/量参与率/现金/费用→公司行动与退市现金→完整账本/净值/指标/内容产物|只支持00/60主板；来源资格、全市场规则、分区账本/任务/API/UI、真实五年未通过|
|滚动机器学习|显式日历扩展/滚动fold、窗口purge/分布/排除原因→真实LightGBM→固定模型/完整预测→独立RankIC/NDCG/分组评价与内容产物|模型/最终测试封存登记、生产任务/API/UI、真实经济标签及样本外资格未完成|
|私有固定数据与合并|本人/盘登记及真实Parquet读取→明确日历网格/历史池→原披露PIT→固定因子/IC→真实模型与报告；另将不可变多fold模型/预测接同R3账本|仅显式内部QA允许观察数据计算；不升级源资格，不开放原价/事件/模型未认证链路|
|R4对照|同日严格指数比较、D+1开盘锚、244与相同无风险利率；同R3等权买持和两组策略严格Scope/数据/池/资金/费用/执行/日期对照、保存完整比较报告|尚无获授权真实指数样本/独立参考对账、正式Web接入及发布验收|

逐页矩阵：`docs/STOCK-PAGE-MATRIX-2026-10-25.md`；R3/R4细节分别见 `docs/STOCK-EXECUTION-V2-2026-10-25.md`、`docs/STOCK-BENCHMARK-V2-2026-10-25.md`；ML见 `docs/STOCK-ML-EXECUTION-15-2026-10-25.md`。不重复其完整红绿记录，也不把矩阵中的代码接线当在线通过。

## 实际文件和新契约

既有源码仅修改两个股票生产文件及各自测试：

- `frontend/src/components/market/stock-screen-results.tsx` / `.test.mjs`：不会再以旧快照指纹无限重试409，也不会把未应用草稿冒充当前查询。
- `backend/app/services/stock_research_v2/ml_train.py` / `backend/tests/test_stock_ml_train.py`：固定列顺序以明确 `feature_order` 为准；版本映射按名称集合/精确值匹配。规范JSON按键排序不再导致保存→恢复后误报特征顺序错误，缺/多/错版本仍拒绝。

新增股票生产文件15个：

- Schema：`backend/app/schemas/stock_portfolio_v2.py`、`stock_ml_experiment.py`、`stock_benchmark_v2.py`。
- `backend/app/services/stock_research_v2/`：`execution.py`、`portfolio.py`、`costs.py`、`corporate_actions.py`；`ml_split.py`、`ml_predict.py`、`ml_evaluate.py`；`study_inputs.py`、`archived_study.py`、`ml_portfolio.py`；`benchmark.py`、`comparison.py`。
- 新增测试12个：`test_stock_costs_v2.py`、`test_stock_execution_v2.py`、`test_stock_portfolio_v2.py`、`test_stock_corporate_actions_v2.py`、`test_stock_benchmark_v2.py`、`test_stock_comparison_v2.py`、`test_stock_ml_split.py`、`test_stock_ml_evaluation.py`、`test_stock_ml_predict.py`、`test_stock_study_inputs.py`、`test_stock_archived_study.py`、`test_stock_ml_portfolio.py`。

根的连接入口：

1. `load_study_inputs(ColumnarService, ColumnarRef, calendar, universe, fields, request_id)`：先走真实私有登记、原文件/Parquet SHA/Scope校验，再按传入明确日历分配有界面板。历史池版本/范围必须吻合，财报按原始披露/修订前沿择期，原价/复权/锚点/单位不混。逐日选池、不补价格/交易日/因子/财报，始终返回 `observation_only/research_enabled=false`。日历/历史池对象是算法输入，不是来源认证证据。
2. `run_archived_study(...)`：从当前Scope精确解析因子，读取上述输入并接原有 `run_experiment`，存模型、计算报告及包含日历/历史池/来源指纹的外层报告。没有正式资格登记前只接受内部显式QA，不把本地文件引用当私有DB研究结果登记。
3. `run_ml_portfolio(ArchiveService, experiment_sha256, PortfolioRequest, market, request_id)`：校验当前用户/盘/数据/池，读取实际不可变实验和原生模型，复核Scope、参数冻结、模型/预测SHA、显式日期与知识截止；不调用最终测试评价，不按未来标签/状态补选。预测完整名单转固定版本 `SignalSnapshot`，使用同一个 `simulate_portfolio`，保存所有账本。缺全日预测不能变成自动清仓信号；QA模型不能进入正式模型路径。ML市值加权尚未接固定PIT市值，明确422，不补0/1；通用R3本身有三权重。

## 金融口径和一次自审

- R3使用原价现金账本和明确事件，理论多空/模型候选不冒充成交收益。未知事件、持仓估值/因子变化缺口只保留完整日的前缀，完整metrics为null；失败当天流水不泄漏为成功结果。日期拒绝epoch/datetime截断，金额/分数拒绝bool、NaN和Inf。
- 当前费用版本有界2022-07-01至2026-10-25，其它时期拒绝；**这个区间不足以支持截至今日的完整五年课程验收**，不能绕过门禁或套当前费率。创业板、科创板、北交所及更早费用仍需独立规则/数据包。
- 因子与训练标签目前仍为明确下一收盘窗口的价格收益，缺少经认证公司行动/退市经济总收益。R3已实现经济账本不代表训练标签已自动完成该迁移；相关报告继续未认证。
- 新研究输入发现并修复财报投影错误：只投影ROE时，新一期只披露毛利率的报告原本会被丢掉，错误沿用上一期ROE。现在保留财报完整前沿，所选新期指标缺失即null，不回填旧期。
- 旧库有无公告时刻或vendor_reported口径的财务观察时，分别给脱敏业务错误 `study_disclosure_unverified` / `study_financial_basis_unverified`，不抛带行内容的原Schema异常，也不伪造披露时间。
- 规范JSON排序造成特征字典顺序变化的真实模型保存恢复问题已先红后绿；模型矩阵仍严格按明确列顺序取值，未放松版本检查。
- R4不将指数点位当可交易账户；价格指数和含分红现金账本明确并列口径与警示，`economic_excess_available=false`。缺日期/开盘锚/收盘/资金或成本不一致时不做伪差值。

## 实际验证、失败与未执行

backend目录，进程局部 `PYTHONPATH` 采用 **绝对** `logs/stock-columnar-qa`、`logs/stock-ml-qa` 和backend路径，默认期货环境未装这些依赖：

```text
.venv/Scripts/python.exe -X utf8 -m pytest <logs/stock-course-execution-15-backend-test-files.log列出的31个实际文件> -q
.venv/Scripts/python.exe -X utf8 -m pytest tests/test_stock_study_inputs.py tests/test_stock_archived_study.py tests/test_stock_ml_portfolio.py -q
```

联合回归 **330通过、0失败、0跳过，132.99秒，104条既有utcnow弃用警告**，`logs/stock-course-execution-15-backend.log`。该轮开始后新增并复现“旧财报缺披露时刻应返回业务错误”的一项用例，完成错误转换后最终三个受影响文件 **8通过、0失败、0跳过，25.09秒，32警告**，`...-final-integration.log`。330与8有重叠，不相加宣称338个独立用例，也未在最后局部修复后虚称重跑全部后端。

前端目录实际全测试命令：

```powershell
$testFiles = @(rg --files src -g '*.test.mjs')
node --experimental-strip-types --import ../logs/node-alias-register.mjs --test --test-concurrency=1 @testFiles
```

**714通过、0失败/取消/跳过，36.68秒**，`logs/stock-course-execution-15-frontend.log`；文件列表同前缀 `-frontend-test-files.log`。子agent53目标及 `node node_modules/typescript/bin/tsc --noEmit --incremental false` 已通过，证据 `logs/stock-parallel-pages-20261025/`。未重复生产构建，tsconfig及共享配置SHA保持；生成型增量缓存不作为源码改动交付。

其它子包：R3最终135通过（54新+81旧），R4最终95通过（25新+冻结R3/指标/产物）；ML首轮21通过，排序兼容修复后11目标通过，含真实两fold512MiB/4进程Job，峰值138,944,512字节、结束active=0。首次ML验证用了相对overlay路径而子进程cwd不同导致依赖不可用；改用绝对进程局部路径后同测试通过，没有改旧Job代码或放宽限制。

根红记录：数据缺模块3红；初次2失败分别为索引分辨率/frequency元数据差异、混锚点已被原归档先行拒绝。明确日频索引单位且只忽略不代表交易日真实性的freq注解，值/日期仍逐项核对；混锚点用例精确验证原归档拒绝，未绕过旧检查。后续财报投影、未核实口径、缺披露时点与ML成交缺模块均先红后绿，保留 `logs/stock-parallel-data-*.log` / `stock-parallel-ml-portfolio-*.log`。代码错误均已修；所有输入、事件、指数、授权文本均是明确QA夹具，**不是实际授权A股数据报告**。SQLite临时factory只验证离线权限/事务逻辑，不冒充原PG。

**未执行：** 原PG应用/并发/跨进程生产链、正式任务/公开API/本人浏览器、真实全市场源/历史日历/池/财报/公司行动/指数认证、300股五年<5分钟、原期货在线回归、发布/迁移/回退与本人验收。原3001/8002/8110/5433/6380均未监听，`...-runtime.json`；未启动服务/恢复paused-user AI/应用015–018。没有新增包安装、金融数据或付费模型调用；外部预算与费用0。只有受控QA短命子进程运行并回收。

## 保护清单、兼容和精确回退

**只使用接受后清单：** `logs/stock-course-execution-15-accepted-inventory.json`（1167）、`...-accepted-supplemental-sha.json`（27）及 `...-accepted-guard.log`。相对执行14的1152旧主项，仅声明ML训练与股票筛选/测试3项改变，**1149旧SHA不变**；15个股票新生产文件，无删除。原15补充项只有 `test_stock_ml_train.py` 声明改变，其他14不变，新增12测试后共27。期货和共享认证/网关/布局/运行配置未改。

早先 `...-final-inventory.json` 在最后财报异常修复前生成；重用snapshot命令被独占创建安全机制拒绝，verify准确报新 `study_inputs.py` 不同。没有覆盖旧清单或放宽guard；保留旧失败 `...-final-guard.log`，按执行14旧基线重新核对3项声明及新文件后生成accepted清单，verify通过。旧final/current均非本轮续做基线。

字节备份/精确diff分域保存：`logs/stock-parallel-pages-20261025/`、`logs/stock-parallel-ml-order-fix-*`；R3、ML新文件及R4冻结SHA各有 `*-final-files.json`/`stock-parallel-ml-files.json`，根已核R3/R4清单。原文档备份 `logs/stock-course-execution-15-backups.json`；根保护断言 `...-preservation.json`。没有DB结构/原数据/旧v1历史结果变化。

回退只反向撤销上述三个旧主项和对应ML测试的精确差异，移除新股票调用/关未来Flag，先确认训练Job树结束；不覆盖用户后续修改，不删除来源/模型/历史产物，不以取消硬资源边界作为兼容方案。新文件当前未挂公开入口，无生产迁移副作用。将来开放后需独立停止接收和保留数据的回退验收。

## 人工路径和下一四包

当前可按离线测试/QA报告逐笔核现金+市值+应收=资产；A股筛选页面在获运行许可后用“应用10→翻页→草稿999→快照409→刷新”应仍按10查询。真正机器学习按钮、正式v2研究/成交报告尚未公开，不让用户去不存在入口验收。

下一轮按依赖继续：

1. 019模型/测试消费登记和私有结果/分区账本登记，兼容迁移/幂等/真实PG验证；保留018列式编号，不覆盖旧SQL。
2. 将固定数据→自定义IC/滚动训练→同执行/基准报告接016有界持久任务、取消/恢复、capability与股票API；不修改共享生命周期配置夹带启用。
3. 股票独立前端接数据选择、精确FactorRef、训练区间/状态/版本与账本/比较/历史报告，严格Schema/会话/晚响应隔离，未就绪能力明确关闭。
4. 扩大有证据规则与真实数据资格（完整五年费用、其它板块、历史源/指数/公司行动），在明确运行/调用预算下做原PG/HTTP/本人/期货回归、五年性能和发布回退验收。

原两条续做已更新为北京时间每日**12:05和17:10**，ID a-02-05/a-07-10沿用，ACTIVE；不新增重复任务。未完成整体验收前不停止它们，不声称持续运行或供应商永不失败。
