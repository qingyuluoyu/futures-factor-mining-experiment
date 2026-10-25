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
const fields = {
  id: z.uuid(), kind: z.literal("dataset"), status: z.enum(["pending", "running", "completed", "failed", "cancelled", "expired"]), step: z.string().max(64),
  attempts: z.number().int().min(0).max(1), request_id: z.string().regex(/^[a-f0-9]{32}$/),
  created_at: dateTime, updated_at: dateTime, started_at: dateTime.nullable(), finished_at: dateTime.nullable(), deadline_at: dateTime.nullable(), queue_expires_at: dateTime,
  method_version: z.literal("stock-observation-manifest-v1"), workflow_version: z.literal("stock-v2-dataset-job-v1"),
  result: z.object({ dataset_id: z.uuid(), data_version: digest, qualification: z.literal("observation_only") }).strict().nullable(), error_code: z.string().max(100).nullable(),
}
function stateIsConsistent(value: z.infer<z.ZodObject<typeof fields>>) {
  return (value.status === "completed") === (value.result !== null)
    && (["pending", "running"].includes(value.status)) === (value.finished_at === null)
    && (value.status !== "running" || (value.attempts === 1 && value.started_at !== null && value.deadline_at !== null))
    && (value.status !== "pending" || (value.attempts === 0 && value.started_at === null && value.deadline_at === null))
    && (value.status !== "completed" || (value.attempts === 1 && value.started_at !== null))
}
const summarySchema = z.object(fields).strict().refine(stateIsConsistent, "任务状态、结果或执行时间不一致")
const detailSchema = z.object({ ...fields, parameters: parametersSchema }).strict().refine(stateIsConsistent, "任务状态、结果或执行时间不一致")
export type DatasetJobSummary = z.infer<typeof summarySchema>
export type DatasetJobDetail = z.infer<typeof detailSchema>
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

export async function cancelStockDatasetJob(identifier: string, token: string, signal?: AbortSignal) {
  const fixed = z.uuid().parse(identifier)
  const result = await request(`/jobs/${fixed}/cancel`, detailSchema, token, signal, {})
  if (result.id !== fixed || result.status !== "cancelled") throw new Error("未收到所选任务的取消确认")
  return result
}
