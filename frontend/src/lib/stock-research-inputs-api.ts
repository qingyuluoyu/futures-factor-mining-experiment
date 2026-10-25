import { z } from "zod"

const ids = ["pe_ttm", "pb", "revenue_yoy", "netprofit_yoy", "roe", "gross_margin", "ret_20d", "vol_20d", "turnover_20d"] as const
const fieldIds = ["pe_ttm", "pb", "turnover_rate", "total_mv", "roe", "gross_margin", "revenue_yoy", "netprofit_yoy"] as const
const units = { pe_ttm: "ratio", pb: "ratio", turnover_rate: "percent", total_mv: "100_million_CNY", roe: "percent", gross_margin: "percent", revenue_yoy: "percent", netprofit_yoy: "percent" }
const count = z.number().int().nonnegative().max(1000000)
const querySchema = z.object({ start_date: z.iso.date(), end_date: z.iso.date(), factor_ids: z.array(z.enum(ids)).min(1).max(9).refine((items) => new Set(items).size === items.length).transform((items) => [...items].sort()), mktcap_neutral: z.boolean().default(false), weight: z.enum(["equal", "mktcap", "factor"]).default("equal") }).strict().refine((value) => value.start_date <= value.end_date && Date.parse(value.end_date) - Date.parse(value.start_date) <= 3660 * 86400000)
const reasons = z.enum(["no_valid_stock_prices", "insufficient_observed_dates", "no_usable_field_values"])
const field = z.object({ field: z.enum(fieldIds), source: z.enum(["valuation", "financial"]), unit: z.string(), source_rows: count, finite_values: count, usable_values: count, missing_values: count, nonfinite_values: count, excluded_nonpositive: count, rule: z.enum(["finite", "finite_strictly_positive"]) }).strict()
export const researchInputsSchema = z.object({
  inputs_version: z.literal("stock-research-inputs-v1"), market: z.literal("stock"), query: querySchema,
  status: z.enum(["invalid_inputs", "missing_inputs", "potential_inputs"]),
  prices: z.object({ rows: count, valid_close_rows: count, invalid_rows: count, securities: count.max(10000), observed_dates: count.max(3661), duplicate_groups: count }).strict(),
  fields: z.array(field).max(8), factors: z.array(z.object({ factor_id: z.enum(ids), label: z.string().min(1).max(64), required_fields: z.array(z.enum(fieldIds)).max(1), minimum_observed_dates: z.union([z.literal(1), z.literal(20), z.literal(21)]), status: z.enum(["missing_inputs", "potential_inputs"]), reasons: z.array(reasons).max(3) }).strict()).min(1).max(9), auxiliary_missing: z.array(z.literal("total_mv")).max(1),
  adjustment: z.literal("forward_adjusted_unversioned"), timezone: z.literal("Asia/Shanghai"), frequency: z.literal("daily"), point_in_time_certified: z.literal(false), source_calls: z.literal(0), model_calls: z.literal(0), read_at: z.iso.datetime({ offset: true }), transaction: z.enum(["postgresql_repeatable_read_readonly", "sqlite_explicit_select_only"]), limitations: z.array(z.string().min(1).max(512)).min(1).max(8),
}).strict().superRefine((value, context) => {
  const fail = () => context.addIssue({ code: "custom", message: "输入检查计数、依赖或状态不一致" })
  const prices = value.prices
  if (prices.valid_close_rows + prices.invalid_rows !== prices.rows || prices.securities > prices.rows || prices.observed_dates > prices.rows || prices.duplicate_groups > prices.rows / 2) fail()
  const fields = new Map(value.fields.map((item) => [item.field, item]))
  if (fields.size !== value.fields.length || value.factors.length !== value.query.factor_ids.length || value.factors.some((item, index) => item.factor_id !== value.query.factor_ids[index])) fail()
  const expectedFields = new Set<string>()
  if (value.query.mktcap_neutral || value.query.weight === "mktcap") expectedFields.add("total_mv")
  for (const item of value.fields) {
    const positive = ["pe_ttm", "pb", "total_mv"].includes(item.field)
    if (item.unit !== units[item.field] || item.source !== (["roe", "gross_margin", "revenue_yoy", "netprofit_yoy"].includes(item.field) ? "financial" : "valuation") || item.rule !== (positive ? "finite_strictly_positive" : "finite") || item.finite_values + item.missing_values + item.nonfinite_values !== item.source_rows || item.usable_values + item.excluded_nonpositive !== item.finite_values || (!positive && item.excluded_nonpositive !== 0)) fail()
  }
  for (const item of value.factors) {
    const required = item.factor_id === "ret_20d" || item.factor_id === "vol_20d" ? [] : [item.factor_id === "turnover_20d" ? "turnover_rate" : item.factor_id]
    required.forEach((name) => expectedFields.add(name))
    const minimum = ["ret_20d", "vol_20d"].includes(item.factor_id) ? 21 : item.factor_id === "turnover_20d" ? 20 : 1
    const expectedReasons = []
    if (prices.valid_close_rows === 0) expectedReasons.push("no_valid_stock_prices")
    if (prices.observed_dates < minimum) expectedReasons.push("insufficient_observed_dates")
    if (item.required_fields.some((name) => (fields.get(name)?.usable_values ?? 0) === 0)) expectedReasons.push("no_usable_field_values")
    if (JSON.stringify(item.required_fields) !== JSON.stringify(required) || item.minimum_observed_dates !== minimum || JSON.stringify(item.reasons) !== JSON.stringify(expectedReasons) || item.status !== (item.reasons.length ? "missing_inputs" : "potential_inputs")) fail()
  }
  if (fields.size !== expectedFields.size || [...expectedFields].some((name) => !fields.has(name as typeof fieldIds[number]))) fail()
  const auxiliary = expectedFields.has("total_mv") && (fields.get("total_mv")?.usable_values ?? 0) === 0 ? ["total_mv"] : []
  const status = prices.invalid_rows || prices.duplicate_groups ? "invalid_inputs" : auxiliary.length || value.factors.some((item) => item.status === "missing_inputs") ? "missing_inputs" : "potential_inputs"
  if (JSON.stringify(auxiliary) !== JSON.stringify(value.auxiliary_missing) || status !== value.status) fail()
})

export type StockResearchInputs = z.infer<typeof researchInputsSchema>
export type StockResearchInputQuery = Omit<z.input<typeof querySchema>, "factor_ids"> & { factor_ids: string[] }

export async function getStockResearchInputs(input: StockResearchInputQuery, token: string, signal?: AbortSignal): Promise<StockResearchInputs> {
  const query = querySchema.parse(input)
  if (!token || token.length > 8192) throw new Error("请先登录后检查研究输入")
  signal?.throwIfAborted()
  const parameters = new URLSearchParams({ start_date: query.start_date, end_date: query.end_date, mktcap_neutral: String(query.mktcap_neutral), weight: query.weight })
  query.factor_ids.forEach((factor) => parameters.append("factor_ids", factor))
  const timeout = AbortSignal.timeout(20000)
  const response = await fetch(`${(process.env.NEXT_PUBLIC_API_URL ?? "").replace(/\/$/, "")}/api/stock/research/inputs?${parameters}`, { method: "GET", cache: "no-store", headers: { Authorization: `Bearer ${token}` }, signal: signal ? AbortSignal.any([signal, timeout]) : timeout })
  signal?.throwIfAborted()
  if (!response.ok) {
    const requestId = response.headers.get("X-Request-ID")
    throw Object.assign(new Error(response.status === 404 ? "股票输入检查尚未部署，请更新股票接口；未回退空数据" : `研究输入检查失败（HTTP ${response.status}），未用零数据代替错误`), { status: response.status, request_id: requestId && /^[a-f0-9]{32}$/.test(requestId) ? requestId : null })
  }
  const result = researchInputsSchema.safeParse(await response.json())
  if (!result.success || JSON.stringify(result.data.query) !== JSON.stringify(query)) throw new Error("输入检查响应或查询回显不一致，请核查；未以零值代替错误")
  return result.data
}
