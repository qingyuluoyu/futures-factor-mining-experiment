import { z } from "zod"

const hash = z.string().regex(/^[a-f0-9]{64}$/)
const count = z.number().int().min(0).max(10000)
const timestamp = z.iso.datetime({ offset: true })
const archiveItem = z.object({
  batch_id: z.uuid(), version: hash, source_data_version: hash, catalog_version: hash,
  method_version: z.literal("stock-daily-archive-2026-10-19.v1"), trade_date: z.iso.date(), source_collected_at: timestamp, archived_at: timestamp,
  row_count: count.min(1), expected_count: count.min(1), missing_count: count, unknown_count: count, source_row_count: count,
  market: z.literal("CN_A"), currency: z.literal("CNY"), timezone: z.literal("Asia/Shanghai"), frequency: z.literal("daily"),
  price_adjustment: z.literal("none"), volume_unit: z.literal("lot_100_shares"), amount_unit: z.literal("CNY"),
  provider: z.literal("user_authorized_teajoin_tushare_compatible"), source_uri: z.literal("https://teajoin.com/debug/api/daily"),
  reference_price: z.literal("vendor_ex_rights_pre_close"), license_scope: z.literal("operator_configured_account_not_redistribution_certified"),
  verification: z.literal("metadata_only_not_row_or_PIT_certification"), research_ready: z.literal(false), replay_ready: z.literal(false),
}).strict().superRefine((item, context) => {
  const collected = Date.parse(item.source_collected_at), archived = Date.parse(item.archived_at)
  if (!Number.isFinite(collected) || !Number.isFinite(archived)) { context.addIssue({ code: "custom", message: "归档目录时间无效" }); return }
  const local = new Date(collected + 8 * 3600000).toISOString()
  if (item.row_count + item.missing_count !== item.expected_count || item.row_count + item.unknown_count !== item.source_row_count || collected > archived || item.trade_date > local.slice(0, 10) || (item.trade_date === local.slice(0, 10) && local.slice(11, 16) < "16:30")) context.addIssue({ code: "custom", message: "归档目录覆盖或时点不一致" })
})
export const stockArchiveCatalogSchema = z.object({ contract_version: z.literal("stock-archive-catalog-v1"), snapshot_at: timestamp, membership_token: z.string().regex(/^[a-f0-9]{32}$/),
  items: z.array(archiveItem).max(20), total: z.number().int().nonnegative(), has_more: z.boolean() }).strict().superRefine((page, context) => {
  if (new Set(page.items.map((item) => item.batch_id)).size !== page.items.length || new Set(page.items.map((item) => item.version)).size !== page.items.length || page.items.some((item) => Date.parse(item.archived_at) > Date.parse(page.snapshot_at))) context.addIssue({ code: "custom", message: "归档目录重复或晚于快照" })
})
export type StockArchiveCatalogPage = z.infer<typeof stockArchiveCatalogSchema>
const querySchema = z.object({ limit: z.number().int().min(1).max(20).optional(), offset: z.number().int().min(0).max(10000).optional(), trade_date: z.iso.date().optional(), snapshot_at: timestamp.optional(), membership_token: z.string().regex(/^[a-f0-9]{32}$/).optional() }).strict().superRefine((query, context) => {
  if (Boolean(query.snapshot_at) !== Boolean(query.membership_token) || ((query.offset ?? 0) > 0 && !query.snapshot_at)) context.addIssue({ code: "custom", message: "翻页需要原快照与目录成员标识" })
})
export type ArchiveCatalogQuery = z.infer<typeof querySchema>

export async function getStockArchiveCatalog(input: ArchiveCatalogQuery, token: string, signal?: AbortSignal): Promise<StockArchiveCatalogPage> {
  const query = querySchema.parse(input)
  const limit = query.limit ?? 20, offset = query.offset ?? 0
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset), ...(query.trade_date ? { trade_date: query.trade_date } : {}), ...(query.snapshot_at ? { snapshot_at: query.snapshot_at } : {}), ...(query.membership_token ? { membership_token: query.membership_token } : {}) })
  const timeout = AbortSignal.timeout(12000)
  const response = await fetch(`${(process.env.NEXT_PUBLIC_API_URL ?? "").replace(/\/$/, "")}/api/stock/market/archive/versions?${params}`, { cache: "no-store", headers: { Authorization: `Bearer ${token}` }, signal: signal ? AbortSignal.any([signal, timeout]) : timeout })
  if (!response.ok) {
    const payload = await response.json().catch(() => null)
    const requestId = response.headers.get("X-Request-ID")
    throw new Error(`${typeof payload?.detail === "string" ? payload.detail : `归档目录读取失败(${response.status})`}${requestId ? ` · 请求 ${requestId}` : ""}`)
  }
  const page = stockArchiveCatalogSchema.parse(await response.json())
  if ((query.snapshot_at && Date.parse(query.snapshot_at) !== Date.parse(page.snapshot_at)) || (query.membership_token && query.membership_token !== page.membership_token) || page.items.length !== Math.min(limit, Math.max(0, page.total - offset)) || page.has_more !== (offset + page.items.length < page.total) || (query.trade_date && page.items.some((item) => item.trade_date !== query.trade_date))) throw new Error("归档目录分页、日期或快照成员与请求不一致")
  return page
}
