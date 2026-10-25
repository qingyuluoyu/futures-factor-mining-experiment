import { z } from "zod"

const API_BASE = `${(process.env.NEXT_PUBLIC_API_URL ?? "").replace(/\/$/, "")}/api/stock/research/v2/models`
const sha = z.string().regex(/^[a-f0-9]{64}$/)
const date = z.iso.date()
const utc = z.iso.datetime({ offset: true }).refine((value) => /(?:Z|\+00:00)$/.test(value), "模型时间必须为UTC")
const feature = z.string().min(1).max(200)
const features = z.array(feature).min(1).max(16).refine((value) => new Set(value).size === value.length, "特征重复")
const datasetSchema = z.strictObject({ dataset_id: z.uuid(), data_version: sha, qualification: z.enum(["observation_only", "research_only", "backtest_ready"]) })
const status = z.enum(["qa_only", "research_only"])
export const stockModelParametersSchema = z.strictObject({
  n_estimators: z.number().int().min(1).max(500), max_depth: z.number().int().min(1).max(8), num_leaves: z.number().int().min(2).max(63),
  min_child_samples: z.number().int().min(1).max(10000), learning_rate: z.number().positive().max(0.5), reg_alpha: z.number().min(0).max(100), reg_lambda: z.number().min(0).max(100),
  seed: z.number().int().min(0).max(2147483647), threads: z.literal(2), max_seconds: z.number().int().min(1).max(600),
}).refine((value) => value.num_leaves <= 2 ** value.max_depth, "叶子数超过深度上限")
const splitSchema = z.strictObject({ train_start: date, train_end: date, validation_start: date, validation_end: date, test_start: date, test_end: date })
  .refine((v) => v.train_start <= v.train_end && v.train_end < v.validation_start && v.validation_start <= v.validation_end && v.validation_end < v.test_start && v.test_start <= v.test_end, "训练、验证和测试日期重叠")
function sameKeys(keys: string[], record: Record<string, unknown>) { return keys.length === Object.keys(record).length && keys.every((key) => Object.hasOwn(record, key)) }
const manifestSchema = z.strictObject({
  method_version: z.literal("stock-native-lambdarank-v1"), scope: z.strictObject({ owner_id: z.uuid(), trading_mode: z.enum(["virtual", "live"]) }), dataset: datasetSchema, parameters: stockModelParametersSchema,
  runtime: z.record(z.string().min(1).max(200), z.string().max(2000)).refine((v) => Object.keys(v).length >= 5 && Object.keys(v).length <= 10),
  feature_order: features, feature_versions: z.record(feature, sha), training_sha256: sha, validation_sha256: sha, model_sha256: sha, training_knowledge_cutoff: date,
  created_at: utc, trees_used: z.number().int().min(1).max(500), elapsed_seconds: z.number().nonnegative(), feature_gain: z.record(feature, z.number().nonnegative()),
  validation_status: status, holdout_registry_verified: z.literal(false), model_api_calls: z.literal(0),
}).refine((v) => sameKeys(v.feature_order, v.feature_versions) && sameKeys(v.feature_order, v.feature_gain), "模型特征版本或重要性不一致")
const summaryFields = {
  id: z.uuid(), dataset: datasetSchema, state: z.enum(["ready", "quarantined"]), validation_status: status, created_at: utc,
  fold_count: z.number().int().min(1).max(5), feature_names: features, holdout_registry_verified: z.literal(false),
}
export const stockModelSummarySchema = z.strictObject(summaryFields)
export const stockModelDetailSchema = z.strictObject({ ...summaryFields, parameters: stockModelParametersSchema,
  folds: z.array(z.strictObject({ fold_id: sha, split: splitSchema, model: manifestSchema })).min(1).max(5), report_sha256: sha, limitations: z.array(z.string().min(1).max(4000)).min(1).max(30),
}).refine((v) => v.folds.length === v.fold_count && new Set(v.folds.map((fold) => fold.fold_id)).size === v.fold_count && v.folds.every(({ model, split }) =>
  model.dataset.dataset_id === v.dataset.dataset_id && model.dataset.data_version === v.dataset.data_version && model.dataset.qualification === v.dataset.qualification
  && JSON.stringify(model.feature_order) === JSON.stringify(v.feature_names) && Object.entries(v.parameters).every(([key, value]) => model.parameters[key as keyof typeof model.parameters] === value)
  && model.validation_status === v.validation_status && model.training_knowledge_cutoff < split.test_start), "模型分段、数据、特征或参数不一致")
const availabilitySchema = z.strictObject({ listing_enabled: z.boolean(), training_enabled: z.literal(false), reason: z.string().min(1).max(1000) })
const pageSchema = z.strictObject({ items: z.array(stockModelSummarySchema).max(100), total: z.number().int().nonnegative(), has_more: z.boolean() })
// Reports are versioned documents, not an instruction protocol; retain finite JSON only.
const reportBodySchema = z.record(z.string(), z.unknown()).refine((value) => {
  const pending: { value: unknown; depth: number }[] = [{ value, depth: 0 }]
  let count = 0
  while (pending.length) {
    const item = pending.pop()!
    if (++count > 100000 || item.depth > 30) return false
    if (item.value === null || typeof item.value === "string" || typeof item.value === "boolean") continue
    if (typeof item.value === "number") { if (!Number.isFinite(item.value)) return false; continue }
    if (typeof item.value !== "object") return false
    for (const child of Object.values(item.value)) pending.push({ value: child, depth: item.depth + 1 })
  }
  return true
}, "报告不是有界有限JSON")
export const stockModelReportSchema = z.strictObject({ kind: z.literal("stock-walk-forward-calculation-v1"), report: reportBodySchema })
export type StockModelSummary = z.infer<typeof stockModelSummarySchema>
export type StockModelDetail = z.infer<typeof stockModelDetailSchema>
export type StockModelAvailability = z.infer<typeof availabilitySchema>
export type StockModelPage = z.infer<typeof pageSchema>
export type StockModelReport = z.infer<typeof stockModelReportSchema>

async function boundedJson(response: Response): Promise<unknown> {
  const maximum = 8 * 1024 * 1024
  const declared = response.headers.get("Content-Length")
  if (declared && (!/^\d+$/.test(declared) || Number(declared) > maximum)) { await response.body?.cancel(); throw new Error("模型历史响应超过安全大小") }
  const reader = response.body?.getReader()
  if (!reader) throw new Error("模型历史响应无内容")
  const decoder = new TextDecoder("utf-8", { fatal: true }), parts: string[] = []
  let bytes = 0
  try {
    while (true) {
      const chunk = await reader.read()
      if (chunk.done) break
      bytes += chunk.value.byteLength
      if (bytes > maximum) { await reader.cancel(); throw new Error("模型历史响应超过安全大小") }
      parts.push(decoder.decode(chunk.value, { stream: true }))
    }
    parts.push(decoder.decode())
  } finally { reader.releaseLock() }
  return JSON.parse(parts.join(""))
}
async function request<T>(path: string, schema: z.ZodType<T>, token: string, signal?: AbortSignal): Promise<T> {
  if (!token) throw new Error("请先登录A股研究会话")
  const timeout = AbortSignal.timeout(12000)
  const response = await fetch(`${API_BASE}${path}`, { method: "GET", cache: "no-store", signal: signal ? AbortSignal.any([signal, timeout]) : timeout, headers: { Authorization: `Bearer ${token}` } })
  let payload: unknown
  try { payload = await boundedJson(response) } catch (error) { if (response.ok) throw error }
  if (!response.ok) {
    const failure = z.strictObject({ detail: z.strictObject({ code: z.string().max(100), message: z.string().max(500), request_id: z.string().max(64), retryable: z.boolean() }) }).safeParse(payload)
    const retryable = [408, 429, 500, 502, 503, 504].includes(response.status) && (!failure.success || failure.data.detail.retryable)
    throw Object.assign(new Error(failure.success ? failure.data.detail.message : `模型历史请求失败：${response.status}`), { status: response.status, retryable, requestId: failure.success ? failure.data.detail.request_id : response.headers.get("X-Request-ID") })
  }
  const parsed = schema.safeParse(payload)
  if (!parsed.success) throw new Error("模型历史响应结构无效，请重新读取核对")
  return parsed.data
}
export function getStockModelAvailability(token: string, signal?: AbortSignal) { return request("/availability", availabilitySchema, token, signal) }
export async function listStockModels(query: { limit?: number; offset?: number }, token: string, signal?: AbortSignal): Promise<StockModelPage> {
  const parsed = z.strictObject({ limit: z.number().int().min(1).max(100).default(20), offset: z.number().int().min(0).max(100000).default(0) }).parse(query)
  const page = await request(`?limit=${parsed.limit}&offset=${parsed.offset}`, pageSchema, token, signal)
  if (page.items.length > parsed.limit || new Set(page.items.map((item) => item.id)).size !== page.items.length || (page.items.length > 0 && parsed.offset + page.items.length > page.total)
    || page.has_more !== (parsed.offset + page.items.length < page.total) || (page.has_more && page.items.length !== parsed.limit)) throw new Error("模型分页与总数不一致")
  return page
}
export async function getStockModel(id: string, token: string, signal?: AbortSignal, scope?: string): Promise<StockModelDetail> {
  const identifier = z.uuid().parse(id)
  const result = await request(`/${identifier}`, stockModelDetailSchema, token, signal)
  if (result.id !== identifier || (scope && result.folds.some(({ model }) => `${model.scope.owner_id}:${model.scope.trading_mode}` !== scope))) throw new Error("响应与本人模型身份不一致")
  return result
}
export function getStockModelReport(id: string, token: string, signal?: AbortSignal) { return request(`/${z.uuid().parse(id)}/report`, stockModelReportSchema, token, signal) }
