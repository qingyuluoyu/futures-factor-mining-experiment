import { z } from "zod"
import { request } from "./api.ts"
import { stockMembersSchema } from "./stock-market-api.ts"

const filtersSchema = z.object({
  industry: z.string().max(32).default(""), keyword: z.string().max(64).default(""),
  sort: z.enum(["code", "name", "change_pct"]).default("code"), direction: z.enum(["asc", "desc"]).default("asc"),
  data_only: z.boolean().default(false),
  min_close: z.number().finite().nonnegative().nullable().default(null), max_close: z.number().finite().positive().nullable().default(null),
  min_change_pct: z.number().finite().nullable().default(null), max_change_pct: z.number().finite().nullable().default(null),
  min_volume: z.number().finite().nonnegative().nullable().default(null),
  limit: z.number().int().min(1).max(100).default(50), offset: z.number().int().min(0).max(10000).default(0),
  expected_fingerprint: z.string().regex(/^[a-f0-9]{64}$/).nullable().default(null),
}).strict().refine((value) => (value.min_close === null || value.max_close === null || value.min_close <= value.max_close) && (value.min_change_pct === null || value.max_change_pct === null || value.min_change_pct <= value.max_change_pct), "筛选下限不能大于上限")

const responseSchema = stockMembersSchema.safeExtend({
  screen_version: z.literal("stock-screen-v1"), applied_filters: filtersSchema,
  screen_fingerprint: z.string().regex(/^[a-f0-9]{64}$/),
  filter_units: z.object({ price: z.literal("CNY_per_share"), change_pct: z.literal("percent"), volume: z.string().min(1) }),
})
export type StockScreenFilters = z.infer<typeof filtersSchema>
export type StockScreenResponse = z.infer<typeof responseSchema>

export function parseStockScreenDraft(draft: Record<string, string>, industry = ""): StockScreenFilters {
  const values: Record<string, unknown> = { industry, keyword: draft.keyword ?? "", sort: draft.sort ?? "code", direction: draft.direction ?? "asc", data_only: draft.data_only === "true" }
  for (const field of ["min_close", "max_close", "min_change_pct", "max_change_pct", "min_volume"]) {
    const input = (draft[field] ?? "").trim()
    if (input && !/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(input)) throw new Error("筛选数字格式无效")
    values[field] = input ? Number(input) : null
  }
  const checked = filtersSchema.safeParse(values)
  if (!checked.success) throw new Error("筛选条件无效：下限不能大于上限，价格和成交量不得为负，数字必须有限")
  return checked.data
}

export async function getStockScreenApi(filters: Partial<StockScreenFilters> = {}, signal?: AbortSignal): Promise<StockScreenResponse> {
  const checked = filtersSchema.safeParse(filters)
  if (!checked.success) throw new Error("筛选条件无效，请检查范围或分页")
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(checked.data)) if (value !== null) params.set(key, String(value))
  const timeout = AbortSignal.timeout(20000)
  const parsed = responseSchema.safeParse(await request<unknown>(`/api/stock/market/screen?${params}`, { signal: signal ? AbortSignal.any([signal, timeout]) : timeout }))
  if (!parsed.success) throw new Error("筛选响应格式或版本异常，请核对后端；未回退为忽略条件的查询")
  if (JSON.stringify(parsed.data.applied_filters) !== JSON.stringify(checked.data) || parsed.data.filter_units.volume !== parsed.data.volume_unit) throw new Error("服务端应用条件或成交量单位与本次请求不一致，未展示不可信筛选结果")
  return parsed.data
}
