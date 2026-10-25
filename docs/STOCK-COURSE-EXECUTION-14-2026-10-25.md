# 执行14：自定义因子接IC与真实LightGBM计算研究链

**状态：已实现待验证。课程业务闭环、机器学习产品和整体验收仍未完成。**

用户本轮要求先推进课程业务及机器学习，再做真实数据和整体验收。本轮定向读取现有公式、DSL/私有版本服务、PIT/资格、任务/产物与原生资源监督，以四个计算切片实施。复用已有九因子和预处理，没有重写旧研究或修改期货。当前服务仍停，不因广义开发要求自动拉起原服务或恢复付费AI。

## 四包及真实数据流

1. **固定因子版本→计算。** 新增 `backend/app/services/stock_research_v2/factor_values.py`。内置因子版本包含原公式/方法/方向/缺失政策；自定义因子通过现有 `CustomFactorService.resolve/get` 按本人/盘/ID/修订/表达式SHA解析，再复核DSL依赖。复用原 `build_factor_matrix/preprocess`，不把自定义分数伪装成PE。输入日期轴须唯一有序且日频，证券须标准ID，字段轴不静默补齐。历史资格掩码、逐日行业和市值参与处理。PE/PB非正以及表达式无定义/窗口不足不被中位数恢复。单批保持200万格预算，市值中性化依赖也计入；不是300股五年全字段一次载入能力。
2. **同一因子值→IC/分组。** 新增同目录 `analysis.py`，输出IC/RankIC、描述性IR、衰减、非重叠分组收益、每日理论多空。244年化、样本标准差；IR明确未修正重叠标签，不当显著性。先按当日因子分成员，再检查未来收益；有成员缺收益整组为null，不按未来存活重新选股。并列边界不足组数不强行按代码拆组。理论净值遇缺价或开始后的缺因子截面不跨缺口续接；非有限收益转缺失。该多空无借券/成交/成本认证，不是可成交组合。
3. **固定特征→日期query/标签→purge。** 新增 `ml_dataset.py` 和 `backend/app/schemas/stock_ml.py`。同日收益并列给相同整数相关等级；全同/规模不足组不训练，缺标签不补0。训练标签须在验证开始前结束，验证标签须在测试开始前结束；不随机拆同一日期。测试候选不按未来收益存在与否筛掉。特征/顺序/样本内容、窗口及模型参数有固定版本；当前只实现一个明确日期切分，滚动/扩展窗口及公开分布诊断尚未交付。
4. **真实CPU LambdaRank→样本外分数→不可变模型/研究报告。** 新增 `ml_train.py` 和 `research_experiment.py`。使用锁版LightGBM原生 `train(objective=lambdarank)`，不是聊天或GP，也不依赖未安装的sklearn包装。CPU2线程、白名单参数/固定种子、验证段early stopping；测试标签不进入训练、选参或训练期IC。知识截止包含验证标签结束，预测决策须更晚。原生文本模型/JSON manifest记录库版本、样本SHA、特征管线SHA、真实创建时间、知识截止、耗时和重要性；只接受本内容存储的同Scope/数据/版本/环境模型，不反序列化用户pickle。原生迭代有取消/软时限检查，正式任务仍必须接已有硬进程边界，不能将函数回调当硬时限。

`run_experiment` 串接计算→训练期IC→切分→训练→预测→按分数确定性选候选→`ArtifactStore`保存模型和报告。报告保存预处理、精确因子版本、标签口径、实际分析区间、purge、模型和预测。改变预处理会改变ML特征管线版本；同一环境同输入两次运行的语义摘要一致，真实时间/耗时单独保留。模型分数不是概率，候选不是成交持仓。

**当前标签仅下一交易日收盘到窗口末收盘的价格收益，未认证复权/公司行动/退市经济回报。** 输入轴是调用方提供的明确日期，不据此认证真实上海日历；数据资格由后续源证据层授予，本计算函数不授予。所有QA均显式 `qa_only`，观察数据默认拒绝正式训练。研究报告明确 `portfolio_backtest.available=false`，不输出一条零费用净值冒充完整回测。

## 实际新增文件与依赖准入

生产命名空间只有上述六个股票新模块，无旧生产文件修改。新增测试：`backend/tests/test_stock_factor_values_v2.py`、`test_stock_analysis_v2.py`、`test_stock_ml_dataset.py`、`test_stock_ml_train.py`、`test_stock_research_experiment.py`、`test_stock_ml_bounded_process.py`。新增股票独立锁版文件 `backend/requirements-stock-ml.txt`。

- LightGBM4.7.0 Windows AMD64 wheel 1,360,833字节，SHA256 `f42d1e5b32b6f170e606d7c689c6165671da98d7bf37f1addec2623efc8740c9`；Narwhals2.27.1 wheel483,211字节，SHA256 `d057df13f5852b8e157596e82eb5e955fad267425df5e420e0ee9863da483b31`。核对官方PyPI元数据和wheel内MIT许可证，保留元数据、传递依赖及摘要在 `logs/stock-course-execution-14-dependency-audit.json`。
- 复用原NumPy2.5.1/SciPy1.18.0；仅安装至 `logs/stock-ml-qa`，不修改默认期货venv/pyproject/lock/运行配置。官方参考：[原生训练](https://lightgbm.readthedocs.io/en/v4.7.0/pythonapi/lightgbm.train.html)、[CPU参数](https://lightgbm.readthedocs.io/en/stable/Parameters.html)。没有宣称已完成全面漏洞扫描或生产发布准入。
- PyPI的Narwhals元数据第一次连接超时，随后一次有界请求成功；实际两个wheel下载均0重试/明确超时。初次许可证检查过滤器遗漏LICENSE.md，修正检查器后核对MIT；不是依赖哈希失败，也未跳过许可证验证。
- 实际安装：`uv pip install --python backend/.venv/Scripts/python.exe --target logs/stock-ml-qa --no-index --find-links logs/stock-ml-wheels --no-deps --require-hashes -r backend/requirements-stock-ml.txt`。仅公共软件包下载，无金融源或模型API调用。

## 验证、失败和一次自审

测试在backend目录运行，以进程局部 `PYTHONPATH` 包含 `../logs/stock-ml-qa`；联合回归额外包含 `../logs/stock-columnar-qa`，没有改系统环境或生产配置。

```text
.venv/Scripts/python.exe -X utf8 -m pytest tests/test_stock_factor_values_v2.py tests/test_stock_analysis_v2.py -q
.venv/Scripts/python.exe -X utf8 -m pytest tests/test_stock_ml_dataset.py tests/test_stock_ml_train.py -q
.venv/Scripts/python.exe -X utf8 -m pytest tests/test_stock_research_experiment.py -q
.venv/Scripts/python.exe -X utf8 -m pytest tests/test_stock_factor_values_v2.py tests/test_stock_analysis_v2.py tests/test_stock_ml_dataset.py tests/test_stock_ml_train.py tests/test_stock_research_experiment.py -q
.venv/Scripts/python.exe -X utf8 -m pytest tests/test_stock_ml_bounded_process.py tests/test_stock_ml_train.py -q -s
.venv/Scripts/python.exe -X utf8 -m pytest tests/test_stock_factor_values_v2.py tests/test_stock_analysis_v2.py tests/test_stock_ml_dataset.py tests/test_stock_ml_train.py tests/test_stock_research_experiment.py tests/test_stock_ml_bounded_process.py tests/test_stock_factor_expressions.py tests/test_stock_custom_factors_api.py tests/test_stock_metrics_v2.py tests/test_stock_dataset_qualification.py tests/test_stock_research_artifact_store.py tests/test_stock_point_in_time_v2.py tests/test_stock_universe_v2.py tests/test_stock_research_engine.py tests/test_stock_value_policy.py tests/test_stock_dataset_windows_resources.py tests/test_stock_research_worker_process.py -q
```

首次因子7红、ML6红、实验1红、私有解析1红均为缺少新实现，分别保存 `logs/stock-course-execution-14-*-red.log`。初绿因子7/ML6，首次集成15通过。自查补三个失败：开始后缺因子截面跳日续接、极端收益inf、变异fold小数标签静默截断（同时覆盖非法窗口/特征/小组）；修复后18通过。报告预处理版本/标签口径用例先红，补齐参数和知识边界后通过；后续训练直接取消测试和真实硬边界QA合计形成20个新增用例。未删断言/跳过测试或修改旧引擎制造通过。

最终受影响**176通过、0失败、0跳过，81.23秒，60条既有utcnow弃用警告**，见 `logs/stock-course-execution-14-final-regression.log`。包括真实LightGBM训练、模型恢复/篡改/跨用户拒绝、时间泄漏与候选保留、旧公式/DSL/私有因子服务、PIT/历史池、产物、进程回收；SQLite仅用原测试factory验证私有版本服务，不替代原PG。

真实短命Windows Job中完成因子→IC→训练→预测→保存，不是mock分数：512MiB提交内存、4进程上限未提高；首次峰值114,638,848字节，结束active_processes=0，`...-native-job.log`六项通过。迭代取消也实际抛ml_cancelled，不返回模型。另保留一份同测试流程产物 `logs/stock-course-execution-14-qa-study/qa-report.json` 及其artifacts，资源日志 `...-saved-qa.log`；**输入为合成QA夹具，非A股真实行情，不可作为课程真实数据报告**。

无overlay默认环境实际探测LightGBM/Narwhals/PyArrow均不可import，`engine()`返回 `ml_engine_unavailable`/503，`...-default-capability.log`。同默认环境运行原 `test_stock_research_engine.py test_stock_value_policy.py test_stock_research_worker_process.py -q`，**85通过，52.38秒**，`...-default-legacy.log`；没有要求期货安装新依赖。此前主保护verify相对执行13报告只有6新增/0改变/0删除，这是声明新增而不是旧文件漂移；最终新清单verify通过。

**未执行：** 原PG/迁移/真实并发跨进程、原HTTP、生产worker/本人浏览器、真实金融样本/PIT资格、300股五年<5分钟、整体部署/回退/人工验收。本轮无前端修改，未重复前端构建/TSC；未运行全部期货功能，不用SHA或股票测试代替其在线回归。新链未挂生产入口，数据/模型API调用0；五原端口仍无监听，`...-runtime.json`。未重启任何原服务或付费AI，未应用015–018。

## 保护、兼容与回退

原执行13主清单1146旧SHA**全部不变**，仅六个新增股票模块；主清单现1152 `logs/stock-course-execution-14-final-inventory.json`。原8补充SHA全部不变，新增六测试和独立requirements后共15 `...-supplemental-sha.json`。保护断言 `...-preservation.json`，最终 `...-final-guard.log` 通过。五份文档字节备份 `...-backups.json` 保留；无Git提交/分支。

旧API/任务/v1报告未变，无DB结构/数据变化；018已经是列式迁移编号，ML模型/封存登记后续必须使用下一个未占编号（当前019），不能照旧子计划重复创建另一个018。回退仅停止未来新任务接收并移除新股票调用/overlay引用；当前未挂生产，删除/停用本链不影响旧功能，不删模型/报告/历史原数据。将来接入后需先确认本次训练Job树结束再回退，不降级成无约束运行。

## 下一四包（沿原课程依赖，不扩写总计划）

1. **固定数据→业务输入资格。** 现有Parquet/来源修订/PIT/历史池组装为带单位/时点/复权证据的面板；只授权符合证据的动作。capability须检查实际列式receipt、迁移/worker而非旧JSON任务。原PG关闭时只推进离线代码，真实迁移/并发保留待运行许可。
2. **R3可成交组合。** 单独股票v2执行器接同一FactorRef/ML候选；D→D+1、整手/T+1、停牌/涨跌停/量参与率/现金/税费/公司行动/退市流水。缺规则或来源拒绝，不补乘数/状态/价格，不改期货。
3. **R4基准与完整课程报告。** 同口径可信指数/简单买持、交易费用及绩效、至少两组因子比较，固定数据/方法复现；ML标签再对齐同执行窗口的经济总收益。没有R3/R4不把当前候选称组合回测。
4. **M1–M4剩余产品接通。** 滚动fold/样本诊断、019模型与测试封存登记、复用016幂等租约/硬资源训练生命周期、API/UI/恢复取消、模型→同R3/R4比较。正式入口只在上下游/权限/迁移就绪后开放；之后再做真实数据、五年性能、原系统/期货在线回归、发布回退与本人验收。

本轮已经从“ML未实现”推进到真实本地排序计算研究可运行，但课程和ML整包均不能勾选完成。依赖仍需部署准入、固定真实数据尚未认证、无生产模型登记/最终测试消费审计，以上风险保留，不以新测试数量掩盖。
