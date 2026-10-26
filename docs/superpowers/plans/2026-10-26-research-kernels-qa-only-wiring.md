# 四个研究内核：更正一处误判，并给出可执行工作包

日期：2026-10-26
范围：仅股票 `backend/app/services/stock_research_v2/**`、`backend/app/schemas/stock_research_v2_jobs.py` 及其测试。期货生产代码、共享认证/网关/布局/运行配置不触碰。

本文件不是新总计划。它更正 `2026-10-26-study-input-transport-and-ipc.md` §2 与 `2026-10-26-research-result-chain-closure.md` §5 中同一个结论，并给出替代它的工作包。

---

## 1. 被更正的结论

前两轮都写了同一句话，措辞不同：

> `2026-10-26-study-input-transport-and-ipc.md:143,146`
> 「D 受阻……那不是代码缺口，是数据/授权缺口。继续写代码不能闭合它。」
>
> `2026-10-26-research-result-chain-closure.md:88`
> 「缺的日历与历史股票池两份无存储来源的证据仍是数据/授权缺口，不是代码缺口。」

**这句话把两件可分离的事混成了一件。**

| | 能否关闭 | 靠什么 |
|---|---|---|
| **认证**研究结果（把资格升到 `research_only`/`backtest_ready`） | 不能 | 外部版本绑定证据，全仓无存储来源。前一轮 §2.4 的分析**正确，不动它** |
| **运行**研究内核，产出 `qa_only` 结果 | **能** | 调用方传入日历/股票池，`run_archived_study` 今天就在这么做 |

系统自己就把这两件事分开了：`qa_only` 这个词、`validation_status` 这个字段、`bound_experiment` 里那个分支，全都是为"能跑但不能认证"设计的。前一轮看到"不能认证"就停了，没问"能不能跑"。

---

## 2. 证据：机器是全的，只差接线

五条，每条都可复跑：

1. **整条链今天就能跑通，带真实 LightGBM。** `tests/test_stock_archived_study.py:18-24`：
   `run_archived_study(registry, receipt, qa_only=True, ...)` → `first["study"]["model"]["runtime"]["lightgbm"] == "4.7.0"`、`candidate_count == 900`、`semantic_sha256` 两次调用逐字节相同、报告落盘。因子→PIT 面板→LambdaRank→样本外候选→封存，一环不缺。

2. **`run_experiment` 是纯同步函数。** `research_experiment.py:22`：入参 `fields/factors/scope/dataset/split`，无数据库、无网络、无模型凭证——正是"有界子进程里能跑"的形状。`archived_study` 只是它的一个调用方。

3. **`study_transport` 零生产调用方。** `materialize`/`restore`/`reference_digest`（`study_transport.py:127,144,171`）三个导出函数，`grep -rn "study_transport" app/` 只命中定义处。docstring 写明它是"parent 把已证实的网格存证、child 读字节"的唯一桥梁——**为这四个内核建的，从没接上**。

4. **`receipt_for` 零生产调用方，且 docstring 点名了这条路。** `columnar.py:117`：「``build`` keys idempotency on the *dataset job's* key, which a research job does not carry — so a research claim cannot ask ``build`` for the receipt it needs. **This is the read side it does need**」。写它就是给研究 claim 用的。

5. **021 任务链本来就允许从 observation_only 发布 qa_only 结果。** `task_dispatch.py:151`：
   ```python
   expected = "qa_only" if reference.qualification == "observation_only" else "research_only"
   ```
   注释：「The model may not certify itself: an observation-only dataset can only publish a qa_only experiment」。`TASK_SHAPES:50` 已把四个 kind 映射到 `report`/`experiment`；`dataset_worker.py:116-130` 的发布分支（读报告→校指纹→登记产物→`finish_report`）已完整。

**唯一的断点**：`research_process.py:77-78` 把四个 kind 映射到 `run_unavailable`。

---

## 3. 不伪造任何东西

接线不会让任何资格升级，也不会让任何门禁放行：

- 数据集仍是 `observation_only`；`allows(qualification, "ml")` 仍为 False（`run_experiment:27` 因此强制 `qa_only=True`）。
- `study_inputs.py:138` 仍硬编码 `"qualification": "observation_only", "research_enabled": False`。
- 发布的实验 `validation_status` 被 `bound_experiment:151-153` 钉成 `qa_only`，声称更高直接 `job_result_mismatch` 409。
- `run_experiment:57` 仍硬编码 `portfolio_backtest: {"available": False}`。
- 子进程仍无数据库、无网络、无模型凭证；身份仍由父进程从 fenced claim 重新推导。
- 资格阶梯（`OBSERVATION_REASONS`/`RESEARCH_REASONS`/`derive_qualification`）**一行不改**。

日历与股票池由**调用方**传入，与 `run_archived_study` 的关键字参数完全同构。它们不是伪造的"证据"，是一次 qa_only 计算的**输入**，并且被内容绑定进报告：`archived_study.py:50`「Source content + full calendar/pool content are bound in `inputs.input_sha256`」——事后可审计，改一个成员日期指纹就变。

`load_study_inputs:44` 仍校验 `pool_id`/`pool_version` 与 manifest 一致、日历覆盖选区；`:113-114` 仍要求每条观察日的 `effective_date` 是交易日。调用方不能换一个池的身份，也不能给一份与数据自相矛盾的日历。

---

## 4. 工作包 D（按依赖）

| # | 内容 | 要点 |
|---|---|---|
| D1 | 四个研究参数模型增加**同一份**研究证据字段 | `calendar: TradingCalendar` + `universe: HistoricalUniverse`，抽成共享模型，四个 kind 各持一份。`Contract` 是 `extra="forbid"`，不加字段就传不进去 |
| D2 | `DatasetClaim` 增加 `study: StudyRef \| None = None` | 父进程执行时注入；`claim_next` 产出的 claim 该字段为 None，既有路径不变 |
| D3 | 父进程建网格 | `receipt_for(claim.parameters.dataset)` → `load_study_inputs(...)` → `study_transport.materialize` → 注入 claim。需要 session，`execute_claim` 有 `session_factory`；`run_research_process` 没有，注入点要在它之前 |
| D4 | 子进程四个真内核 | `restore(reference, store)` → 按 kind 调 `run_experiment`（或其组合）→ `store.put_json` 报告 → 回报 `TaskOutcome` |
| D5 | `ml_train` 额外登记实验行 | 子进程不能写库；实验行的 owner/dataset/grade 由父进程按 claim 派生后 `bound_experiment` 校验 |
| D6 | 测试 | 每个 kind 一条：真子进程、真报告、`qa_only`、`observation_only` 不放宽；以及拒绝路径（缺日历/池不匹配/指纹不符） |

**`backtest` 需先查清**：`run_experiment:57` 硬编码不产出组合回测。`portfolio.py` 是否有 qa_only 入口、`ExecutionEvidence` 是否是硬前提，动手前先读代码再定 D4 里它到底产出什么——**不允许把"回测不可用"包装成一份看起来像回测的报告**。

---

## 5. 验证要求

- 后端全量必须用 `backend/.venv/Scripts/python.exe`（PATH 上的系统 `python` 缺 `asyncpg`/`passlib`，会在收集阶段报约 102 个错，看似回归实则环境）。
- 每个内核至少一条**真子进程**测试；不许用 `fake_spawn` 替代后宣称接通。
- 每项修复做变异验证：回退后对应测试必须转红。
- 明确不断言：不宣称四个内核"可用/已认证"，不宣称资格升级，不宣称全系统完成。

## 6. 仍未闭合（与本包无关，保持原状）

①重启五原端口、③tick 显式预算补采仍受阻，授权前 `financial_data_calls` 与 `paid_model_calls` 保持 0；020/021 未 apply 到任何真实库；资格升级所需的日历/股票池/授权/执行四类外部证据仍无存储来源，**本包不试图闭合它**。未发布构建、未接 PG、未跑 HTTP 契约、未做本人/五年/发布回退验收。
