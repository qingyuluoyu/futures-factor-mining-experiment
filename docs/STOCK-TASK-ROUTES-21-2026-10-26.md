# 执行21 P4：研究任务六个kind的HTTP入口（默认全关）

状态：已实现待验证。本包目标4/4通过；未启动服务、未应用迁移、未连接PG、未发布构建、外呼与模型预算0。真实HTTP契约与本人页面未验收。

## 为什么要有这一包

迁移021把`ck_stock_v2_job_kind`放宽到六个kind，`JobService.submit`也已按kind分派，但HTTP层只有观察`dataset`一个POST入口：五个研究kind在库里可存、在服务里可领，却没有路由能提交。本包把它们接上，且全部保持默认关闭。

## 实际改动（三个文件）

- `backend/app/api/stock_dataset_jobs.py`：新增五个POST路由，每个只接受自己的command schema——
  `/research/v2/ic/jobs`→`CreateResearchICJob`、`/backtest/jobs`→`CreateBacktestJob`、
  `/benchmark/jobs`→`CreateBenchmarkJob`、`/ml/jobs`→`CreateMLTrainJob`、`/report/jobs`→`CreateReportJob`。
- `backend/app/schemas/stock_dataset_jobs_api.py`：`DatasetJobDetail.parameters`由`DatasetBuild`改为`TaskParameters`。
- `backend/tests/test_stock_research_v2_task_routes.py`：新增，4个目标。

## 两个设计决定

**一个kind一条路径，不共用一个body。** `Create*Job`的`kind`是`Literal[...]`，所以往`/ic/jobs`发backtest的body会在pydantic层就被拒（422），服务根本不会被调用。不存在"把任务改名成另一个内核"的路径，也不存在请求体决定内核的可能——内核只由持久化的`job.kind`选，这一点由021与`task_dispatch`的闭表保证，路由不参与选择。

**研究路由复用观察开关`stock_v2_jobs_enabled`，不新增第二个开关。** 该开关是唯一会真正启动v2执行器的开关：开关关→503；开关开但021未登记或artifact root缺失→worker起不来→该属性仍为False→还是503。研究内核比观察内核更重（`ml_train`要900秒并训练原生LambdaRank），如果没有活执行器，开放提交只会堆一批没人跑的任务。所以共用最严的那个门，而不是给研究kind单独开一个能绕过执行器检查的口子。

**`cancel`仍然不门禁**，与观察路由一致：取消不创建任何东西，新提交关闭时既有任务必须仍可查询和取消。

## 顺带修掉的真问题

`DatasetJobDetail.parameters`原本只接受`DatasetBuild`。`view()`返回的是`job.parameters["command"]`，研究kind存的是五个研究参数模型之一。也就是说021一放宽kind，任何研究任务的`GET /jobs/{id}`都会503 `v2_job_response_failed`——不是新bug，是021留下的一处没跟上的响应schema。改用claim同一个`TaskParameters`判别联合后，详情按各自kind返回各自参数。测试里以`detail["parameters"]["kind"]`固定这一点。

## 红绿与自审

初次4项全红：先是fixture重复注册schema版本（UNIQUE），再是021链schema未注册，然后是路由不存在（404），最后暴露出上面两个真问题。实现后4/4通过。

联合回归：**3246 passed / 4 skipped**，0新增失败（4个失败全在`tests/test_stock_research_v2_task_dispatch.py`，是④-3并发在建文件，其fixture的`dataset_not_found`与`TASK_SHAPES`映射尚未闭合，不属本包）。

自审查证：五个路由确实默认503且`retryable:false`；跨kind body确实422且不进服务；开开关后五个kind各自202且`method_version`/`workflow_version`为研究版；同键重发返回同一job、同键不同参返回409；换user/换mode读不到别人任务；关开关后读和取消仍正常；别人的dataset、版本不符、资格不符分别404/409/409且不落库。

未做：未跑前端、未构建、未启动服务、未apply 020/021、未接PG、未做本人验收。`research_ic`/`backtest`/`ml_train`/`report`四个内核目前仍由`research_process.run_unavailable`显式拒绝（缺日历/股票池/列式凭证），路由开放后它们会以`task_inputs_unavailable`响亮失败而不是产出看似合理的报告；`benchmark`是唯一已有真实实现的内核。
