import { z } from "zod"

const API_BASE = `${(process.env.NEXT_PUBLIC_API_URL ?? "").replace(/\/$/, "")}/api/stock/research/v2/custom-factors`
const expression = z.string().trim().min(1).max(4096)
const revision = z.number().int().min(1).max(2147483646)
const storedRevision = z.number().int().min(1).max(2147483647)
const fields = z.array(z.enum(["close", "pe_ttm", "pb", "revenue_yoy", "netprofit_yoy", "roe", "gross_margin", "turnover", "marketcap"])).min(1).max(9)
const compiledSchema = z.object({ expression, expression_version: z.string().regex(/^[0-9a-f]{64}$/), formula_version: z.literal("stock-expression-v1"), fields, lookback: z.number().int().min(0).max(244) })
export const customFactorSchema = compiledSchema.extend({ id: z.uuid(), revision: storedRevision, latest_revision: storedRevision, name: z.string().min(1).max(80), direction: z.enum(["high", "low"]), description: z.string().max(1000), disabled: z.boolean(), created_at: z.iso.datetime({ offset: true }) }).refine((value) => value.revision <= value.latest_revision, "版本关系无效")
export const customFactorCommandSchema = z.object({ idempotency_key: z.uuid(), name: z.string().trim().min(1).max(80), expression, direction: z.enum(["high", "low"]), description: z.string().trim().max(1000).default("") }).strict()
const disableSchema = z.object({ idempotency_key: z.uuid(), expected_revision: storedRevision }).strict()
const availabilitySchema = z.object({ definitions_enabled: z.boolean(), research_enabled: z.literal(false), reason: z.string().max(500), fields: z.array(z.object({ id: z.string().max(40), unit: z.string().max(40), availability: z.string().max(300) })).max(16) })
const pageSchema = z.object({ items: z.array(customFactorSchema).max(100), total: z.number().int().nonnegative(), has_more: z.boolean() })
export type CustomFactor = z.infer<typeof customFactorSchema>
export type CustomFactorCommand = z.infer<typeof customFactorCommandSchema>
export type CustomFactorAvailability = z.infer<typeof availabilitySchema>
export type CompiledFactor = z.infer<typeof compiledSchema>
export type CustomFactorPage = z.infer<typeof pageSchema>

async function boundedJson(response: Response): Promise<unknown> {
  const maximum = 1024 * 1024
  const declared = response.headers.get("Content-Length")
  if (declared && (!/^\d+$/.test(declared) || Number(declared) > maximum)) {
    await response.body?.cancel()
    throw new Error("自定义因子响应超过安全大小")
  }
  const reader = response.body?.getReader()
  if (!reader) throw new Error("自定义因子响应无内容")
  let bytes = 0
  const decoder = new TextDecoder("utf-8", { fatal: true })
  const parts: string[] = []
  try {
    while (true) {
      const chunk = await reader.read()
      if (chunk.done) break
      bytes += chunk.value.byteLength
      if (bytes > maximum) { await reader.cancel(); throw new Error("自定义因子响应超过安全大小") }
      parts.push(decoder.decode(chunk.value, { stream: true }))
    }
    parts.push(decoder.decode())
  } finally { reader.releaseLock() }
  return JSON.parse(parts.join(""))
}

async function request<T>(path: string, schema: z.ZodType<T>, token: string, signal?: AbortSignal, method = "GET", body?: unknown): Promise<T> {
  if (!token) throw new Error("请先登录A股研究会话")
  const timeout = AbortSignal.timeout(12000)
  const response = await fetch(`${API_BASE}${path}`, { method, cache: "no-store", signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) })
  const payload = await boundedJson(response)
  if (!response.ok) {
    const failure = z.object({ detail: z.object({ code: z.string().max(100), message: z.string().max(500), request_id: z.string().max(64), retryable: z.boolean() }) }).safeParse(payload)
    throw Object.assign(new Error(failure.success ? failure.data.detail.message : `自定义因子请求失败: ${response.status}`), { status: response.status, requestId: failure.success ? failure.data.detail.request_id : response.headers.get("X-Request-ID"), code: failure.success ? failure.data.detail.code : "request_failed" })
  }
  return schema.parse(payload)
}

export async function getCustomFactorAvailability(token: string, signal?: AbortSignal) {
  return request("/availability", availabilitySchema, token, signal)
}

export async function validateCustomFactor(value: string, token: string, signal?: AbortSignal) {
  return request("/validate", compiledSchema, token, signal, "POST", { expression: expression.parse(value) })
}

export async function saveCustomFactor(body: CustomFactorCommand, token: string, signal?: AbortSignal, editing?: { id: string; expected_revision: number }) {
  const parsed = customFactorCommandSchema.parse(body)
  const target = editing && z.object({ id: z.uuid(), expected_revision: revision }).strict().parse(editing)
  const result = await request(target ? `/${target.id}` : "", customFactorSchema, token, signal, target ? "PUT" : "POST", target ? { ...parsed, expected_revision: target.expected_revision } : parsed)
  if (target && (result.id !== target.id || result.revision !== target.expected_revision + 1)) throw new Error("保存响应与预期因子版本不一致，请刷新核对")
  return result
}

export async function disableCustomFactor(id: string, body: z.infer<typeof disableSchema>, token: string, signal?: AbortSignal) {
  const identifier = z.uuid().parse(id)
  const parsed = disableSchema.parse(body)
  const result = await request(`/${identifier}/disable`, customFactorSchema, token, signal, "POST", parsed)
  if (result.id !== identifier || result.revision !== parsed.expected_revision || !result.disabled) throw new Error("停用响应与预期版本不一致，请刷新核对")
  return result
}

export async function getCustomFactorVersion(id: string, version: number, token: string, signal?: AbortSignal) {
  const identifier = z.uuid().parse(id)
  const selected = storedRevision.parse(version)
  const result = await request(`/${identifier}/versions/${selected}`, customFactorSchema, token, signal)
  if (result.id !== identifier || result.revision !== selected) throw new Error("响应与指定因子版本不一致")
  return result
}

export async function listCustomFactors(query: { limit?: number; offset?: number }, token: string, signal?: AbortSignal, factorId?: string) {
  const parsed = z.object({ limit: z.number().int().min(1).max(100).default(20), offset: z.number().int().min(0).max(100000).default(0) }).strict().parse(query)
  const id = factorId && z.uuid().parse(factorId)
  const page = await request(`${id ? `/${id}/versions` : ""}?limit=${parsed.limit}&offset=${parsed.offset}`, pageSchema, token, signal)
  if (page.items.length > parsed.limit || new Set(page.items.map((item) => `${item.id}:${item.revision}`)).size !== page.items.length
    || (page.items.length > 0 && parsed.offset + page.items.length > page.total)
    || page.has_more !== (parsed.offset + page.items.length < page.total) || (page.has_more && page.items.length !== parsed.limit)
    || page.items.some((item) => id ? item.id !== id : item.revision !== item.latest_revision)) throw new Error("自定义因子分页与总数或版本不一致")
  return page
}
