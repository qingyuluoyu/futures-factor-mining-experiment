# ④-3 后继包：研究输入传输与研究子进程 IPC 接缝

日期：2026-10-26
范围：仅股票 `backend/app/services/stock_research_v2/**` 及其测试。期货生产代码、共享认证/网关/布局/运行配置未触碰。

本文件不是新总计划，是 ④-3（`1d6af9c`）之后的一个定向工作包。它记录三件事：本轮实际接通了什么、四个研究内核**为什么**仍然拒绝（带证据）、以及按依赖顺序下一步能做什么。

---

## 1. 本轮已完成并验证

### 1.1 研究输入传输（新模块）

`study_transport.py` 新增，`artifacts.py` 增加有界二进制路径。

- `ArtifactStore.put_binary` / `read_binary`：内容寻址、逐字节返回、`os.link` + 读回校验，与 `put_json` 同一套写法。
- **为什么必须新开二进制路径**：`read_json`/`put_json` 用 `allow_nan=False` 且会规范化。研究面板大半是 NaN（缺披露处），JSON 结构上装不下；float64 走十进制文本也不再是源值。所以 digest 本身就是全部契约，不做任何解释。
- `study_transport` 单向、内容寻址：父进程存盘，子进程只读字节；坐标轴**永远从引用重建，不从载荷重建**，所以改存储字节无法给证券改名或移动日期。

### 1.2 研究子进程 IPC 接缝测试（新测试，抓到真 bug）

`tests/test_stock_research_process_ipc.py` 用**真实解释器**跑 `python -m app.services.stock_research_v2.research_process`，断言真正过管道的那一条消息。

**抓到的生产 bug**（在我上一轮提交的 `1d6af9c` 里）：

```python
# research_process.py:105（修复前）
if task_module(claim.kind) != __name__:
    raise JobError("task_kind_unregistered", ...)
```

用 `python -m` 启动时 `__name__` 是 `"__main__"`，不等于 `"app.services.stock_research_v2.research_process"`。**每一个被派发的研究内核都会以 `task_kind_unregistered` 被拒绝。**

为什么此前全绿：所有既有测试要么在进程内直接调 `run_task_kernel`（绕过 `execute_payload` 的模块校验），要么用 `fake_spawn` 替换掉 `spawn_bounded_process`（从不真正启动子进程）。变异验证：

| 状态 | `test_stock_research_process_ipc.py` | `test_stock_research_v2_task_dispatch.py` |
|---|---|---|
| 修复后 | 6 passed | 11 passed |
| 把 `MODULE_NAME` 改回 `__name__` | **3 failed** | **11 passed** |

即：这个 bug 对所有既有测试不可见。已改为 `MODULE_NAME = __spec__.name if __spec__ else __name__`。

### 1.3 列式凭证按数据集推导（新能力）

`ColumnarService.receipt_for(reference)`（`columnar.py`）新增。这是 §2.1 那个"数据在、索引在、缺查询"的缺口。

- 按 `(owner_id, trading_mode, dataset_id)` 查，走既有的 `ix_stock_columnar_dataset_scope` 索引。
- 不构建、不重写、不补采；只读。
- 三道拒绝，全部变异验证过（回退后对应测试转红）：
  - 无登记 → `columnar_not_found` 404；
  - 同一数据集多条登记但 `manifest_sha256` 不一致 → `columnar_integrity_failed` 409（**不任选其一**——两个构建本应内容相同，不一致是登记故障，悄悄取新的会把它藏起来）；
  - 全部登记都已隔离 → `columnar_integrity_failed` 409；
  - 去掉 scope 过滤会跨用户/跨盘泄露 → 测试转红。

### 1.4 验证（真跑）

- 后端全量：**3277 passed, 4 skipped, 0 failed，退出 0**。基线 3250 + 本轮新增 27（传输 16、IPC 接缝 6、凭证查询 5）。
- 新传输测试 16 条，零 warning。
- 传输五项修复逐一变异验证（每项回退后对应测试转红，恢复后转绿）：日志行用了已解码的 DataFrame、纯空白轴标签被接受、`restore` 信任调用方模型实例、`ArtifactError` 以裸 `ValueError` 逃逸、被篡改载荷不被发现。
- `receipt_for` 三道拒绝逐一变异验证：去掉 scope 过滤（跨用户泄露）、放行已隔离登记、对内容不一致任选其一。
- 解释器注意：后端全量必须用 `backend/.venv/Scripts/python.exe`。PATH 上的系统 `python` 缺 `asyncpg`/`passlib`，`pytest tests` 会在收集阶段报 102 个错，看起来像代码回归，其实不是。

---

## 2. 四个研究内核仍然拒绝——精确原因（这是阻塞，不是没做）

`research_process.py` 的 `TASK_KERNELS` 里 `benchmark` 有真内核，其余四个由 `run_unavailable` 以 `task_inputs_unavailable` 显式拒绝。本轮查清了缺的到底是什么。`load_study_inputs`（`study_inputs.py:34`）要三样东西：

### 2.1 列式凭证 `ColumnarRef`——**可推导，但没有代码走这条路**

`ColumnarRef` 只有三个字段（`app/schemas/stock_columnar.py:47-50`）：`columnar_id`、`dataset`、`manifest_sha256`。

`StockColumnarDataset`（`app/models/stock_columnar.py:16-35`）**已经存了这三样的全部来源**，并且已经有索引：

```python
Index("ix_stock_columnar_dataset_scope", "owner_id", "trading_mode", "dataset_id"),
```

但全仓没有任何一处按 `dataset_id` 查这张表。`grep -rn "StockColumnarDataset|dataset_id ==" app/` 只有：

- `columnar.py:11`（导入为 `Row`）
- `archive.py:231` 的 `dataset_id ==`，但那是**另一张表** `StockResearchDatasetSource`

`ColumnarService.read`（`columnar.py:122`）只按 `Row.id == receipt.columnar_id` 查。五个研究命令 schema（`stock_research_v2_jobs.py:38-105`）只带 `dataset: DatasetRef`，没有 `columnar_id`/`manifest_sha256`。`grep -rn "columnar" app/api/` 无匹配——没有 HTTP 路由能构建或暴露凭证。

**结论：数据在、索引在，缺的只是一次 `(owner_id, trading_mode, dataset_id) → (id, manifest_sha256)` 的查询。这是可实现的一步。**

### 2.2 交易日历 `TradingCalendar`——**无存储来源，只能调用方传入**

`TradingCalendar(` 在整个仓库只出现在 tests 里（`test_stock_study_inputs.py:31`、`test_stock_research_qualification_grades.py:28`）。生产代码里四处提及全部是"重新校验调用方传入的对象"：

| 位置 | 行为 |
|---|---|
| `qualification.py:273` | `_evidence(calendar, TradingCalendar)`，`calendar` 是默认 `None` 的关键字参数 |
| `study_inputs.py:40` | `isinstance` 检查调用方参数 |
| `study_inputs.py:42` | `model_validate(calendar.model_dump())`，只是往返，不推导 |

`migrations/*.sql` 里没有任何 A 股日历/股票池成员表。`app/models/` 里没有任何模型有 `sessions` 或 `members` 列。

`study_inputs.py:50` 要遍历 `calendar.sessions`，`:113-114` 在观察行的 `effective_date` 不在 `session_set` 时抛 `study_calendar_conflict`——**代码要求一份它无法从 manifest 拿到的交易日列表**。manifest 里唯一的日历痕迹是自由文本 `SourcePage.calendar_reference`，默认值 `"unverified"`（`stock_research_v2.py:140`）。

而 `study_inputs.py:41` 明确拒绝猜测：

```python
raise JobError("study_time_evidence_required", "研究输入必须绑定明确日历和历史股票池，未推测工作日或当前名单", 422)
```

### 2.3 历史股票池 `HistoricalUniverse`——**无存储来源，只能调用方传入**

manifest 钉住了**身份**（`pool_id`、`pool_version`、`instruments`、`warmup_start`、`end`），但**没有成员区间**（`valid_from`/`valid_to`/`known_on`/`exit_known_on`）——而那正是 `universe_at` 实际消费的东西（`universe.py:60-61`）。`study_inputs.py:64` 需要真实成员时间线来构建 eligibility mask。

`HistoricalUniverse(` 同样只出现在 tests 里。没有迁移建表，没有模型有成员列。

### 2.4 这是系统**自己**的设计意图，不是遗漏

`qualification.py:44-46`：

```python
# A gap in any of these means the observation itself is not a certified research input.
OBSERVATION_REASONS = frozenset({"observation_only_dataset", "calendar_unverified", "historical_universe_unverified",
    "source_authorization_evidence_unverified", "selected_fields_unavailable", "historical_industry_unavailable"})
```

`derive_qualification`（`:69-86`）：`if tiers & OBSERVATION_REASONS: return "observation_only"`。`inspect_requirements` 的阻断项（`:276-286`）在 `certified_calendar is None` 时追加 `calendar_unverified`，在 `certified_universe is None` 时追加 `historical_universe_unverified`。`EXECUTION_REFERENCES`（`:40-41`）还把两者列入六项执行证据，缺任一项连 `backtest_ready` 都拿不到。

模块 docstring（`:1-12`）直说："There is no admin button, no request flag and no manifest edit that can raise it."

`inspect_requirements` **零生产调用方**（`grep -rn "inspect_requirements"` 只有定义处和 tests）。没有登记表、没有证据行、没有 HTTP 路由能让调用方断言一份"已认证"的日历/股票池。唯一路径是把 Python 对象当关键字参数传进来，而今天这么做的只有 tests 里的手写字面量。

`study_inputs.py:143` 自己记着这条限制："日历/历史池输入及来源授权尚未由正式资格登记认证，不自动升级observation_only。"

**所以：伪造日历或股票池 = 伪造资格门禁本该扣住的那份证据。本轮不做，也不该做。**

---

## 3. 按依赖顺序的下一步

| # | 工作包 | 状态 | 说明 |
|---|---|---|---|
| A | 列式凭证按 `dataset_id` 推导 | **已完成** | §1.3。`receipt_for`，含三道拒绝，全部变异验证。 |
| B | 研究输入IPC接缝 | **已完成** | §1.2。抓到并修掉 `__name__` 生产 bug。 |
| C | 研究输入传输 | **已完成** | §1.1。 |
| D | `research_ic`/`backtest`/`ml_train`/`report` 四内核 | **受阻** | A 做完后，缺的只剩 §2.2/§2.3 两份证据。二者都**没有存储来源**，且资格门禁刻意认定其未认证。要打通需要：要么登记真实日历/历史池证据（外部数据，需显式预算与授权），要么改资格口径（等于自己给自己开门，不做）。 |
| E | 重启五原端口 / tick 显式预算补采 / 020·021 落库 | **受阻** | 需用户授权，授权前 `financial_data_calls` 与 `paid_model_calls` 保持 0。 |

**D 之前不伪造证据能推进的已经全部做完（A/B/C）。** 现在四个内核缺的是两份"全仓没有存储来源"的证据——那不是代码缺口，是数据/授权缺口。继续写代码不能闭合它，伪造它能闭合但会伪造资格门禁本该扣住的东西。

---

## 4. 本轮明确没有做的事

- 没有伪造交易日历或历史股票池，没有把 `observation_only` 冒充成更高资格。
- 没有重启任何服务，没有应用 020/021 到任何真实库，没有恢复付费 AI。
- 没有发起任何外部数据或模型调用（`financial_data_calls` = 0，`paid_model_calls` = 0）。
- 没有据测试数或 commit 数宣称"四个研究内核可用"或"全系统完成"。
- 没有碰期货生产代码及共享认证/网关/布局/运行配置。
