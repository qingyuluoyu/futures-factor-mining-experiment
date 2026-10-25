import { z } from "zod"

const hash = z.string().regex(/^[a-f0-9]{64}$/)
const instrument = z.string().regex(/^(?:6[0-9]{5}\.SH|[03][0-9]{5}\.SZ|[489][0-9]{5}\.BJ)$/)
const stamp = z.iso.datetime({ offset: true })
const count = z.number().int().min(0).max(10000)
const decimalPattern = /^(?:0|[1-9][0-9]{0,19})(?:\.[0-9]{1,18})?$/
function exactUnits(value: string): bigint | null {
  if (!decimalPattern.test(value)) return null
  const [whole, fraction = ""] = value.split(".")
  return BigInt(whole + fraction.padEnd(18, "0"))
}
const decimal = z.string().regex(decimalPattern)
const price = decimal.refine((value) => { const units = exactUnits(value); return units !== null && units > BigInt(0) }, "原价必须为正十进制字符串")
const identity = { instrument_id: instrument, trade_date: z.iso.date(), archive_version: hash }
const observation = z.object({ ...identity, open: price, close: price, high: price, low: price, pre_close: price, volume: decimal, amount: decimal }).strict().superRefine((point, context) => {
  const low = exactUnits(point.low), high = exactUnits(point.high), open = exactUnits(point.open), close = exactUnits(point.close)
  if (low === null || high === null || open === null || close === null || low > open || low > close || high < open || high < close) context.addIssue({ code: "custom", message: "归档原价OHLC关系无效" })
})
const metadata = z.object({ market: z.literal("CN_A"), currency: z.literal("CNY"), timezone: z.literal("Asia/Shanghai"), frequency: z.literal("daily"), price_adjustment: z.literal("none"), volume_unit: z.literal("lot_100_shares"), amount_unit: z.literal("CNY"), provider: z.literal("user_authorized_teajoin_tushare_compatible"), source_uri: z.literal("https://teajoin.com/debug/api/daily"), reference_price: z.literal("vendor_ex_rights_pre_close"), license_scope: z.literal("operator_configured_account_not_redistribution_certified") }).strict()
export const stockArchiveObservationSchema = z.object({ manifest_version: z.literal("stock-raw-panel-v1"), versions: z.array(hash).length(1), instrument_ids: z.array(instrument).length(1), financial_metadata: metadata,
  batches: z.array(z.object({ version: hash, source_data_version: hash, source_batch_id: z.uuid(), archive_batch_id: z.uuid(), trade_date: z.iso.date(), method_version: z.literal("stock-daily-archive-2026-10-19.v1"), source_collected_at: stamp, archived_at: stamp, verified_row_count: count.min(1), expected_count: count.min(1), missing_count: count, unknown_count: count }).strict()).length(1),
  observations: z.array(observation).max(1), missing: z.array(z.object({ ...identity, reason: z.enum(["source_missing", "source_unknown_quarantined", "outside_snapshot"]) }).strict()).max(1),
  verification: z.literal("stored_source_and_archive_content_equal_not_independently_certified"), number_encoding: z.literal("decimal_of_stored_json_number_not_original_response_text"), data_available_at: z.null(), point_in_time_verified: z.literal(false), eligible_for_backtest: z.literal(false), usage: z.literal("internal_raw_observation_audit_not_backtest_dataset"), calendar_continuity_verified: z.literal(false), source_calls: z.literal(0), model_calls: z.literal(0), content_sha256: hash, generated_at: stamp,
}).strict().superRefine((result, context) => {
  const batch = result.batches[0]
  if (!batch) return
  const generated = Date.parse(result.generated_at), collected = Date.parse(batch.source_collected_at), archived = Date.parse(batch.archived_at)
  if (![generated, collected, archived].every(Number.isFinite)) { context.addIssue({ code: "custom", message: "归档核对时间无效" }); return }
  const local = new Date(collected + 8 * 3600000).toISOString()
  const points = [...result.observations, ...result.missing]
  if (batch.version !== result.versions[0] || batch.verified_row_count + batch.missing_count !== batch.expected_count || batch.verified_row_count + batch.unknown_count > 10000 || collected > archived || archived > generated || batch.trade_date > local.slice(0, 10) || (batch.trade_date === local.slice(0, 10) && local.slice(11, 16) < "16:30") || points.length !== 1 || points.some((point) => point.archive_version !== result.versions[0] || point.instrument_id !== result.instrument_ids[0] || point.trade_date !== batch.trade_date)) context.addIssue({ code: "custom", message: "归档核对版本、身份、分区或时点不一致" })
})
export type StockArchiveObservationResult = z.infer<typeof stockArchiveObservationSchema>

export async function getStockArchiveObservation(version: string, instrumentId: string, token: string, signal?: AbortSignal): Promise<StockArchiveObservationResult> {
  hash.parse(version); instrument.parse(instrumentId)
  const timeout = AbortSignal.timeout(12000)
  const response = await fetch(`${(process.env.NEXT_PUBLIC_API_URL ?? "").replace(/\/$/, "")}/api/stock/market/archive/versions/${version}/observation?${new URLSearchParams({ instrument_id: instrumentId })}`, { cache: "no-store", headers: { Authorization: `Bearer ${token}` }, signal: signal ? AbortSignal.any([signal, timeout]) : timeout })
  if (!response.ok) {
    const payload = await response.json().catch(() => null), requestId = response.headers.get("X-Request-ID")
    throw new Error(`${typeof payload?.detail === "string" ? payload.detail : `固定版本核对失败(${response.status})`}${requestId ? ` · 请求 ${requestId}` : ""}`)
  }
  const result = stockArchiveObservationSchema.parse(await response.json())
  if (result.versions[0] !== version || result.instrument_ids[0] !== instrumentId) throw new Error("归档核对返回的固定版本或证券与请求不一致")
  return result
}
