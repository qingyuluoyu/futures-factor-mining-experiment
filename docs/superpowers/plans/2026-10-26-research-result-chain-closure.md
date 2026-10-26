# ④-3 结果链闭环：全环测试抓到三个真缺陷

日期：2026-10-26
范围：仅股票 `backend/app/services/stock_research_v2/**`、`backend/app/schemas/stock_*`、其测试，以及股票独占前端三文件。期货生产代码、共享认证/网关/布局/运行配置未触碰。

本文不是新总计划，是 `ef27ee0` 之后的定向工作包。前一轮把传输、IPC 接缝、列式凭证做完，并判定四个研究内核因缺日历/历史股票池而受阻。本轮补上最后一环未覆盖的验证：**整条 claim→真实子进程→发布链路**，并因此抓到三个此前全绿掩盖的真缺陷。

---

## 1. 为什么写这个测试

④-3 的既有测试分两半，都不覆盖合起来的那一条路：

| 测试 | 覆盖 | 不覆盖 |
|---|---|---|
| `test_stock_research_v2_task_dispatch.py` | 父进程协议（`fake_spawn` 换掉 `spawn_bounded_process`） | 从不真启子进程 |
| `test_stock_research_process_ipc.py` | 子进程单体（真实解释器跑 `python -m`） | 没有父进程、没有 claim、没有发布 |

`grep spawn_bounded_process tests/` 证实：列式/ML 路径都有**真实** spawn 的测试，唯独研究派发路径一直是 monkeypatch。所以"提交→领取→真实有界子进程→父进程凭 fenced claim 重新推导产物行与任务结果"这条唯一真正上线的路，此前零覆盖。

`tests/test_stock_research_v2_full_loop.py`（2 条）：真 lease、真 `JobService.submit`、真 `claim_next`、真 `execute_claim(runner=task_execution.run_research_process)`、真子进程。benchmark 是五个 kind 里唯一有真内核的，所以它必须真的跑完并发布。

---

## 2. 抓到的三个真缺陷

### 2.1 `view()` 渲染不出已完成的研究任务（生产 bug，`GET /jobs/{id}` 500）

```python
# jobs.py:114（修复前）
reference = DatasetRef(dataset_id=job.result_dataset_id, ...).model_dump(mode="json") if job.status == "completed" else None
```

021 把单结果改成三个互斥分支，`finish_report` 写 `result_artifact_id`/`result_report_sha256` 并把三个数据集列留 NULL。于是**任何已完成的研究任务**一 `get()` 就 `pydantic.ValidationError`（3 个字段全收到 `None`）。

这与 `e21a48e` 声称修掉的 `DatasetJobDetail.parameters` 是**同一个响应对象上的另一个字段**——那次只改了 `parameters`，`result` 没改，所以"021 一放宽 kind 任何研究任务的 `GET /jobs/{id}` 都会 503"这句只对了一半：详情视图照样失败，只是换了一行。

**修法**：新增 `result_reference(job)`，**从结果列本身**推导形态，不从 `kind` 推导。读列比读 `kind` 更严——`kind` 会乐意为一个发布了 report 的任务渲染数据集引用。且两个研究分支按 CHECK 必须把三个数据集列留 NULL，所以**研究结果根本无法重述数据集资格，因此根本无法声称资格**。列组合不构成任一分支（或未完成却带结果列）按名拒绝 `job_result_corrupt` 409，而不是返回 `None` 让别处的状态机报一句含糊的"状态与结果不一致"。

### 2.2 子进程的具名拒绝码被父进程抹成 `worker_failed`（生产 bug）

`research_process.main()` 精心 `emit({"type":"error","code":...})`，`supervise_process` 也确实把它作为 `ProcessExecutionError(code, ...)` 抛出来。但 `supervise_research_process` 的 except 只认 `execution_timeout`/`worker_protocol`/`result_too_large`，其余一律 `JobError("worker_failed")`。

于是 `task_inputs_unavailable` / `task_kind_unregistered` / `task_kernel_failed` 全部变 `worker_failed`。而 `task_inputs_unavailable` 是五个 kind 里**四个的常规结局**（缺日历/股票池/列式凭证），不是边缘情况——`TASK_FAILURE_CODES` 存在就是为了让这些码能落库，父进程却没用它。整条具名错误协议在父侧是死重。

**修法**：`error.code in TASK_FAILURE_CODES` 且状态码合法时，用子进程自己的 code/status/message 继续抛。**不放宽成任意文本的通道**：未登记码、自由文本、大小写变体、非字符串一律仍 `worker_failed`（有测试钉住 7 个负例）。

### 2.3 API 响应 schema 根本没有研究结果的字段

`DatasetJobSummary.result: DatasetRef | None`，加上 `validate_state` 要求 `(completed) == (result is not None)` 且 `result.qualification == "observation_only"`。即使 2.1 修好了，响应 schema 也会拒。021 的三个结果列由 worker 写入，却**没有任何客户端能读**。

**修法**：三个形态各自成模型（`DatasetJobResult` 继承 `DatasetRef` 以保持数据集分支的线上字节不变；`ExperimentJobResult` 只带 `experiment_id`；`ReportJobResult` 带 `artifact_id`+`report_sha256`），组成 union。`Contract` 是 `extra="forbid"`，所以报告载荷不会被读成"缺列的数据集引用"。资格检查移入 `DatasetJobResult` 自己的 validator，错误信息原样保留。

### 2.4 顺带：`decay_lags` 声明 `list[int]` 默认值是 tuple

`Field(default=(1, 5, 10, 20))` 配 `validate_default=False`：调用方传了就是 list（validator 返回 `sorted()`），没传就是 tuple——**同一字段的运行时类型取决于客户端发没发**。每次 `model_dump` 还喷一条 `PydanticSerializationUnexpectedValue` UserWarning（全量 1364 条警告里有 8 条是它，会掩盖真警告）。一个词的根因修复：`default=[1, 5, 10, 20]`。

---

## 3. 前端同源收口

`stock-dataset-jobs-api.ts` 把 `result` 钉成单一数据集对象，`stock-dataset-jobs.tsx:155` 无条件读 `result.dataset_id`——研究结果两项都是 `undefined`。已按后端同样三形态放宽，新增 `isDatasetResult`/`isReportResult` **按字段窄化**（不按 `kind`，否则会给发布 report 的任务渲染数据集引用）。面板对三种结果各说各的话：报告结果明确"不声明数据资格、未认证公司行动或退市、不可直接当可成交结论"；实验结果指向模型库，"本面板不重新认证"。

---

## 4. 变异验证（每项回退后对应测试必须转红）

| # | 回退 | 结果 |
|---|---|---|
| 1 | `view()` 改回无条件 `DatasetRef` | 全环 benchmark 测试转红，`ValidationError` 与原缺陷逐字一致 |
| 2 | 去掉 `TASK_FAILURE_CODES` 承接 | 单测转红；**全环 IC 测试也转红**（`worker_failed`），即真实子进程测试对该 bug 有牙 |
| 3 | API `result` 收窄回 `DatasetRef` | API schema 测试转红 |
| 4 | `decay_lags` 改回 tuple | 新测试转红，`(1,5,10,20) == [1,5,10,20]` |
| 5 | 前端 union 收窄回单形态 | 新客户端测试转红 |

第 2 项是重点：它证明全环测试不是只覆盖 benchmark 成功路径，对"响亮失败"路径同样有牙。

---

## 5. 验证（真跑）

- **后端全量 3286 passed, 4 skipped, 0 failed，退出 0**（基线 3277 ＋ 本轮新增 9：全环 2、结果形态 7）。`decay_lags` 警告从 1364 降至 1346。
- **前端全量 895 passed, 0 failed**（含新增 1 条客户端三形态测试）；`tsc --noEmit` 退出 0，`frontend/tsconfig.json` 逐字节未变。
- 全环测试用**真实子进程**：`execute_claim(runner=task_execution.run_research_process)`，benchmark 真算出报告、真发布、真在 claim 自己的 scope 和 job 键下登记产物行；IC 内核真拒绝并落 `task_inputs_unavailable`，且**不留下任何内容文件**（`store.root.iterdir() == []`）。
- 后端全量必须用 `backend/.venv/Scripts/python.exe`；PATH 上的系统 `python` 缺 `asyncpg`/`passlib`，`pytest tests` 会在收集阶段报 102 个错，看似回归实则环境。
- 前端全量需 `node --import ../logs/node-alias-register.mjs --test`（`@/` 别名解析）。

**未闭合**：`research_ic`/`backtest`/`ml_train`/`report` 四内核仍由 `run_unavailable` 以 `task_inputs_unavailable` 显式拒绝——本轮把它们拒绝的**原因**正确落到了任务行上，但缺的日历与历史股票池两份**无存储来源**的证据仍是数据/授权缺口，不是代码缺口；伪造它们等于伪造资格门禁本该扣住的东西，不做。①重启五原端口与③tick 显式预算补采仍受阻，授权前 `financial_data_calls` 与 `paid_model_calls` 保持 0；020/021 未 apply 到任何真实库，未发布构建、未接 PG、未跑 HTTP 契约、未做本人/五年/发布回退验收。本轮未发起任何外部数据或模型调用，未重启任何服务，未据测试数宣称四内核可用或全系统完成。
