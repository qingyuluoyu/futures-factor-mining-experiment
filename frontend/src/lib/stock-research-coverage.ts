import { z } from "zod"

const date = z.iso.date()
const count = z.number().int().nonnegative()
const range = z.object({ start_date: date, end_date: date }).strict().refine((value) => value.start_date <= value.end_date, "覆盖区间无效")

const legacyCoverageSchema = z.object({
  coverage_version: z.literal("stock-research-coverage-v1"),
  market: z.literal("stock"),
  frequency: z.literal("daily"),
  adjustment: z.literal("forward_adjusted_unversioned"),
  timezone: z.literal("Asia/Shanghai"),
  catalog_count: count,
  available: z.object({ first_trade_date: date.nullable(), last_trade_date: date.nullable(), securities: count, price_rows: count, observed_dates: count }).strict(),
  recommended: range.nullable(),
  requested: range.nullable(),
  selected: z.object({ securities: count, observed_dates: count, price_rows: count, missing_price_cells: count, valuation_rows: count, financial_rows: count, panel_cells: count }).strict(),
  limits: z.object({ max_panel_cells: count, max_price_rows: count, max_valuation_rows: count, max_financial_rows: count, max_instruments: count }).strict(),
  status: z.enum(["within_limits", "no_data", "over_limit"]),
  warnings: z.array(z.string()),
  as_of: z.iso.datetime({ offset: true }),
}).strict()

const dependenciesSchema = z.object({
  version: z.literal("stock-research-dependencies-v1"), selection: z.literal("factor_dependencies"), price_fields: z.tuple([z.literal("close")]),
  valuation_fields: z.array(z.enum(["pe_ttm", "pb", "turnover_rate", "total_mv"])).max(4),
  financial_fields: z.array(z.enum(["roe", "gross_margin", "revenue_yoy", "netprofit_yoy"])).max(4),
}).strict()
const selectiveCoverageSchema = legacyCoverageSchema.extend({ coverage_version: z.literal("stock-research-coverage-v2"), dependencies: dependenciesSchema })
export const researchCoverageSchema = z.discriminatedUnion("coverage_version", [legacyCoverageSchema, selectiveCoverageSchema])

export type ResearchCoverage = z.infer<typeof researchCoverageSchema>

export async function getStockResearchCoverage(query: { start_date?: string; end_date?: string; factor_ids?: string[]; mktcap_neutral?: boolean; weight?: "equal" | "mktcap" | "factor" }, token: string, signal?: AbortSignal): Promise<ResearchCoverage> {
  const parsed = z.object({ start_date: z.string().optional(), end_date: z.string().optional(), factor_ids: z.array(z.string().min(1).max(40)).min(1).max(9).refine((items) => new Set(items).size === items.length).optional(), mktcap_neutral: z.boolean().optional(), weight: z.enum(["equal", "mktcap", "factor"]).optional() }).strict().refine((value) => value.factor_ids !== undefined || (value.mktcap_neutral === undefined && value.weight === undefined), "依赖参数需要因子列表").parse(query)
  const parameters = new URLSearchParams()
  for (const key of ["start_date", "end_date"] as const) if (parsed[key]) parameters.set(key, parsed[key])
  if (parsed.factor_ids) {
    parsed.factor_ids.forEach((factor) => parameters.append("factor_ids", factor))
    if (parsed.mktcap_neutral !== undefined) parameters.set("mktcap_neutral", String(parsed.mktcap_neutral))
    if (parsed.weight !== undefined) parameters.set("weight", parsed.weight)
  }
  const timeout = AbortSignal.timeout(20000)
  const response = await fetch(`${(process.env.NEXT_PUBLIC_API_URL ?? "").replace(/\/$/, "")}/api/stock/research/${parsed.factor_ids ? "coverage-v2" : "coverage"}${parameters.size ? `?${parameters}` : ""}`, {
    method: "GET", cache: "no-store", headers: { Authorization: `Bearer ${token}` }, signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
  })
  if (!response.ok) {
    const payload = await response.json().catch(() => null)
    throw Object.assign(new Error(typeof payload?.detail === "string" ? payload.detail : `覆盖预检失败: ${response.status}`), { status: response.status })
  }
  const result = researchCoverageSchema.parse(await response.json())
  if (parsed.factor_ids && result.coverage_version !== "stock-research-coverage-v2") throw new Error("服务端尚未提供按因子依赖的覆盖预检，请更新后端后重试")
  return result
}
