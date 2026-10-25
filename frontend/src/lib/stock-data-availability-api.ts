import { z } from "zod"
import { stockBoardsSchema, type StockBoardsResponse } from "./stock-market-api.ts"
import { stockReplayAvailabilitySchema, type ReplayAvailability } from "./stock-replay-api.ts"
import { getStockResearchCoverage, type ResearchCoverage } from "./stock-research-coverage.ts"
import { researchAvailabilitySchema } from "./stock-research-api.ts"

const marketAvailabilitySchema = stockBoardsSchema.refine((value) => value.coverage.comparable <= value.coverage.quoted && value.coverage.quoted <= value.coverage.instruments, "行情覆盖数量冲突")
const replayAvailabilitySchema = stockReplayAvailabilitySchema.refine((value) => (value.enabled || value.datasets.length === 0) && value.datasets.every((dataset) => dataset.start_date <= dataset.end_date && dataset.version.length > 0) && new Set(value.datasets.map((dataset) => dataset.id)).size === value.datasets.length, "回放注册状态、区间或版本冲突")

export type AvailabilityPeriod = { start_date?: string; end_date?: string }
export type AvailabilityRead<Data> = { state: "ready"; data: Data } | { state: "failed"; message: string; request_id: string | null }
export interface StockDataAvailability {
  market: AvailabilityRead<StockBoardsResponse>
  coverage: AvailabilityRead<ResearchCoverage>
  replay: AvailabilityRead<ReplayAvailability>
  research: AvailabilityRead<z.infer<typeof researchAvailabilitySchema>>
}

export function parseAvailabilityPeriod(draft: AvailabilityPeriod, today = new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Shanghai" }).format(new Date())): AvailabilityPeriod {
  const parsed = z.object({ start_date: z.string().optional(), end_date: z.string().optional() }).strict().parse(draft)
  const result: AvailabilityPeriod = {}
  for (const key of ["start_date", "end_date"] as const) {
    if (!parsed[key]) continue
    if (!z.iso.date().safeParse(parsed[key]).success || parsed[key] > today) throw new Error("覆盖日期必须有效且不晚于上海今天")
    result[key] = parsed[key]
  }
  const end = result.end_date ?? today
  if (result.start_date && (result.start_date > end || (Date.parse(end) - Date.parse(result.start_date)) / 86400000 > 3660)) throw new Error("开始不得晚于结束，覆盖区间不得超过3660天")
  return result
}

export async function getStockDataAvailability(period: AvailabilityPeriod, token: string, signal?: AbortSignal): Promise<StockDataAvailability> {
  const query = parseAvailabilityPeriod(period)
  if (!token || token.length > 8192) throw new Error("请先登录后查看股票覆盖")
  signal?.throwIfAborted()
  const timeout = AbortSignal.timeout(20000)
  const bounded = signal ? AbortSignal.any([signal, timeout]) : timeout
  async function read<Data>(label: string, callback: () => Promise<Data>): Promise<AvailabilityRead<Data>> {
    try {
      return { state: "ready", data: await callback() }
    } catch (failure) {
      if (signal?.aborted) throw failure
      const error = failure as { status?: unknown; request_id?: unknown }
      const status = typeof error?.status === "number" ? `（HTTP ${error.status}）` : ""
      return { state: "failed", message: `${label}读取失败${status}，未用零值代替；请重试`, request_id: typeof error?.request_id === "string" && /^[a-zA-Z0-9_-]{1,128}$/.test(error.request_id) ? error.request_id : null }
    }
  }
  async function fetchSource<Schema extends z.ZodType>(path: string, schema: Schema): Promise<z.infer<Schema>> {
    const response = await fetch(`${(process.env.NEXT_PUBLIC_API_URL ?? "").replace(/\/$/, "")}${path}`, { method: "GET", cache: "no-store", headers: { Authorization: `Bearer ${token}` }, signal: bounded })
    if (!response.ok) throw Object.assign(new Error("股票覆盖读取失败"), { status: response.status, request_id: response.headers.get("X-Request-ID") })
    return schema.parse(await response.json())
  }
  const [market, coverage, replay, research] = await Promise.all([
    read("目录与当日行情", () => fetchSource("/api/stock/market/boards?limit=200&offset=0", marketAvailabilitySchema)),
    read("历史研究覆盖", () => getStockResearchCoverage(query, token, bounded)),
    read("回放注册", () => fetchSource("/api/stock/replay/availability", replayAvailabilitySchema)),
    read("研究执行器", () => fetchSource("/api/stock/research/availability", researchAvailabilitySchema)),
  ])
  signal?.throwIfAborted()
  return { market, coverage, replay, research }
}
