# A股ML执行15：滚动训练、固定预测及独立评估

**状态：已实现待验证。** 三个内部计算工作包已通过离线目标和真实原生进程验证；尚未接生产任务、模型/最终测试数据库登记、API/UI及真实金融数据资格，不能称ML产品或课程完整交付。与并行数据/组合执行器工作分别记录，不覆盖执行14证据。

## 实际修改与计算数据流

本轮仅新增七个股票文件，没有修改已有 `ml_dataset.py`、`ml_train.py`、`research_experiment.py`、期货、共享认证、运行配置、前端或数据库。

1. `backend/app/schemas/stock_ml_experiment.py`、`backend/app/services/stock_research_v2/ml_split.py`、`backend/tests/test_stock_ml_split.py`：`WalkForwardSpec`要求完整明确的交易日轴及warmup/train/validation/test session数，支持expanding/rolling，最多5个fold，每步前移一个完整测试窗口。日期轴须与样本一致；不足窗口拒绝，不生成自然日或缩短范围。复用 `split_samples` 按实际label_end purge；每fold记录完整窗口日期、暖机、样本SHA、purge数量及逐日规模/等级/排除原因。终端窗口标明terminal_holdout，仅是角色说明，**不代表封存认证**。
2. `backend/app/services/stock_research_v2/ml_predict.py`、`backend/tests/test_stock_ml_predict.py`：`run_walk_forward_experiment(samples,spec,scope,dataset,feature_versions,...)`先冻结单组参数和版本摘要，再逐fold调用已有真实 `fit_ranker`/`predict_ranker`；输出每个原生模型manifest、完整候选/无分数原因、固定Top-N。使用现有ArtifactStore保存不可变原生模型及报告；缺特征不能通过后验收益挑选替补。每fold线程仍为2，总运行预算600秒，有取消检查；生产调用仍须进入已有硬资源监督，不将内部函数冒充已挂任务worker。
3. `backend/app/services/stock_research_v2/ml_evaluate.py`、`backend/tests/test_stock_ml_evaluation.py`：训练报告默认 `evaluation=null`。`evaluate_walk_forward`是独立调用，要求当前Scope/Dataset，重建固定fold、检查参数冻结摘要、训练/验证样本SHA、特征顺序/版本、知识截止及预测SHA后评价。输出逐日RankIC、NDCG、标签分组描述、可评价天数和缺预测/缺标签/两者均缺的互斥计数；整窗口无预测/无标签保留日期并给null指标。分组先按分数选成员，任一成员缺标签则整组均值null；分数并列不按证券代码制造等级，NDCG并列采用平均gain。

分数明确为 `ranking_score_not_probability_or_expected_return`，不是概率或预期收益。`created_at`保持真实训练时间，历史重训只检查训练及验证标签知识截止早于决策日，不回填历史模型创建时间。后续fold可使用其当时已结束的先前窗口历史标签，终端测试始终不参与本次固定参数训练或early stopping；没有参数搜索。训练/评估均固定 `holdout_registry_verified=false`，评估后另写 `holdout_consumption_registered=false`，状态为 `exploratory_not_certified`，不自行认证未见测试。

当前标签口径仍由输入MLSamples决定，执行14原研究入口保持未经公司行动/退市认证的价格收益；本轮没有改金融标签或伪造经济总收益。分组结果只是标签描述，不累计成免成本净值；`portfolio_backtest.available=false`，等待同规则R3执行器和R4基准。分布含日期/等级/缺失原因，输入没有行业维度，不能声称已完成行业分布认证。

## 实际验证和失败修复

使用test-driven-development的先红后绿以及一次定向自审。新增模块前8用例因模块缺失失败，记录 `logs/stock-parallel-ml-red.log`；实现后8通过。自审补出全窗口None分数和模型训练hash篡改两个真实失败，`...-review-red.log`为2失败/5通过；修复全缺失数字列解析，并补核模型训练/验证SHA及特征顺序后，10通过。报告通过内容存储读取后再次独立评估的结果也一致。

关键命令（PowerShell；PYTHONPATH仅此进程设置，未安装/更改默认venv）：

```powershell
$taskRoot=(Get-Location).Path
$env:PYTHONPATH=(Join-Path $taskRoot 'logs/stock-ml-qa')+';'+(Join-Path $taskRoot 'backend')
backend/.venv/Scripts/python.exe -m pytest backend/tests/test_stock_ml_split.py backend/tests/test_stock_ml_evaluation.py backend/tests/test_stock_ml_predict.py backend/tests/test_stock_ml_dataset.py backend/tests/test_stock_ml_train.py backend/tests/test_stock_research_experiment.py backend/tests/test_stock_ml_bounded_process.py -q -s --basetemp=logs/stock-parallel-ml-qa-20261025-v1
```

**最终21通过，0失败/0跳过，16.20秒**，见 `logs/stock-parallel-ml-final.log`。包含真实两fold原生训练、参数/标签泄漏防护、不可变保存恢复、缺数/篡改/越权/取消，以及执行14原单fold训练报告回归。独立模型分数和SHA在最终测试标签修改后不变，不用mock分数替代训练。

新两fold链真实Windows Job峰值 **138,944,512字节**，内存限额536,870,912字节、4进程，退出后active_processes=0；旧链同轮峰值114,855,936字节并回收。QA模型/报告保存在 `logs/stock-parallel-ml-qa-20261025-v1/`，为**合成夹具，非A股真实行情**，不能作为真实市场/五年性能验收。

首次包含旧Job的回归为20通过/1失败（`...-final-regression.log`）：旧fixture子进程cwd=backend，验证命令的相对overlay路径失效；直接探测该cwd下 `find_spec('lightgbm')=None` 证实。改用绝对的进程局部PYTHONPATH后21通过（`...-final-absolute-regression.log`），未改旧fixture或生产代码规避失败。最终代码又执行上述21项并通过。

无overlay的默认venv探测LightGBM/Narwhals/PyArrow均不可import，原 `engine()`返回 `ml_engine_unavailable/503`，记录 `logs/stock-parallel-ml-default-capability.log`。新增外部行情/模型API调用均0、费用0。**未执行**原PG、迁移应用、生产队列/worker/API、HTTP/本人页面、真实金融样本/公司行动/退市资格、300股五年、完整期货在线回归或上线回退演练；没有启动服务或paid AI。本轮无数据库变更、兼容迁移或旧API变更，不用离线测试宣称这些链路已完成。

## 保护、人工复核与剩余工作

七个新增源/测试文件和本记录的逐字节快照及SHA见 `logs/stock-parallel-ml-files.json`、`logs/stock-parallel-ml-snapshots/`。没有覆盖既有文件，无Git提交/建分支。停用新增内部调用可回退；保留已生成不可变模型/报告，不删除原始或历史数据。全局旧保护SHA由主执行者统一核验，本子包不改全局清单。

人工路径：用独立overlay执行上方目标命令，打开保留的 `qa-report.json` 对照两个fold的显式交易日/purge、原生模型知识截止、600条候选及评价日期；同目录artifacts可通过ArtifactStore按SHA重新读。检查训练报告evaluation为null、独立评价含 `holdout_consumption_registered=false`、所有报告未认证且没有组合净值。此路径仅复核计算QA，不能代替本人生产页面验收。

下一四包：固定真实Parquet/PIT/历史池业务输入资格；模型与最终测试消费登记及019迁移的原PG验证；复用现有任务/租约/硬资源生命周期和API/UI接线；同数据/费用/执行/基准下ML与传统因子的R3/R4比较、真实数据与五年容量验收。任何新入口在上下游未接通前继续隐藏。

## 同日集成定向修复：版本映射键顺序

上述七新增文件冻结后，主执行者集成时发现原训练核将 `list(feature_versions)` 的字典插入顺序当作特征列顺序；ArtifactStore的规范JSON按键排序，会令非字母序特征保存恢复后错误拒绝。按其明确指派，本追加包仅修改既有 `backend/app/services/stock_research_v2/ml_train.py` 和 `backend/tests/test_stock_ml_train.py`；不修改其他七个ML文件或组合桥接。原文件先逐字节备份，原SHA及位置见 `logs/stock-parallel-ml-order-fix-before.json`。

真实红例：特征显式顺序 `[value,quality]` →真实LightGBM训练→ArtifactStore保存读取→映射键恢复为 `[quality,value]` → `predict_ranker`错误抛 `ml_feature_mismatch`。日志 `logs/stock-parallel-ml-order-fix-red.log` 为1失败。最小修复仅改变fit/predict的版本映射条件：fit严格检查映射名称集合、SHA值及显式特征名称唯一性；predict严格检查名称/版本值完全相等。唯一列顺序仍为 `fold.feature_names` / `manifest.feature_order`，原矩阵取列逻辑不变，没有容忍缺列、多列或版本变化。

真实用例同时验证保存前后分数逐项相等、调换映射插入顺序重训模型SHA相等，以及缺特征/额外特征/错误版本均被拒绝。使用上方同一绝对进程局部overlay执行：

```powershell
backend/.venv/Scripts/python.exe -m pytest backend/tests/test_stock_ml_train.py backend/tests/test_stock_ml_predict.py backend/tests/test_stock_research_experiment.py -q
```

**追加修复后11通过，0失败/0跳过，10.47秒**，见 `logs/stock-parallel-ml-order-fix-green.log`；包含新两fold硬资源Job和原研究报告。两个源文件保持原LF换行，精确差异及修改后SHA另存 `logs/stock-parallel-ml-order-fix.patch` / `logs/stock-parallel-ml-order-fix-after.json`。没有变更金融计算、依赖、接口、DB或服务状态。原21项是修复前完整计算包证据，本追加包不虚报全部重新跑过；实库/真实市场/生产链的未验证边界仍适用。
