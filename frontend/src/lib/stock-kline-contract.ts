import { z } from "zod"
import { getStockKlineApi } from "./stock-api.ts"
import { request } from "./api.ts"

export type StockKlineSource = "history_qfq" | "eod_raw"
const sourceEntry = z.object({
  id: z.enum(["history_qfq", "eod_raw"]), source: z.enum(["local_import", "teajoin_eod"]), price_adjustment: z.enum(["qfq", "none"]),
  available: z.boolean(), reason: z.enum(["available", "no_local_history", "eod_disabled", "instrument_not_found"]),
  available_bars: z.number().int().nonnegative().nullable(), first_date: z.iso.date().nullable(), last_date: z.iso.date().nullable(),
  window_cap: z.union([z.literal(250), z.literal(3000)]), history_completeness: z.literal("unverified"),
}).strict().superRefine((value, context) => {
  const raw = value.id === "eod_raw"
  const invalid = value.source !== (raw ? "teajoin_eod" : "local_import") || value.price_adjustment !== (raw ? "none" : "qfq")
    || value.window_cap !== (raw ? 250 : 3000) || value.available !== (value.reason === "available")
    || (value.reason === "eod_disabled" && !raw)
    || (value.available ? value.available_bars === null || value.available_bars <= 0 || value.first_date === null || value.last_date === null || value.first_date > value.last_date
      : value.first_date !== null || value.last_date !== null || (value.reason === "eod_disabled" ? value.available_bars !== null : value.available_bars !== 0))
  if (invalid) context.addIssue({ code: "custom", message: "日线来源目录口径冲突" })
})
const sourcesSchema = z.object({ catalog_version: z.literal("stock-kline-sources-v1"), code: z.string().regex(/^[0-9]{6}$/), default_mode: z.literal("legacy_auto"), sources: z.array(sourceEntry).length(2) }).strict()
  .refine((value) => new Set(value.sources.map((item) => item.id)).size === 2)
export type StockKlineSources = z.infer<typeof sourcesSchema>

export function parseStockKlineSources(value: unknown, code: string): StockKlineSources {
  const parsed = sourcesSchema.safeParse(value)
  if (!parsed.success || parsed.data.code !== code) throw new Error("日线来源目录响应异常，停止选源")
  return parsed.data
}
export function latestStockKlineSource(value: StockKlineSources): StockKlineSource | null {
  const sorted = value.sources.filter((item) => item.available).slice().sort((left, right) => (right.last_date ?? "").localeCompare(left.last_date ?? "") || (left.id === "eod_raw" ? -1 : 1))
  return sorted[0]?.id ?? null
}
export async function getStockKlineSources(code: string, signal?: AbortSignal): Promise<StockKlineSources> {
  if (!/^[0-9]{6}$/.test(code)) throw new Error("股票代码必须为6位ASCII数字")
  signal?.throwIfAborted()
  const timeout = AbortSignal.timeout(20000)
  const value = await request(`/api/stock/kline/sources?code=${code}`, { signal: signal ? AbortSignal.any([signal, timeout]) : timeout, cache: "no-store" })
  signal?.throwIfAborted()
  return parseStockKlineSources(value, code)
}

const positive = z.number().finite().positive()
const bar = z.object({
  time: z.iso.date(), open: positive, high: positive, low: positive, close: positive,
  volume: z.number().finite().nonnegative(), pre_close: positive.nullable().optional(),
}).refine((value) => value.low <= Math.min(value.open, value.close) && value.high >= Math.max(value.open, value.close))
const coverage = z.object({
  scope: z.literal("selected_source_local_store"), available_bars: z.number().int().nonnegative(),
  first_date: z.iso.date().nullable(), last_date: z.iso.date().nullable(),
  requested_limit: z.number().int().min(1).max(3000), effective_limit: z.number().int().min(1).max(3000),
  window_status: z.enum(["window_limited", "local_history_exhausted", "no_local_history"]),
  history_completeness: z.literal("unverified"),
})
const response = z.object({
  code: z.string().regex(/^[0-9]{6}$/), name: z.string().max(128), industry: z.string().max(128),
  bars: z.array(bar).max(3000), has_more: z.boolean(),
  price_adjustment: z.enum(["none", "qfq"]).optional(), source: z.enum(["teajoin_eod", "local_import"]).optional(),
  volume_unit: z.literal("lot_100_shares").optional(),
  reference_price: z.literal("vendor_ex_rights_pre_close").optional(),
  change_basis: z.enum(["vendor_ex_rights_pre_close", "adjacent_loaded_adjusted_close"]).optional(),
  coverage: coverage.optional(),
  selected_source: z.enum(["history_qfq", "eod_raw"]).optional(),
}).superRefine((value, context) => {
  const raw = value.price_adjustment === "none"
  const invalid = (raw && (value.source !== "teajoin_eod" || value.volume_unit !== "lot_100_shares")) || (!raw && value.source === "teajoin_eod")
    || (!raw && (value.reference_price !== undefined || value.bars.some((item) => item.pre_close !== undefined)))
    || (value.change_basis !== undefined && value.change_basis !== (raw ? "vendor_ex_rights_pre_close" : "adjacent_loaded_adjusted_close"))
    || value.bars.some((item, index) => index > 0 && item.time <= value.bars[index - 1].time)
  if (invalid) context.addIssue({ code: "custom", message: "日线来源、复权、基准或日期顺序冲突" })
  if (value.selected_source && (value.source !== (value.selected_source === "eod_raw" ? "teajoin_eod" : "local_import") || value.price_adjustment !== (value.selected_source === "eod_raw" ? "none" : "qfq"))) context.addIssue({ code: "custom", message: "所选来源与返回口径冲突" })
  const stored = value.coverage
  if (stored) {
    const count = value.bars.length
    const expectedStatus = stored.available_bars === 0 ? "no_local_history" : value.has_more ? "window_limited" : "local_history_exhausted"
    const invalidCoverage = stored.available_bars < count || value.has_more !== (stored.available_bars > count)
      || stored.window_status !== expectedStatus || count > stored.effective_limit
      || stored.effective_limit !== (raw ? Math.min(stored.requested_limit, 250) : stored.requested_limit)
      || (stored.available_bars === 0 ? stored.first_date !== null || stored.last_date !== null
        : stored.first_date === null || stored.last_date === null || stored.first_date > stored.last_date
          || count === 0 || stored.first_date > value.bars[0].time || stored.last_date !== value.bars[count - 1].time
          || (!value.has_more && stored.first_date !== value.bars[0].time))
    if (invalidCoverage) context.addIssue({ code: "custom", message: "日线本地覆盖范围或数量冲突" })
  }
}).transform((value) => ({ ...value, change_basis: value.price_adjustment === "none" ? "vendor_ex_rights_pre_close" as const : "adjacent_loaded_adjusted_close" as const }))

export type ValidatedStockKlineResponse = z.infer<typeof response>

export function parseStockKlineResponse(value: unknown, code: string): ValidatedStockKlineResponse {
  const parsed = response.safeParse(value)
  if (!parsed.success || parsed.data.code !== code) throw new Error("日线响应或来源/复权/涨跌参考口径异常，停止显示报价和指标；未用空行情代替失败")
  return parsed.data
}

export async function getValidatedStockKlineApi(code: string, limit = 250, signal?: AbortSignal, source?: StockKlineSource): Promise<ValidatedStockKlineResponse> {
  if (!/^[0-9]{6}$/.test(code)) throw new Error("股票代码必须为6位ASCII数字")
  if (!Number.isInteger(limit) || limit < 1 || limit > 3000) throw new Error("日线数量必须为1至3000")
  signal?.throwIfAborted()
  const result = await getStockKlineApi(code, limit, signal, source)
  signal?.throwIfAborted()
  const parsed = parseStockKlineResponse(result, code)
  if (source && parsed.selected_source !== source) throw new Error("服务端未确认所选来源，停止展示；未静默改读其他来源")
  if (parsed.bars.length > limit) throw new Error("日线响应超过请求数量，停止展示")
  if (parsed.coverage && parsed.coverage.requested_limit !== limit) throw new Error("日线响应覆盖窗口与请求不符，停止展示")
  return parsed
}
