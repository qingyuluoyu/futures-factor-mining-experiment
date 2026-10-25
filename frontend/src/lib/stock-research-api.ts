import { z } from "zod"

const preprocess = z.object({ winsorize: z.enum(["mad", "3sigma", "none"]).default("mad"), standardize: z.boolean().default(true), fillna: z.boolean().default(true), industry_neutral: z.boolean().default(false), mktcap_neutral: z.boolean().default(false) }).strict()
export type ResearchPreprocess = z.infer<typeof preprocess>
const period = { start_date: z.string().default("20180101"), end_date: z.string().default("") }
const analysisParameters = z.object({ ...period, factor_id: z.string().min(1).max(40), horizon: z.number().int().min(1).max(120).default(20), groups: z.number().int().min(2).max(10).default(5), preprocess: preprocess.default(() => preprocess.parse({})) }).strict()
const backtestParameters = z.object({ ...period, factor_ids: z.array(z.string().min(1).max(40)).min(1).max(100).refine((values) => new Set(values).size === values.length, "因子不得重复"), rebalance: z.enum(["week", "month"]).default("month"), top_n: z.number().int().min(1).max(300).default(20), weight: z.enum(["equal", "mktcap", "factor"]).default("equal"), commission_rate: z.number().min(0).max(0.01).default(0.0003), slippage_rate: z.number().min(0).max(0.01).default(0.001), preprocess: preprocess.default(() => preprocess.parse({})) }).strict()

function parseResearchDate(value: string): number {
  if (!/^(?:\d{8}|\d{4}-\d{2}-\d{2}|\d{4}\/\d{2}\/\d{2})$/.test(value)) return NaN
  const digits = value.replace(/[-/]/g, "")
  const normalized = `${digits.slice(0, 4)}-${digits.slice(4, 6)}-${digits.slice(6, 8)}`
  const milliseconds = Date.parse(`${normalized}T00:00:00Z`)
  return Number.isFinite(milliseconds) && new Date(milliseconds).toISOString().slice(0, 10) === normalized ? milliseconds : NaN
}

export function researchPeriodError(start: string, end: string): string {
  if (!start) return "请选择研究开始日期"
  const today = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date())
  const startTime = parseResearchDate(start)
  const endTime = parseResearchDate(end || today)
  if (!Number.isFinite(startTime) || !Number.isFinite(endTime)) return "研究日期无效"
  if (startTime > endTime) return "开始日期不得晚于结束日期"
  if (endTime > parseResearchDate(today)) return "结束日期不得晚于今天"
  if ((endTime - startTime) / 86400000 > 3660) return "研究区间不得超过3660天"
  return ""
}

const commonCreate = { idempotency_key: z.uuid(), title: z.string().trim().min(1).max(80).optional() }
export const researchCreateSchema = z.discriminatedUnion("kind", [z.object({ ...commonCreate, kind: z.literal("analysis"), parameters: analysisParameters }).strict(), z.object({ ...commonCreate, kind: z.literal("backtest"), parameters: backtestParameters }).strict()]).superRefine((value, context) => {
  const start = parseResearchDate(value.parameters.start_date)
  const end = value.parameters.end_date === "" ? null : parseResearchDate(value.parameters.end_date)
  const message = !Number.isFinite(start) || (end !== null && !Number.isFinite(end)) ? "研究日期无效" : end !== null && start > end ? "开始日期不得晚于结束日期" : ""
  if (message) context.addIssue({ code: "custom", path: ["parameters", "start_date"], message })
})
export const newResearchCreateSchema = researchCreateSchema.superRefine((value, context) => {
  const message = researchPeriodError(value.parameters.start_date, value.parameters.end_date)
  if (message) context.addIssue({ code: "custom", path: ["parameters", "start_date"], message })
})
export type ResearchCreateInput = z.input<typeof researchCreateSchema>
export const researchStatusSchema = z.enum(["pending", "running", "completed", "failed", "cancelled", "expired"])
export const researchJobSchema = z.object({ id: z.uuid(), kind: z.enum(["analysis", "backtest"]), title: z.string(), status: researchStatusSchema, step: z.string(), progress: z.number().int().min(0).max(100), request_id: z.string(), attempts: z.number().int().min(0).max(1), created_at: z.string(), updated_at: z.string(), started_at: z.string().nullable(), finished_at: z.string().nullable(), method_version: z.string(), workflow_version: z.string(), data_version: z.string().nullable(), has_result: z.boolean(), error: z.object({ code: z.string(), message: z.string(), status: z.number().int() }).nullable(), params: z.record(z.string(), z.unknown()).optional(), data_manifest: z.record(z.string(), z.unknown()).optional() }).superRefine((job, context) => {
  if (job.status === "completed" && (!job.has_result || !job.data_version || !job.finished_at)) context.addIssue({ code: "custom", message: "完成任务缺少结果或数据版本" })
  if (job.status !== "completed" && job.has_result) context.addIssue({ code: "custom", message: "非完成任务不能包含结果" })
})
export type ResearchJob = z.infer<typeof researchJobSchema>
export type ResearchKind = ResearchJob["kind"]
export const researchAvailabilitySchema = z.object({ enabled: z.boolean(), accepting: z.boolean(), saved_report_artifacts: z.boolean().optional().default(false), selective_coverage: z.boolean().optional().default(false), max_active: z.number().int().positive(), max_user_active: z.number().int().positive(), max_runtime_seconds: z.number().positive(), reason: z.string().nullable() })
export type ResearchAvailability = z.infer<typeof researchAvailabilitySchema>
const nav = z.array(z.object({ date: z.string(), nav: z.number() }))
const sampleCount = z.number().int().nonnegative()
const factorPolicySchema = z.object({ factor_id: z.string(), policy_version: z.string().min(1), eligibility_rule: z.enum(["finite", "finite_strictly_positive"]), missing_value_policy: z.enum(["exclude_without_imputation", "cross_sectional_median_if_enabled"]), exclusion_rule: z.string(), count_unit: z.literal("date_security_observations"), start_date: z.iso.date(), end_date: z.iso.date(), counts: z.object({ observations: sampleCount, raw_eligible: sampleCount, excluded_nonpositive: sampleCount, excluded_nonfinite: sampleCount, missing: sampleCount, postprocess_eligible: sampleCount, imputed: sampleCount }) }).superRefine((policy, context) => {
  const counts = policy.counts
  if (counts.raw_eligible + counts.excluded_nonpositive + counts.excluded_nonfinite + counts.missing !== counts.observations || counts.postprocess_eligible > counts.observations || counts.imputed > counts.missing + counts.excluded_nonfinite || (policy.missing_value_policy === "exclude_without_imputation" && counts.imputed !== 0)) context.addIssue({ code: "custom", message: "因子政策样本计数不一致" })
})
export type StockFactorPolicy = z.infer<typeof factorPolicySchema>
const neutralizationPolicySchema = z.object({ version: z.literal("neutralization-identifiability-v1"), min_industry_observations: z.literal(2), min_residual_degrees_of_freedom: z.literal(1), requires_full_column_rank: z.literal(true), insufficient_sample: z.literal("exclude_without_imputation") }).strict()
const metadata = { research_job_id: z.uuid(), data_version: z.string().min(1), data_manifest: z.record(z.string(), z.unknown()), method_version: z.string().min(1), workflow_version: z.string().min(1), validation_status: z.literal("research_only"), validation_limits: z.array(z.string()), market: z.literal("stock"), research_only: z.literal(true), factor_policies: z.array(factorPolicySchema).optional(), neutralization_policy: neutralizationPolicySchema.optional() }
const analysisResultSchema = z.object({ ...metadata, factor_id: z.string(), label: z.string(), group: z.string(), direction: z.string(), ic: z.object({ ic_mean: z.number(), ic_std: z.number(), ic_ir: z.number().nullable(), rank_ic_mean: z.number(), rank_ic_ir: z.number().nullable(), ic_positive_ratio: z.number(), periods: z.number().int() }), decay: z.array(z.object({ lag: z.number(), rank_ic: z.number().nullable() })), quantile_returns: z.array(z.object({ group: z.number(), avg_return: z.number() })), long_short_nav: nav }).passthrough()
const backtestResultSchema = z.object({ ...metadata, metrics: z.object({ total_return_pct: z.number(), annualized_return_pct: z.number(), annualized_vol_pct: z.number(), sharpe: z.number(), sortino: z.number(), calmar: z.number().nullable(), max_drawdown_pct: z.number(), win_rate: z.number(), turnover_rate: z.number(), periods: z.number().int() }), nav, trades: z.array(z.object({ date: z.string(), code: z.string(), name: z.string(), weight: z.number(), action: z.string(), factor_value: z.number().nullable() })), rebalance_count: z.number().int(), params: z.record(z.string(), z.unknown()) }).passthrough()
export type ResearchResult = z.infer<typeof analysisResultSchema> | z.infer<typeof backtestResultSchema>

export function isActiveResearchJob(job: Pick<ResearchJob, "status"> | null): boolean { return job?.status === "pending" || job?.status === "running" }

export function stockResearchScope(userId: string | undefined, token: string | null): string | null {
  if (!userId || !token) return null
  try {
    const encoded = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")
    const payload = JSON.parse(atob(encoded))
    const mode = payload.trading_mode ?? payload.mode
    return payload.sub === userId && (mode === "live" || mode === "virtual") ? `${userId}:${mode}` : null
  } catch { return null }
}

async function researchRequest<Output>(path: string, schema: z.ZodType<Output>, token: string, signal?: AbortSignal, body?: unknown, method = "GET"): Promise<Output> {
  const timeout = AbortSignal.timeout(20000)
  const response = await fetch(`${(process.env.NEXT_PUBLIC_API_URL ?? "").replace(/\/$/, "")}/api/stock/research${path}`, { method, cache: "no-store", headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` }, signal: signal ? AbortSignal.any([signal, timeout]) : timeout, ...(body === undefined ? {} : { body: JSON.stringify(body) }) })
  if (!response.ok) {
    const payload = await response.json().catch(() => null)
    const error = new Error(typeof payload?.detail === "string" ? payload.detail : `研究请求失败: ${response.status}`) as Error & { status: number }
    error.status = response.status
    throw error
  }
  return schema.parse(await response.json())
}

export async function getStockResearchAvailability(token: string, signal?: AbortSignal) { return researchRequest("/availability", researchAvailabilitySchema, token, signal) }
export async function createStockResearchJob(body: ResearchCreateInput, token: string, signal?: AbortSignal) { return researchRequest("/jobs", researchJobSchema, token, signal, researchCreateSchema.parse(body), "POST") }
export async function getStockResearchJob(id: string, token: string, signal?: AbortSignal) {
  const job = await researchRequest(`/jobs/${z.uuid().parse(id)}`, researchJobSchema, token, signal)
  if (job.id !== id) throw new Error("研究任务与请求ID不匹配")
  return job
}
export async function cancelStockResearchJob(id: string, token: string, signal?: AbortSignal) {
  const job = await researchRequest(`/jobs/${z.uuid().parse(id)}/cancel`, researchJobSchema, token, signal, undefined, "POST")
  if (job.id !== id) throw new Error("取消响应与研究任务ID不匹配；请刷新核对，未自动重试")
  return job
}
type ResearchListQuery = { limit?: number; offset?: number; kind?: ResearchKind; status?: ResearchJob["status"] }
async function listResearchJobsAt(path: "/jobs" | "/bookmarks", query: ResearchListQuery, token: string, signal?: AbortSignal) {
  const parsed = z.object({ limit: z.number().int().min(1).max(50).default(20), offset: z.number().int().min(0).max(10000).default(0), kind: z.enum(["analysis", "backtest"]).optional(), status: researchStatusSchema.optional() }).strict().parse(query)
  const parameters = new URLSearchParams()
  Object.entries(parsed).forEach(([key, value]) => { if (value !== undefined) parameters.set(key, String(value)) })
  const page = await researchRequest(`${path}?${parameters}`, z.object({ items: z.array(researchJobSchema).max(50), total: z.number().int().nonnegative(), has_more: z.boolean() }), token, signal)
  if (page.items.length > parsed.limit || new Set(page.items.map((job) => job.id)).size !== page.items.length
    || page.items.some((job) => (parsed.kind !== undefined && job.kind !== parsed.kind) || (parsed.status !== undefined && job.status !== parsed.status))
    || (page.items.length > 0 && parsed.offset + page.items.length > page.total)
    || (page.has_more && page.items.length < parsed.limit)
    || page.has_more !== (parsed.offset + page.items.length < page.total)) throw new Error("研究分页与请求筛选或总数不一致，请刷新核对")
  return page
}
export async function listStockResearchJobs(query: ResearchListQuery, token: string, signal?: AbortSignal) { return listResearchJobsAt("/jobs", query, token, signal) }
export async function listBookmarkedStockResearchJobs(query: ResearchListQuery, token: string, signal?: AbortSignal) {
  const page = await listResearchJobsAt("/bookmarks", query, token, signal)
  if (page.items.some((job) => job.status !== "completed" || !job.has_result)) throw new Error("收藏列表包含未完成报告，请刷新核对")
  return page
}

const bookmarkAvailabilitySchema = z.object({ enabled: z.boolean(), reason: z.string().nullable() })
export async function getStockReportBookmarkAvailability(token: string, signal?: AbortSignal) {
  try { return await researchRequest("/bookmarks/availability", bookmarkAvailabilitySchema, token, signal) }
  catch (error) {
    if ((error as { status?: number }).status === 404) return { enabled: false, reason: "当前服务尚未部署报告收藏" }
    throw error
  }
}
const bookmarkSchema = z.object({ job_id: z.uuid(), market: z.literal("stock"), bookmarked: z.boolean(), created_at: z.iso.datetime({ offset: true }).nullable() }).refine((value) => value.bookmarked === (value.created_at !== null), "收藏状态与时间不一致")
export type StockReportBookmark = z.infer<typeof bookmarkSchema>
export async function getStockReportBookmark(id: string, token: string, signal?: AbortSignal) {
  const view = await researchRequest(`/jobs/${z.uuid().parse(id)}/bookmark`, bookmarkSchema, token, signal)
  if (view.job_id !== id) throw new Error("收藏响应与报告ID不匹配")
  return view
}
export async function setStockReportBookmark(id: string, bookmarked: boolean, token: string, signal?: AbortSignal) {
  const body = z.object({ bookmarked: z.boolean() }).strict().parse({ bookmarked })
  const view = await researchRequest(`/jobs/${z.uuid().parse(id)}/bookmark`, bookmarkSchema, token, signal, body, "PUT")
  if (view.job_id !== id || view.bookmarked !== bookmarked) throw new Error("收藏响应未确认目标状态，请刷新核对；未自动重写")
  return view
}
export async function getStockResearchResult(id: string, kind: ResearchKind, token: string, signal?: AbortSignal): Promise<ResearchResult> {
  const result = await researchRequest<ResearchResult>(`/jobs/${z.uuid().parse(id)}/result`, kind === "analysis" ? analysisResultSchema : backtestResultSchema, token, signal)
  if (result.research_job_id !== id) throw new Error("报告与研究任务不匹配")
  return result
}
