import { z } from "zod"

const API_BASE = `${(process.env.NEXT_PUBLIC_API_URL ?? "").replace(/\/$/, "")}/api/stock/research/v2`
const digest = z.string().regex(/^[a-f0-9]{64}$/)
const dateTime = z.iso.datetime({ offset: true })
const parametersSchema = z.object({
  source_ids: z.array(z.uuid()).min(1).max(1000),
  start: z.iso.date(), end: z.iso.date(), warmup_start: z.iso.date(),
  pool_id: z.string().min(1).max(100), pool_version: digest,
  instruments: z.array(z.string().regex(/^(?:6\d{5}\.SH|[03]\d{5}\.SZ|[489]\d{5}\.BJ)$/)).min(1).max(10000),
}).strict().superRefine((value, context) => {
  if (!(value.warmup_start <= value.start && value.start <= value.end) || new Set(value.source_ids).size !== value.source_ids.length || new Set(value.instruments).size !== value.instruments.length) context.addIssue({ code: "custom", message: "来源、证券重复或日期倒置" })
}).transform((value) => ({ ...value, source_ids: [...value.source_ids].sort(), instruments: [...value.instruments].sort() }))

export const createDatasetJobSchema = z.object({
  kind: z.literal("dataset").default("dataset"), idempotency_key: z.uuid(), parameters: parametersSchema,
  max_runtime_seconds: z.number().int().min(30).max(300).default(120),
  max_queue_seconds: z.number().int().min(30).max(900).default(300),
}).strict()
export type CreateDatasetJob = z.input<typeof createDatasetJobSchema>

// --- research kinds (migration 021) ------------------------------------------------
// ``JOB_KINDS`` in ``app/schemas/stock_research_v2_jobs.py`` is the single source of
// truth; these two literals must stay in step with it.  Each kind carries exactly one
// workflow/method version pair, and the pair is what tells an observation job from a
// research one — so the client checks the pairing rather than trusting either field
// alone.  A job never claims a qualification its evidence cannot reach: the dataset
// result branch is the only one that restates one and the backend pins it to
// ``observation_only``, so a run can never upgrade the dataset it was built from.
const OBSERVATION_METHOD = "stock-observation-manifest-v1"
const RESEARCH_METHOD = "stock-research-task-v1"
const OBSERVATION_WORKFLOW = "stock-v2-dataset-job-v1"
const RESEARCH_WORKFLOW = "stock-v2-research-task-v1"
const kindSchema = z.enum(["dataset", "research_ic", "backtest", "benchmark", "ml_train", "report"])
const methodSchema = z.enum([OBSERVATION_METHOD, RESEARCH_METHOD])
const workflowSchema = z.enum([OBSERVATION_WORKFLOW, RESEARCH_WORKFLOW])
const referenceSchema = z.object({ dataset_id: z.uuid(), data_version: digest, qualification: z.enum(["observation_only", "research_only", "backtest_ready"]) }).strict()
const BUILTIN_FACTORS = ["pe_ttm", "pb", "revenue_yoy", "netprofit_yoy", "roe", "gross_margin", "ret_20d", "vol_20d", "turnover_20d"]
const requirementsSchema = z.object({
  factor_ids: z.array(z.enum(BUILTIN_FACTORS)).max(9),
  custom_factors: z.array(z.object({ id: z.uuid(), revision: z.number().int().min(1).max(2147483647), expression_version: digest }).strict()).max(16),
  action: z.enum(["analysis", "backtest", "rotation"]),
  industry_neutral: z.boolean(), mktcap_neutral: z.boolean(), weight: z.enum(["equal", "mktcap", "factor"]),
}).strict().superRefine((value, context) => {
  if (!value.factor_ids.length && !value.custom_factors.length) context.addIssue({ code: "custom", message: "至少选择一个内置或固定版本自定义因子" })
  if (new Set(value.factor_ids).size !== value.factor_ids.length) context.addIssue({ code: "custom", message: "所选内置因子不能重复" })
  if (new Set(value.custom_factors.map((item) => `${item.id}:${item.revision}`)).size !== value.custom_factors.length) context.addIssue({ code: "custom", message: "所选自定义因子不能重复" })
})

/** The backend serialises ``Decimal`` as a string in JSON mode, so money and rates are
 *  read and written as exact decimal text — never coerced through a float, which would
 *  silently round a cash amount or a cost rate. */
function decimal(places: number | null, minimum: number, maximum: number, label: string) {
  const pattern = places === null ? /^-?\d+(?:\.\d+)?$/ : new RegExp(`^-?\\d+(?:\\.\\d{1,${places}})?$`)
  return z.string().regex(pattern, `${label}必须是不超过${places ?? "任意"}位小数的数字字符串`).refine((value) => {
    const parsed = Number(value)
    return Number.isFinite(parsed) && parsed >= minimum && parsed <= maximum
  }, `${label}超出允许范围`)
}
const costsSchema = z.object({
  commission_rate: decimal(6, 0, 0.003, "手续费率"), min_commission: decimal(2, 0, 100, "最低手续费"),
  slippage_rate: decimal(6, 0, 0.02, "滑点率"), participation_rate: decimal(6, 0.000001, 0.1, "参与率上限"),
  commission_basis: z.literal("inclusive_exchange_fees"),
}).strict()
const indexPointSchema = z.object({ trade_date: z.iso.date(), open: decimal(null, 0.000001, 1e12, "指数开盘").nullable(), close: decimal(null, 0.000001, 1e12, "指数收盘").nullable() }).strict()
/** Every one of these must name a real document; a blank one is not evidence of anything. */
const REFERENCE_KEYS = ["provider", "source_reference", "authorization_reference", "methodology_reference", "calendar_reference"] as const
const indexSchema = z.object({
  spec: z.object({
    instrument_id: z.string().regex(/^(?:000[0-9]{3}\.SH|399[0-9]{3}\.SZ)$/), kind: z.enum(["price_index", "total_return_index"]),
    total_return_basis: z.enum(["not_applicable", "gross_reinvested", "net_reinvested"]), currency: z.literal("CNY"), market: z.literal("CN_A"),
    timezone: z.literal("Asia/Shanghai"), frequency: z.literal("1d"), level_unit: z.literal("point"),
    provider: z.string().min(1).max(100), source_reference: z.string().min(1).max(200), authorization_reference: z.string().min(1).max(200),
    methodology_reference: z.string().min(1).max(200), calendar_reference: z.string().min(1).max(200), data_version: digest,
    qualification: z.enum(["unverified", "verified"]), anchor: z.literal("next_session_open"),
  }).strict().superRefine((value, context) => {
    // A price index has no total-return basis and vice versa; the pair is evidence, not a label.
    if ((value.kind === "price_index") !== (value.total_return_basis === "not_applicable")) context.addIssue({ code: "custom", message: "指数类型与总收益口径不一致" })
    for (const key of REFERENCE_KEYS) {
      if (!value[key].trim()) context.addIssue({ code: "custom", message: "空引用不是证据" })
    }
  }),
  rows: z.array(indexPointSchema).min(2).max(20000),
}).strict().superRefine((value, context) => {
  const dates = value.rows.map((row) => row.trade_date)
  if (new Set(dates).size !== dates.length || dates.join() !== [...dates].sort().join()) context.addIssue({ code: "custom", message: "指数日期必须唯一且严格升序" })
})
const trainingSchema = z.object({
  n_estimators: z.number().int().min(1).max(500), max_depth: z.number().int().min(1).max(8), num_leaves: z.number().int().min(2).max(63),
  min_child_samples: z.number().int().min(1).max(10000), learning_rate: z.number().gt(0).lte(0.5), reg_alpha: z.number().min(0).max(100),
  reg_lambda: z.number().min(0).max(100), seed: z.number().int().min(0).max(2147483647), threads: z.literal(2), max_seconds: z.number().int().min(1).max(600),
}).strict().superRefine((value, context) => {
  if (value.num_leaves > 2 ** value.max_depth) context.addIssue({ code: "custom", message: "叶子数不得超过深度对应上限" })
})
const walkForwardSchema = z.object({
  sessions: z.array(z.iso.date()).min(6).max(20000), mode: z.enum(["expanding", "rolling"]),
  warmup_sessions: z.number().int().min(0).max(10000), train_sessions: z.number().int().min(2).max(10000),
  validation_sessions: z.number().int().min(1).max(5000), test_sessions: z.number().int().min(1).max(5000), fold_count: z.number().int().min(1).max(5),
}).strict().superRefine((value, context) => {
  if (new Set(value.sessions).size !== value.sessions.length || value.sessions.join() !== [...value.sessions].sort().join()) context.addIssue({ code: "custom", message: "交易日轴必须唯一且严格升序" })
  if (value.warmup_sessions + value.train_sessions + value.validation_sessions + value.test_sessions * value.fold_count > value.sessions.length) context.addIssue({ code: "custom", message: "交易日不足以覆盖全部明确窗口；未自动缩短" })
})
const timeSplitSchema = z.object({
  train_start: z.iso.date(), train_end: z.iso.date(), validation_start: z.iso.date(), validation_end: z.iso.date(), test_start: z.iso.date(), test_end: z.iso.date(),
}).strict().superRefine((value, context) => {
  if (!(value.train_start <= value.train_end && value.train_end < value.validation_start && value.validation_start <= value.validation_end
    && value.validation_end < value.test_start && value.test_start <= value.test_end)) context.addIssue({ code: "custom", message: "训练、验证、测试须按日期严格分离" })
})
const researchParameterSchemas = {
  research_ic: z.object({ kind: z.literal("research_ic"), dataset: referenceSchema, factors: requirementsSchema,
    horizon: z.number().int().min(1).max(250), groups: z.number().int().min(2).max(20),
    decay_lags: z.array(z.number().int().min(1)).max(8) }).strict().superRefine((value, context) => {
    if (new Set(value.decay_lags).size !== value.decay_lags.length || value.decay_lags.some((lag) => lag < 1)) context.addIssue({ code: "custom", message: "衰减滞后不能重复或小于1" }
    )
    if (value.decay_lags.join() !== [...value.decay_lags].sort((left, right) => left - right).join()) context.addIssue({ code: "custom", message: "衰减滞后必须升序" })
  }),
  backtest: z.object({ kind: z.literal("backtest"), dataset: referenceSchema, factors: requirementsSchema, pool_version: digest,
    start: z.iso.date(), end: z.iso.date(), top_n: z.number().int().min(1).max(1000), rebalance: z.enum(["daily", "weekly", "monthly"]),
    weight: z.enum(["equal", "factor", "mktcap"]), initial_cash: decimal(2, 0.01, 1e14, "初始资金"),
    risk_free_annual: decimal(6, 0, 1, "年化无风险利率"), costs: costsSchema, qa_only: z.literal(true) }).strict()
    .superRefine((value, context) => { if (value.start > value.end) context.addIssue({ code: "custom", message: "回测区间倒置" }) }),
  benchmark: z.object({ kind: z.literal("benchmark"), dataset: referenceSchema, strategy_artifact_sha256: digest, index: indexSchema }).strict(),
  ml_train: z.object({ kind: z.literal("ml_train"), dataset: referenceSchema, factors: requirementsSchema, split: walkForwardSchema,
    training: trainingSchema, qa_only: z.literal(true) }).strict(),
  report: z.object({ kind: z.literal("report"), dataset: referenceSchema, factors: requirementsSchema, split: timeSplitSchema,
    horizon: z.number().int().min(1).max(250), groups: z.number().int().min(2).max(20), top_n: z.number().int().min(1).max(1000),
    training: trainingSchema, qa_only: z.literal(true) }).strict(),
}
/** Mirrors ``parameter_tag``: an observation build is the only payload without its own
 *  ``kind``, so it is tagged from the field only it declares. */
function parameterTag(parameters: unknown): string {
  if (typeof parameters !== "object" || parameters === null) return ""
  return "source_ids" in parameters ? "dataset" : String((parameters as { kind?: unknown }).kind ?? "")
}
const taskParametersSchema = z.union([parametersSchema, ...Object.values(researchParameterSchemas)])

/** The three mutually exclusive result branches ``ck_stock_v2_job_result`` allows.  Exactly
 *  one is ever present and each is told apart by its own required fields, so a research
 *  result is never re-read as a dataset reference that merely lacks its columns — every
 *  member is ``strict()``, so the extra keys alone refuse it.  Only the dataset branch
 *  restates a qualification, and the backend pins that one to ``observation_only``; the
 *  two research branches carry none, so a research job cannot claim a grade at all. */
const datasetResultSchema = z.object({ dataset_id: z.uuid(), data_version: digest, qualification: z.literal("observation_only") }).strict()
const experimentResultSchema = z.object({ experiment_id: z.uuid() }).strict()
const reportResultSchema = z.object({ artifact_id: z.uuid(), report_sha256: digest }).strict()
const resultSchema = z.union([datasetResultSchema, experimentResultSchema, reportResultSchema])

const fields = {
  id: z.uuid(), kind: kindSchema, status: z.enum(["pending", "running", "completed", "failed", "cancelled", "expired"]), step: z.string().max(64),
  attempts: z.number().int().min(0).max(1), request_id: z.string().regex(/^[a-f0-9]{32}$/),
  created_at: dateTime, updated_at: dateTime, started_at: dateTime.nullable(), finished_at: dateTime.nullable(), deadline_at: dateTime.nullable(), queue_expires_at: dateTime,
  method_version: methodSchema, workflow_version: workflowSchema,
  result: resultSchema.nullable(), error_code: z.string().max(100).nullable(),
}
function stateIsConsistent(value: z.infer<z.ZodObject<typeof fields>>) {
  return (value.status === "completed") === (value.result !== null)
    && (["pending", "running"].includes(value.status)) === (value.finished_at === null)
    && (value.status !== "running" || (value.attempts === 1 && value.started_at !== null && value.deadline_at !== null))
    && (value.status !== "pending" || (value.attempts === 0 && value.started_at === null && value.deadline_at === null))
    && (value.status !== "completed" || (value.attempts === 1 && value.started_at !== null))
}
/** The kind is the persisted truth, so it — not the body — selects the version pair and
 *  the parameter model.  A job claiming the other family's versions, or parameters
 *  tagged for another kind, is refused instead of being reinterpreted. */
function kindIsConsistent(value: z.infer<z.ZodObject<typeof fields>>) {
  const research = value.kind !== "dataset"
  if (research !== (value.method_version === RESEARCH_METHOD) || research !== (value.workflow_version === RESEARCH_WORKFLOW)) return false
  return "parameters" in value ? parameterTag(value.parameters) === value.kind : true
}
const summarySchema = z.object(fields).strict().refine(stateIsConsistent, "任务状态、结果或执行时间不一致").refine(kindIsConsistent, "任务类型、版本或参数不一致")
const detailSchema = z.object({ ...fields, parameters: taskParametersSchema }).strict().refine(stateIsConsistent, "任务状态、结果或执行时间不一致").refine(kindIsConsistent, "任务类型、版本或参数不一致")
export type DatasetJobSummary = z.infer<typeof summarySchema>
export type DatasetJobDetail = z.infer<typeof detailSchema>
/** The observation build is the only member of the parameter union without its own
 *  ``kind``, so a consumer narrows to it by the field only it declares — after checking
 *  the persisted kind, never on the body alone. */
export type ObservationJobParameters = Extract<DatasetJobDetail["parameters"], { source_ids: unknown }>
export function isObservationDetail(detail: DatasetJobDetail): detail is DatasetJobDetail & { parameters: ObservationJobParameters } {
  return detail.kind === "dataset" && "source_ids" in detail.parameters
}
/** Which of the three 021 result branches a finished job published.  The branches are
 *  told apart by the fields each declares, so this narrows on those — never on the
 *  kind, which would render a dataset reference for a job that published a report. */
export function isDatasetResult(result: NonNullable<DatasetJobDetail["result"]>): result is Extract<NonNullable<DatasetJobDetail["result"]>, { dataset_id: unknown }> {
  return "dataset_id" in result
}
export function isReportResult(result: NonNullable<DatasetJobDetail["result"]>): result is Extract<NonNullable<DatasetJobDetail["result"]>, { report_sha256: unknown }> {
  return "report_sha256" in result
}
const pageSchema = z.object({ items: z.array(summarySchema).max(100), total: z.number().int().nonnegative(), has_more: z.boolean() }).strict()
export type DatasetJobPage = z.infer<typeof pageSchema>
const availabilitySchema = z.object({ accepting: z.boolean(), research_enabled: z.literal(false), reason: z.string().max(500) }).strict()

async function boundedJson(response: Response, maximum: number): Promise<unknown> {
  const length = response.headers.get("Content-Length")
  if (length && (!/^\d+$/.test(length) || Number(length) > maximum)) { await response.body?.cancel(); throw new Error("任务响应超过安全大小") }
  const reader = response.body?.getReader()
  if (!reader) throw new Error("任务响应无内容")
  const parts: string[] = []
  const decoder = new TextDecoder("utf-8", { fatal: true })
  let bytes = 0
  try {
    while (true) {
      const chunk = await reader.read()
      if (chunk.done) break
      bytes += chunk.value.byteLength
      if (bytes > maximum) { await reader.cancel(); throw new Error("任务响应超过安全大小") }
      parts.push(decoder.decode(chunk.value, { stream: true }))
    }
    parts.push(decoder.decode())
  } finally { reader.releaseLock() }
  try { return JSON.parse(parts.join("")) } catch { throw new Error("任务响应不是有效JSON，未以空结果替代") }
}

async function request<T>(path: string, schema: z.ZodType<T>, token: string, signal?: AbortSignal, body?: unknown): Promise<T> {
  if (!token) throw new Error("请先登录A股研究会话")
  const timeout = AbortSignal.timeout(12000)
  const response = await fetch(`${API_BASE}${path}`, { method: body === undefined ? "GET" : "POST", body: body === undefined ? undefined : JSON.stringify(body), cache: "no-store", signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
    headers: { Authorization: `Bearer ${token}`, ...(body === undefined ? {} : { "Content-Type": "application/json" }) } })
  const payload = await boundedJson(response, response.ok ? 1024 * 1024 : 64 * 1024)
  if (!response.ok) {
    const failure = z.object({ detail: z.object({ code: z.string().max(100), message: z.string().max(500), request_id: z.string().max(64), retryable: z.boolean() }) }).safeParse(payload)
    throw Object.assign(new Error(failure.success ? failure.data.detail.message : `观察任务请求失败: ${response.status}`), { status: response.status, requestId: failure.success ? failure.data.detail.request_id : response.headers.get("X-Request-ID") })
  }
  const parsed = schema.safeParse(payload)
  if (!parsed.success) throw new Error("任务响应状态、身份或版本格式不一致")
  return parsed.data
}

export function getStockDatasetJobAvailability(token: string, signal?: AbortSignal) { return request("/jobs/availability", availabilitySchema, token, signal) }

export async function listStockDatasetJobs(query: { limit?: number; offset?: number }, token: string, signal?: AbortSignal) {
  const parsed = z.object({ limit: z.number().int().min(1).max(100).default(20), offset: z.number().int().min(0).max(100000).default(0) }).strict().parse(query)
  const result = await request(`/jobs?limit=${parsed.limit}&offset=${parsed.offset}`, pageSchema, token, signal)
  if (result.items.length > parsed.limit || new Set(result.items.map((item) => item.id)).size !== result.items.length || (result.items.length > 0 && parsed.offset + result.items.length > result.total)
    || result.has_more !== (parsed.offset + result.items.length < result.total) || (result.has_more && result.items.length !== parsed.limit)) throw new Error("任务分页、数量或身份不一致")
  return result
}

export async function getStockDatasetJob(identifier: string, token: string, signal?: AbortSignal) {
  const fixed = z.uuid().parse(identifier)
  const result = await request(`/jobs/${fixed}`, detailSchema, token, signal)
  if (result.id !== fixed) throw new Error("返回任务不是所选任务")
  return result
}

export async function submitStockDatasetJob(command: CreateDatasetJob, token: string, signal?: AbortSignal) {
  const fixed = createDatasetJobSchema.parse(command)
  const result = await request("/datasets/jobs", detailSchema, token, signal, fixed)
  if (JSON.stringify(result.parameters) !== JSON.stringify(fixed.parameters)) throw new Error("任务参数与确认的固定来源或区间不一致")
  return result
}

// One path and one body per research kind, mirroring the backend: ``Create*Job.kind`` is
// a literal, so a body can never be posted as another kind.  The submit body is built
// from the same parameter schema the detail view parses, which is what keeps the echoed
// receipt comparable to what the caller confirmed.
const RESEARCH_PATHS = { research_ic: "/ic/jobs", backtest: "/backtest/jobs", benchmark: "/benchmark/jobs", ml_train: "/ml/jobs", report: "/report/jobs" }
function createResearchJobSchema(kind: keyof typeof RESEARCH_PATHS) {
  return z.object({
    kind: z.literal(kind).default(kind), idempotency_key: z.uuid(), parameters: researchParameterSchemas[kind],
    max_runtime_seconds: z.number().int().min(30).max(900).default(kind === "ml_train" || kind === "report" ? 900 : 300),
    max_queue_seconds: z.number().int().min(30).max(900).default(300),
  }).strict()
}
const researchJobSchemas = {
  research_ic: createResearchJobSchema("research_ic"), backtest: createResearchJobSchema("backtest"),
  benchmark: createResearchJobSchema("benchmark"), ml_train: createResearchJobSchema("ml_train"), report: createResearchJobSchema("report"),
}
export type ResearchJobKind = keyof typeof RESEARCH_PATHS

function researchSubmitter<K extends ResearchJobKind>(kind: K) {
  const schema = researchJobSchemas[kind]
  return async function submit(command: z.input<typeof schema>, token: string, signal?: AbortSignal) {
    const fixed = schema.parse(command)
    const result = await request(RESEARCH_PATHS[kind], detailSchema, token, signal, fixed)
    if (result.kind !== kind) throw new Error("返回任务不是所提交的研究类型")
    if (JSON.stringify(result.parameters) !== JSON.stringify(fixed.parameters)) throw new Error("任务参数与确认的固定数据集、因子或区间不一致")
    return result
  }
}

export const submitStockResearchICJob = researchSubmitter("research_ic")
export const submitStockBacktestJob = researchSubmitter("backtest")
export const submitStockBenchmarkJob = researchSubmitter("benchmark")
export const submitStockMLTrainJob = researchSubmitter("ml_train")
export const submitStockReportJob = researchSubmitter("report")

export async function cancelStockDatasetJob(identifier: string, token: string, signal?: AbortSignal) {
  const fixed = z.uuid().parse(identifier)
  const result = await request(`/jobs/${fixed}/cancel`, detailSchema, token, signal, {})
  if (result.id !== fixed || result.status !== "cancelled") throw new Error("未收到所选任务的取消确认")
  return result
}
