import { z } from "zod"

export const stockFundamentalQuerySchema = z.object({ code: z.string().regex(/^[0-9]{6}$/), kind: z.enum(["financial", "valuation"]), as_of: z.iso.date(), limit: z.number().int().min(1).max(100).default(20), offset: z.number().int().min(0).max(100000).default(0) }).strict()
const numberOrNull = z.number().finite().nullable()
const financial = z.object({ report_date: z.iso.date(), publish_date: z.iso.date(), roe: numberOrNull, gross_margin: numberOrNull, revenue_yoy: numberOrNull, netprofit_yoy: numberOrNull, eps: numberOrNull, bps: numberOrNull, netprofit: numberOrNull }).strict()
const valuation = z.object({ trade_date: z.iso.date(), pe_ttm: numberOrNull, pb: numberOrNull, total_mv: numberOrNull, circ_mv: numberOrNull, turnover_rate: numberOrNull }).strict()
const financialUnits = { roe: "percent", gross_margin: "percent", revenue_yoy: "percent", netprofit_yoy: "percent", eps: "CNY_per_share", bps: "CNY_per_share", netprofit: "CNY" }
const valuationUnits = { pe_ttm: "ratio", pb: "ratio", total_mv: "CNY_100_million", circ_mv: "CNY_100_million", turnover_rate: "percent" }
const count = z.number().int().nonnegative().max(50000)
export const stockFundamentalsSchema = z.object({
  history_version: z.literal("stock-fundamentals-history-v1"), market: z.literal("stock"), query: stockFundamentalQuerySchema,
  instrument: z.object({ code: z.string().regex(/^[0-9]{6}$/), name: z.string().min(1).max(32), exchange: z.enum(["SSE", "SZSE", "BSE"]), instrument_id: z.string().max(16) }).strict(),
  counts: z.object({ source_rows: count, eligible_rows: count, unknown_publication: count, invalid_dates: count, later_rows: count }).strict(),
  rows: z.array(z.union([financial, valuation])).max(100), has_more: z.boolean(), units: z.record(z.string().max(32), z.string().max(32)),
  source: z.literal("local_import"), provenance: z.literal("legacy_unverified"), data_version: z.null(), point_in_time_certified: z.literal(false), currency: z.literal("CNY"), timezone: z.literal("Asia/Shanghai"),
  supplier: z.literal("unknown"), license_scope: z.literal("unverified"), collected_at: z.null(), trading_calendar: z.literal("unverified"), frequency: z.enum(["quarterly", "daily"]), financial_basis: z.literal("unverified"), price_adjustment: z.literal("not_applicable"), unit_status: z.literal("schema_declared_not_row_verified"), date_semantics: z.record(z.string().max(32), z.string().max(64)),
  transaction: z.enum(["postgresql_repeatable_read_readonly", "sqlite_explicit_select_only"]), read_at: z.iso.datetime({ offset: true }), source_calls: z.literal(0), model_calls: z.literal(0), limitations: z.array(z.string().min(1).max(512)).min(1).max(8),
}).strict().superRefine((value, context) => {
  const fail = () => context.addIssue({ code: "custom", message: "历史计数、日期、单位、标识或分页不一致" })
  const { query, counts, instrument, rows } = value
  if (instrument.code !== query.code || instrument.instrument_id !== `${instrument.code}.${instrument.exchange}`) fail()
  if (counts.source_rows !== counts.eligible_rows + counts.unknown_publication + counts.invalid_dates + counts.later_rows) fail()
  const returned = Math.max(0, Math.min(query.limit, counts.eligible_rows - query.offset))
  if (rows.length !== returned || value.has_more !== (query.offset + rows.length < counts.eligible_rows)) fail()
  const expectedUnits = query.kind === "financial" ? financialUnits : valuationUnits
  if (Object.keys(value.units).length !== Object.keys(expectedUnits).length || Object.entries(expectedUnits).some(([key, unit]) => value.units[key] !== unit)) fail()
  const expectedDates = query.kind === "financial" ? { report_date: "report_period_end", publish_date: "disclosure_date_inclusive_display_only" } : { trade_date: "trading_date" }
  if (value.frequency !== (query.kind === "financial" ? "quarterly" : "daily") || Object.keys(value.date_semantics).length !== Object.keys(expectedDates).length || Object.entries(expectedDates).some(([key, meaning]) => value.date_semantics[key] !== meaning)) fail()
  if (query.kind === "valuation" && (counts.unknown_publication || counts.invalid_dates)) fail()
  let previous = "9999-12-31"
  for (const row of rows) {
    if (query.kind === "financial") {
      if (!("report_date" in row)) { fail(); continue }
      if (row.report_date > row.publish_date || row.publish_date > query.as_of || row.report_date >= previous) fail()
      previous = row.report_date
    } else {
      if (!("trade_date" in row)) { fail(); continue }
      if (row.trade_date > query.as_of || row.trade_date >= previous) fail()
      previous = row.trade_date
    }
  }
})

export type StockFundamentals = z.infer<typeof stockFundamentalsSchema>
export type StockFundamentalQuery = z.input<typeof stockFundamentalQuerySchema>

export async function getStockFundamentals(input: StockFundamentalQuery, token: string, signal?: AbortSignal): Promise<StockFundamentals> {
  const query = stockFundamentalQuerySchema.parse(input)
  if (!token || token.length > 8192) throw new Error("请先登录后读取股票历史")
  signal?.throwIfAborted()
  const params = new URLSearchParams({ code: query.code, kind: query.kind, as_of: query.as_of, limit: String(query.limit), offset: String(query.offset) })
  const timeout = AbortSignal.timeout(20000)
  const response = await fetch(`${(process.env.NEXT_PUBLIC_API_URL ?? "").replace(/\/$/, "")}/api/stock/market/fundamentals?${params}`, { method: "GET", cache: "no-store", headers: { Authorization: `Bearer ${token}` }, signal: signal ? AbortSignal.any([signal, timeout]) : timeout })
  signal?.throwIfAborted()
  if (!response.ok) {
    const requestId = response.headers.get("X-Request-ID")
    throw Object.assign(new Error(response.status === 404 ? "证券未收录或历史接口尚未部署，请核对股票后端；未返回假空表" : `股票历史读取失败（HTTP ${response.status}），未以零或空表代替错误`), { status: response.status, request_id: requestId && /^[a-f0-9]{32}$/.test(requestId) ? requestId : null })
  }
  const result = stockFundamentalsSchema.safeParse(await response.json())
  signal?.throwIfAborted()
  if (!result.success || JSON.stringify(result.data.query) !== JSON.stringify(query)) throw new Error("历史响应格式或请求回显不一致，请核查数据；未返回假空表")
  return result.data
}
