import { z } from "zod"
import { request } from "./api.ts"

const nullableNumber = z.number().finite().nullable()
const count = z.number().int().nonnegative()
const date = z.iso.date()
const code = z.string().regex(/^[0-9]{6}$/)
const exchange = z.enum(["SSE", "SZSE", "BSE"])
const identity = { code, instrument_id: z.string().regex(/^[0-9]{6}\.(SH|SZ|BJ)$/), exchange }
function sameInstrument(value: { code: string; instrument_id: string; exchange: "SSE" | "SZSE" | "BSE" }): boolean {
  return value.instrument_id === `${value.code}.${{ SSE: "SH", SZSE: "SZ", BSE: "BJ" }[value.exchange]}`
}
function coherentMetadata(value: { as_of: string | null; previous_date: string | null; coverage: { instruments: number; quoted: number; comparable: number } }): boolean {
  return value.coverage.comparable <= value.coverage.quoted && value.coverage.quoted <= value.coverage.instruments
    && (!value.previous_date || (!!value.as_of && value.previous_date < value.as_of))
    && (value.as_of !== null || value.coverage.quoted === 0)
}
const metadata = z.object({
  as_of: date.nullable(), previous_date: date.nullable(),
  classification: z.string(), source: z.string(), price_adjustment: z.string(),
  volume_unit: z.string(), method_version: z.string(),
  coverage: z.object({ instruments: count, quoted: count, comparable: count }),
  source_row_count: count.optional(),
  missing_codes: z.array(z.string().regex(/^\d{6}$/)).optional(),
  unknown_codes: z.array(z.string().regex(/^\d{6}$/)).optional(),
  data_version: z.string().min(1).optional(),
  collected_at: z.iso.datetime({ offset: true }).optional(),
  reference_price: z.string().optional(),
  license_scope: z.string().optional(),
})
const member = z.object({
  ...identity, name: z.string(), industry: z.string(), close: z.number().finite().positive().nullable(), change: nullableNumber,
  change_pct: nullableNumber, volume: z.number().finite().nonnegative().nullable(), trade_date: date.nullable(),
  last_trade_date: date.nullable().optional(),
  data_available: z.boolean(), data_status: z.enum(["ready", "missing_previous", "invalid_quote", "stale_quote", "no_daily"]),
}).refine((value) => sameInstrument(value) && (value.data_available
  ? value.close !== null && value.volume !== null && value.trade_date !== null
    && (value.data_status === "ready" ? value.change !== null && value.change_pct !== null : value.data_status === "missing_previous" && value.change === null && value.change_pct === null)
  : value.close === null && value.volume === null && value.trade_date === null && value.change === null && value.change_pct === null
    && value.data_status !== "ready" && value.data_status !== "missing_previous"), "证券标识或行情状态冲突")
const board = z.object({
  industry: z.string(), count, quoted_count: count, comparable_count: count,
  advancing: count, declining: count, flat: count, change_pct: nullableNumber,
  leader: z.object({ code, name: z.string(), change_pct: z.number().finite() }).nullable(),
}).refine((value) => value.comparable_count <= value.quoted_count && value.quoted_count <= value.count
  && value.advancing + value.declining + value.flat === value.comparable_count
  && (value.comparable_count === 0 ? value.change_pct === null && value.leader === null : value.change_pct !== null && value.leader !== null), "行业样本统计冲突")
const boards = metadata.extend({ boards: z.array(board).max(200), total: count, has_more: z.boolean() })
  .refine((value) => coherentMetadata(value) && value.boards.length <= value.total
    && new Set(value.boards.map((item) => item.industry)).size === value.boards.length
    && value.boards.reduce((sum, item) => sum + item.count, 0) <= value.coverage.instruments
    && value.boards.reduce((sum, item) => sum + item.quoted_count, 0) <= value.coverage.quoted
    && value.boards.reduce((sum, item) => sum + item.comparable_count, 0) <= value.coverage.comparable, "板块元数据或分页冲突")
export const stockBoardsSchema = boards
const members = metadata.extend({ items: z.array(member).max(100), total: count, has_more: z.boolean() })
  .refine((value) => coherentMetadata(value) && value.items.length <= value.total
    && new Set(value.items.map((item) => item.instrument_id)).size === value.items.length
    && value.items.every((item) => (!item.trade_date || item.trade_date === value.as_of) && (!item.last_trade_date || !value.as_of || item.last_trade_date <= value.as_of)), "成分元数据、证券或日期冲突")
export const stockMembersSchema = members
const details = z.object({
  instrument: z.object({ ...identity, name: z.string(), industry: z.string(), list_date: date.nullable() }).refine(sameInstrument),
  as_of: date, coverage: z.object({ first_date: date.nullable(), last_date: date.nullable(), daily_rows: count }),
  valuation: z.object({ trade_date: date, pe_ttm: nullableNumber, pb: nullableNumber, total_mv: z.number().finite().nonnegative().nullable(), circ_mv: z.number().finite().nonnegative().nullable(), turnover_rate: z.number().finite().nonnegative().nullable() }).nullable(),
  financials: z.array(z.object({ report_date: date, publish_date: date, roe: nullableNumber, gross_margin: nullableNumber, revenue_yoy: nullableNumber, netprofit_yoy: nullableNumber, eps: nullableNumber, bps: nullableNumber })).max(8),
  limitations: z.array(z.string()),
}).refine((value) => (value.coverage.daily_rows === 0
  ? value.coverage.first_date === null && value.coverage.last_date === null
  : value.coverage.first_date !== null && value.coverage.last_date !== null && value.coverage.first_date <= value.coverage.last_date && value.coverage.last_date <= value.as_of)
  && (!value.valuation || value.valuation.trade_date <= value.as_of)
  && value.financials.every((row) => row.report_date <= row.publish_date && row.publish_date <= value.as_of)
  && new Set(value.financials.map((row) => `${row.report_date}:${row.publish_date}`)).size === value.financials.length, "覆盖或披露时点冲突")
export const stockDetailsSchema = details
export type StockBoard = z.infer<typeof board>
export type StockMarketMetadata = z.infer<typeof metadata>
export type StockMember = z.infer<typeof member>
export type StockBoardsResponse = z.infer<typeof boards>
export type StockMembersResponse = z.infer<typeof members>
export type StockDetails = z.infer<typeof details>
export interface MemberFilters {
  industry?: string
  keyword?: string
  sort?: "code" | "name" | "change_pct"
  direction?: "asc" | "desc"
  dataOnly?: boolean
  offset?: number
}

async function validatedRequest<Schema extends z.ZodType>(path: string, schema: Schema, signal?: AbortSignal): Promise<z.infer<Schema>> {
  const timeout = AbortSignal.timeout(20000)
  const result = schema.safeParse(await request<unknown>(path, { signal: signal ? AbortSignal.any([signal, timeout]) : timeout }))
  if (!result.success) throw new Error("行情响应格式异常，请重试或联系管理员；未使用缺失值代替行情")
  return result.data
}

export function getStockBoardsApi(keyword = "", signal?: AbortSignal): Promise<StockBoardsResponse> {
  return validatedRequest(`/api/stock/market/boards?${new URLSearchParams({ keyword, limit: "200", offset: "0" })}`, boards, signal)
}

export async function getStockMembersApi(filters: MemberFilters = {}, signal?: AbortSignal): Promise<StockMembersResponse> {
  const offset = filters.offset ?? 0
  if (!Number.isInteger(offset) || offset < 0 || offset > 10000) throw new Error("股票分页范围无效，请使用0至10000的整数")
  const params = new URLSearchParams({ industry: filters.industry ?? "", keyword: filters.keyword ?? "", sort: filters.sort ?? "code", direction: filters.direction ?? "asc", data_only: String(filters.dataOnly ?? false), limit: "50", offset: String(filters.offset ?? 0) })
  const result = await validatedRequest(`/api/stock/market/members?${params}`, members, signal)
  if (result.items.length > 50) throw new Error("行情响应超过本次分页数量，停止展示")
  return result
}

export async function getStockDetailsApi(code: string, signal?: AbortSignal): Promise<StockDetails> {
  if (!/^\d{6}$/.test(code)) throw new Error("股票代码必须为六位数字")
  const result = await validatedRequest(`/api/stock/market/details?code=${code}`, details, signal)
  if (result.instrument.code !== code) throw new Error("证券详情与本次查询不一致，停止展示错误证券的财务")
  return result
}
