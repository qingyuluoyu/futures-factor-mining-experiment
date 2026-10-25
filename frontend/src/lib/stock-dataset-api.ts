import { z } from "zod"

const API_BASE = `${(process.env.NEXT_PUBLIC_API_URL ?? "").replace(/\/$/, "")}/api/stock/research/v2`
const digest = z.string().regex(/^[0-9a-f]{64}$/)
const qualification = z.enum(["observation_only", "research_only", "backtest_ready"])
export const datasetRefSchema = z.object({ dataset_id: z.uuid(), data_version: digest, qualification }).strict()
export type DatasetRef = z.infer<typeof datasetRefSchema>
const itemSchema = datasetRefSchema.extend({ artifact_state: z.enum(["ready", "quarantined"]), integrity: z.literal("not_checked"), created_at: z.iso.datetime({ offset: true }) })
export type DatasetCatalogItem = z.infer<typeof itemSchema>
// These three are plain booleans on the server: ``research_enabled`` is
// ``ready && qualification_ready && artifact_root``, ``inspection_enabled`` is
// ``ready && qualification_ready`` and ``parquet_enabled`` mirrors the columnar
// engine.  They are false today only because migration 020 is not applied yet, so
// pinning them to ``false`` here would make the client reject the real answer — and
// break this panel — on the day 020 is registered.  The backend still refuses every
// graded capability until then, so accepting ``true`` grants nothing by itself.
const availabilitySchema = z.object({ catalog_enabled: z.boolean(), detail_enabled: z.boolean(), inspection_enabled: z.boolean(), research_enabled: z.boolean(), parquet_enabled: z.boolean(), reason: z.string().max(500) }).strict()
export type DatasetAvailability = z.infer<typeof availabilitySchema>
const pageSchema = z.object({ items: z.array(itemSchema).max(100), total: z.number().int().nonnegative(), has_more: z.boolean() }).strict()
export type DatasetCatalogPage = z.infer<typeof pageSchema>
const instrument = z.string().regex(/^(?:6\d{5}\.SH|[03]\d{5}\.SZ|[489]\d{5}\.BJ)$/)
const sourceSchema = z.object({ source_id: z.uuid(), sha256: digest, byte_size: z.number().int().positive().max(64 * 1024 * 1024), row_count: z.number().int().min(1).max(10000), kind: z.enum(["daily", "valuation", "financial"]) }).strict()
const descriptionSchema = z.object({ reference: datasetRefSchema, manifest_version: z.literal("stock-observation-manifest-v1"), start: z.iso.date(), end: z.iso.date(), warmup_start: z.iso.date(), pool_id: z.string().min(1).max(100), pool_version: digest, instruments: z.array(instrument).min(1).max(10000), sources: z.array(sourceSchema).min(1).max(1000), integrity: z.literal("manifest_verified_sources_not_checked"), research_enabled: z.boolean(), limitations: z.array(z.string().max(500)).max(20) }).strict().superRefine((value, context) => {
  if (!(value.warmup_start <= value.start && value.start <= value.end)
    || new Set(value.instruments).size !== value.instruments.length
    || new Set(value.sources.map((source) => source.source_id)).size !== value.sources.length
    || value.sources.reduce((total, source) => total + source.byte_size, 0) > 64 * 1024 * 1024
    || value.sources.reduce((total, source) => total + source.row_count, 0) > 1000000) context.addIssue({ code: "custom", message: "清单区间、身份或资源声明不一致" })
})
export type DatasetDescription = z.infer<typeof descriptionSchema>

async function boundedJson(response: Response, maximum: number): Promise<unknown> {
  const declared = response.headers.get("Content-Length")
  if (declared && (!/^\d+$/.test(declared) || Number(declared) > maximum)) { await response.body?.cancel(); throw new Error("数据目录响应超过安全大小") }
  const reader = response.body?.getReader()
  if (!reader) throw new Error("数据目录响应无内容")
  const parts: string[] = []
  const decoder = new TextDecoder("utf-8", { fatal: true })
  let bytes = 0
  try {
    while (true) {
      const chunk = await reader.read()
      if (chunk.done) break
      bytes += chunk.value.byteLength
      if (bytes > maximum) { await reader.cancel(); throw new Error("数据目录响应超过安全大小") }
      parts.push(decoder.decode(chunk.value, { stream: true }))
    }
    parts.push(decoder.decode())
  } finally { reader.releaseLock() }
  try { return JSON.parse(parts.join("")) } catch { throw new Error("数据目录响应不是有效JSON，未以空数据替代") }
}

async function request<T>(path: string, schema: z.ZodType<T>, token: string, signal?: AbortSignal): Promise<T> {
  if (!token) throw new Error("请先登录A股研究会话")
  const timeout = AbortSignal.timeout(12000)
  const response = await fetch(`${API_BASE}${path}`, { method: "GET", cache: "no-store", signal: signal ? AbortSignal.any([signal, timeout]) : timeout, headers: { Authorization: `Bearer ${token}` } })
  const payload = await boundedJson(response, response.ok ? 1024 * 1024 : 64 * 1024)
  if (!response.ok) {
    const failure = z.object({ detail: z.object({ code: z.string().max(100), message: z.string().max(500), request_id: z.string().max(64), retryable: z.boolean() }) }).safeParse(payload)
    throw Object.assign(new Error(failure.success ? failure.data.detail.message : `固定数据请求失败: ${response.status}`), { status: response.status, requestId: failure.success ? failure.data.detail.request_id : response.headers.get("X-Request-ID") })
  }
  const parsed = schema.safeParse(payload)
  if (!parsed.success) throw new Error("数据目录响应字段、数量或版本格式不一致")
  return parsed.data
}

export function getStockDatasetAvailability(token: string, signal?: AbortSignal) {
  return request("/availability", availabilitySchema, token, signal)
}

export async function listStockDatasets(query: { limit?: number; offset?: number }, token: string, signal?: AbortSignal) {
  const parsed = z.object({ limit: z.number().int().min(1).max(100).default(20), offset: z.number().int().min(0).max(100000).default(0) }).strict().parse(query)
  const page = await request(`/datasets?limit=${parsed.limit}&offset=${parsed.offset}`, pageSchema, token, signal)
  if (page.items.length > parsed.limit || new Set(page.items.map((item) => item.dataset_id)).size !== page.items.length
    || (page.items.length > 0 && parsed.offset + page.items.length > page.total) || page.has_more !== (parsed.offset + page.items.length < page.total)
    || (page.has_more && page.items.length !== parsed.limit)) throw new Error("数据目录分页、总数或身份不一致")
  return page
}

export async function getStockDatasetDescription(reference: DatasetRef, token: string, signal?: AbortSignal) {
  const fixed = datasetRefSchema.parse(reference)
  const result = await request(`/datasets/${fixed.dataset_id}?data_version=${fixed.data_version}&qualification=${fixed.qualification}`, descriptionSchema, token, signal)
  if (result.reference.dataset_id !== fixed.dataset_id || result.reference.data_version !== fixed.data_version || result.reference.qualification !== fixed.qualification) throw new Error("响应与指定数据集版本不一致，未切换为最新")
  return result
}
