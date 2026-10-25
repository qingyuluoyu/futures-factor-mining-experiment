import { z } from "zod"

const count = z.number().int().nonnegative()
const date = z.iso.date()
const hash = z.string().regex(/^[a-f0-9]{64}$/)
export const eodGapQuerySchema = z.object({
  kind: z.enum(["all", "missing", "unknown"]).default("all"), keyword: z.string().max(64).default(""),
  limit: z.number().int().min(1).max(100).default(50), offset: z.number().int().min(0).max(20000).default(0), expected_fingerprint: hash.nullable().default(null),
}).strict()

const itemSchema = z.object({
  code: z.string().regex(/^[0-9]{6}$/), kind: z.enum(["missing", "unknown"]), name: z.string().nullable(), industry: z.string().nullable(),
  catalog_status: z.enum(["present_now", "absent_now", "exchange_conflict_now"]), instrument_id: z.string().regex(/^[0-9]{6}\.(SH|SZ|BJ)$/).nullable(),
  instrument_id_basis: z.enum(["source_snapshot", "current_catalog", "unavailable"]), current_list_date: date.nullable(),
  history: z.object({ rows: count, first_date: date.nullable(), last_date: date.nullable() }).strict(),
}).strict()
const snapshotSchema = z.object({
  trade_date: date, data_version: hash, provider: z.literal("user_authorized_teajoin_tushare_compatible"), source_uri: z.literal("https://teajoin.com/debug/api/daily"),
  collected_at: z.iso.datetime({ offset: true }), price_adjustment: z.literal("none"), volume_unit: z.literal("lot_100_shares"),
  license_scope: z.literal("operator_configured_account_not_redistribution_certified"), source_evidence_recorded: z.boolean(),
  verification: z.literal("stored_metadata_only_not_independent_certification"), history_adjustment: z.literal("legacy_qfq_unversioned"),
}).strict()
const responseSchema = z.object({
  gap_version: z.literal("stock-eod-gaps-v1"), state: z.enum(["ready", "disabled", "no_snapshot"]), snapshot: snapshotSchema.nullable(),
  totals: z.object({ missing: count.max(10000), unknown: count.max(10000) }).strict().nullable(), query_fingerprint: hash.nullable(),
  items: z.array(itemSchema).max(100), total: count.max(20000), has_more: z.boolean(), applied_query: eodGapQuerySchema,
}).strict().superRefine((value, context) => {
  const fail = () => context.addIssue({ code: "custom", message: "盘后缺口响应口径冲突" })
  if (value.applied_query.expected_fingerprint !== null && (value.state !== "ready" || value.query_fingerprint !== value.applied_query.expected_fingerprint)) fail()
  if (value.state !== "ready") {
    if (value.snapshot !== null || value.totals !== null || value.query_fingerprint !== null || value.items.length !== 0 || value.total !== 0 || value.has_more) fail()
    return
  }
  if (!value.snapshot || !value.totals || !value.query_fingerprint) { fail(); return }
  const expectedSize = Math.min(value.applied_query.limit, Math.max(0, value.total - value.applied_query.offset))
  const categoryTotal = value.applied_query.kind === "all" ? value.totals.missing + value.totals.unknown : value.totals[value.applied_query.kind]
  if (value.total > categoryTotal || (!value.applied_query.keyword.trim() && value.total !== categoryTotal) || value.items.filter((item) => item.kind === "missing").length > value.totals.missing || value.items.filter((item) => item.kind === "unknown").length > value.totals.unknown) fail()
  if (value.total > value.totals.missing + value.totals.unknown || value.items.length !== expectedSize || value.has_more !== (value.applied_query.offset + value.applied_query.limit < value.total) || new Set(value.items.map((item) => item.code)).size !== value.items.length) fail()
  for (const item of value.items) {
    const history = item.history
    if (history.rows === 0 ? history.first_date !== null || history.last_date !== null : !history.first_date || !history.last_date || history.first_date > history.last_date || history.last_date > value.snapshot.trade_date) fail()
    if (item.instrument_id !== null && !item.instrument_id.startsWith(item.code + ".")) fail()
    if (value.applied_query.kind !== "all" && item.kind !== value.applied_query.kind) fail()
    if (item.kind === "unknown" && (item.instrument_id === null || item.instrument_id_basis !== "source_snapshot")) fail()
  }
})

export type EodGapQuery = z.infer<typeof eodGapQuerySchema>
export type EodGapResponse = z.infer<typeof responseSchema>

export async function getStockEodGaps(input: Partial<EodGapQuery>, token: string, signal?: AbortSignal): Promise<EodGapResponse> {
  const query = eodGapQuerySchema.parse(input)
  if (!token || token.length > 8192) throw new Error("请先登录后查看盘后缺口")
  signal?.throwIfAborted()
  const parameters = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) if (value !== null) parameters.set(key, String(value))
  const timeout = AbortSignal.timeout(20000)
  const response = await fetch(`${(process.env.NEXT_PUBLIC_API_URL ?? "").replace(/\/$/, "")}/api/stock/market/eod-gaps?${parameters}`, { method: "GET", cache: "no-store", headers: { Authorization: `Bearer ${token}` }, signal: signal ? AbortSignal.any([signal, timeout]) : timeout })
  if (!response.ok) {
    const changed = response.status === 409 && response.headers.get("X-Stock-Gap-Error") === "snapshot_changed"
    throw Object.assign(new Error(changed ? "盘后快照或目录已变化，请重新应用条件返回第一页；未混合分页" : `盘后缺口读取失败（HTTP ${response.status}），请重试；未用空数据代替失败`), { status: response.status })
  }
  const result = responseSchema.safeParse(await response.json())
  if (!result.success || JSON.stringify(result.data.applied_query) !== JSON.stringify(query)) throw new Error("盘后缺口响应或查询回显异常，请核查后端版本；未回退为零缺口")
  return result.data
}
