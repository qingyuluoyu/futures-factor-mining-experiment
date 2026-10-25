import { z } from "zod"

const code = z.string().regex(/^(?:00|60)[0-9]{4}$/)
const revision = z.number().int().min(0).max(2147483647)
export const replayHistoryQuerySchema = z.object({ expected_revision: revision, status: z.enum(["all", "pending", "filled", "rejected", "cancelled"]).default("all"), direction: z.enum(["all", "buy", "sell"]).default("all"), code: code.nullable().default(null), start: z.iso.date().nullable().default(null), end: z.iso.date().nullable().default(null), limit: z.number().int().min(1).max(100).default(20), offset: z.number().int().min(0).max(100000).default(0) }).strict().refine((value) => !value.start || !value.end || value.start <= value.end)
const cents = z.string().regex(/^-?(?:0|[1-9][0-9]{0,15})\.[0-9]{2}$/)
const price = z.string().regex(/^(?:0|[1-9][0-9]{0,13})\.[0-9]{4}$/).refine((value) => /[1-9]/.test(value))
const order = z.object({ id: z.uuid(), code, instrument_id: z.string().max(16), direction: z.enum(["buy", "sell"]), quantity: z.number().int().positive().max(1000000).multipleOf(100), order_type: z.enum(["market", "limit"]), limit_price: price.nullable(), status: z.enum(["pending", "filled", "rejected", "cancelled"]), reason: z.string().max(160).nullable(), submitted_on: z.iso.date(), filled_on: z.iso.date().nullable(), filled_price: price.nullable(), fees: cents.refine((value) => !value.startsWith("-") || !/[1-9]/.test(value)), cash_delta: cents.nullable(), realized_pnl: cents, created_at: z.iso.datetime({ offset: true }).regex(/\.[0-9]{6}\+00:00$/) }).strict()

export const replayOrderHistorySchema = z.object({
  history_version: z.literal("stock-replay-order-history-v1"), market: z.literal("stock"), account_id: z.uuid(), trading_mode: z.enum(["live", "virtual"]), revision, current_date: z.iso.date(), method_version: z.literal("stock-replay-v1"), query: replayHistoryQuerySchema,
  total: z.number().int().nonnegative().max(Number.MAX_SAFE_INTEGER), items: z.array(order).max(100), has_more: z.boolean(), dataset_bindings: z.array(z.object({ code, dataset_id: z.uuid() }).strict()).min(1).max(20),
  source: z.literal("internal_day_replay"), supplier: z.literal("internal_simulator"), currency: z.literal("CNY"), quantity_unit: z.literal("share"), price_unit: z.literal("CNY_per_share"), amount_unit: z.literal("CNY"), timezone: z.literal("Asia/Shanghai"), frequency: z.literal("order_event"), price_adjustment: z.literal("none"), license_scope: z.literal("account_owner_private"), trading_calendar: z.literal("account_bound_snapshot_not_revalidated_here"), financial_basis: z.literal("not_applicable"), collected_at: z.null(), data_version: z.null(), point_in_time_certified: z.literal(false), read_at: z.iso.datetime({ offset: true }), source_calls: z.literal(0), model_calls: z.literal(0), transaction: z.enum(["postgresql_repeatable_read_readonly", "sqlite_explicit_select_only"]), limitations: z.array(z.string().min(1).max(512)).min(1).max(8),
}).strict().superRefine((value, context) => {
  const fail = () => context.addIssue({ code: "custom", message: "委托历史的状态、日期、分页或账户锚点不一致" })
  const query = value.query
  const boundCodes = new Set(value.dataset_bindings.map((item) => item.code))
  if (boundCodes.size !== value.dataset_bindings.length || new Set(value.dataset_bindings.map((item) => item.dataset_id)).size !== value.dataset_bindings.length) fail()
  if (query.expected_revision !== value.revision || (query.code && !boundCodes.has(query.code)) || (query.start && query.start > value.current_date) || (query.end && query.end > value.current_date)) fail()
  if (value.items.length !== Math.max(0, Math.min(query.limit, value.total - query.offset)) || value.has_more !== (query.offset + value.items.length < value.total) || new Set(value.items.map((item) => item.id)).size !== value.items.length) fail()
  let previous: typeof value.items[number] | null = null
  for (const item of value.items) {
    if (!boundCodes.has(item.code) || item.instrument_id !== `${item.code}.${item.code.startsWith("60") ? "SH" : "SZ"}` || item.submitted_on > value.current_date || (query.code && item.code !== query.code) || (query.status !== "all" && item.status !== query.status) || (query.direction !== "all" && item.direction !== query.direction) || (query.start && item.submitted_on < query.start) || (query.end && item.submitted_on > query.end)) fail()
    if ((item.order_type === "limit") !== (item.limit_price !== null)) fail()
    if (item.status === "filled") {
      if (!item.filled_on || item.filled_on <= item.submitted_on || item.filled_on > value.current_date || item.filled_price === null || item.cash_delta === null) fail()
    } else if (item.filled_on !== null || item.filled_price !== null || item.cash_delta !== null || /[1-9]/.test(item.fees) || /[1-9]/.test(item.realized_pnl)) fail()
    if (previous && (item.created_at > previous.created_at || (item.created_at === previous.created_at && item.id >= previous.id))) fail()
    previous = item
  }
})

export type ReplayOrderHistory = z.infer<typeof replayOrderHistorySchema>
export type ReplayHistoryQuery = z.input<typeof replayHistoryQuerySchema>

export async function getStockReplayOrderHistory(accountId: string, input: ReplayHistoryQuery, token: string, mode: "live" | "virtual", signal?: AbortSignal): Promise<ReplayOrderHistory> {
  const account = z.uuid().parse(accountId)
  const query = replayHistoryQuerySchema.parse(input)
  if (!token || token.length > 8192) throw new Error("请先登录后读取模拟委托历史")
  signal?.throwIfAborted()
  const params = new URLSearchParams()
  for (const [name, value] of Object.entries(query)) if (value !== null) params.set(name, String(value))
  const timeout = AbortSignal.timeout(20000)
  const response = await fetch(`${(process.env.NEXT_PUBLIC_API_URL ?? "").replace(/\/$/, "")}/api/stock/replay/accounts/${account}/order-history?${params}`, { cache: "no-store", method: "GET", headers: { Authorization: `Bearer ${token}` }, signal: signal ? AbortSignal.any([signal, timeout]) : timeout })
  signal?.throwIfAborted()
  if (!response.ok) {
    const refresh = response.status === 409 && response.headers.get("X-Replay-History-Error") === "history_revision_conflict"
    const requestId = response.headers.get("X-Request-ID")
    throw Object.assign(new Error(refresh ? "账户已变化，请先使用账户刷新按钮刷新账户，再重新读取历史" : `模拟委托历史读取失败（HTTP ${response.status}）；未用近期200笔或空页代替`), { status: response.status, requires_refresh: refresh, request_id: requestId && /^[a-f0-9]{32}$/.test(requestId) ? requestId : null })
  }
  const result = replayOrderHistorySchema.safeParse(await response.json())
  signal?.throwIfAborted()
  if (!result.success || result.data.account_id !== account || result.data.trading_mode !== mode || JSON.stringify(result.data.query) !== JSON.stringify(query)) throw new Error("委托历史响应格式或账户/登录盘/查询回显不一致；未返回假空页")
  return result.data
}
