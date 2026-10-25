import { z } from "zod"
import { request } from "./api.ts"

const money = z.string().regex(/^-?\d+(\.\d+)?$/)
const date = z.iso.date()
const instrument = z.object({ code: z.string().regex(/^\d{6}$/), name: z.string(), instrument_id: z.string() })
const accountSummary = z.object({
  id: z.string(), name: z.string(), current_date: date,
  status: z.enum(["active", "completed"]), revision: z.number().int().nonnegative(),
})
const dataset = instrument.extend({
  id: z.string(), start_date: date, end_date: date, version: z.string(), source: z.string(), adjustment: z.literal("none"),
})
const position = instrument.extend({
  quantity: z.number().int().nonnegative(), available_quantity: z.number().int().nonnegative(),
  cost_price: money, last_price: money, market_value: money, unrealized_pnl: money,
})
const order = z.object({
  id: z.string(), code: z.string().regex(/^\d{6}$/), direction: z.enum(["buy", "sell"]),
  quantity: z.number().int().positive(), order_type: z.enum(["market", "limit"]), limit_price: money.nullable(),
  status: z.enum(["pending", "filled", "rejected", "cancelled"]), reason: z.string().nullable(),
  submitted_on: date, filled_on: date.nullable(), filled_price: money.nullable(),
  fees: money, cash_delta: money.nullable(), realized_pnl: money,
})
const bar = z.object({
  time: date, open: z.number().positive().finite(), high: z.number().positive().finite(),
  low: z.number().positive().finite(), close: z.number().positive().finite(), volume: z.number().nonnegative().finite(),
})
const replayView = accountSummary.extend({
  market_type: z.literal("stock"), trading_mode: z.enum(["live", "virtual"]), method_version: z.string(),
  price_adjustment: z.literal("none"), currency: z.literal("CNY"),
  initial_cash: money, cash: money, available_cash: money, reserved_cash: money, equity: money,
  total_pnl: money, fees_paid: money, realized_pnl: money,
  start_date: date, end_date: date, positions: z.array(position), orders: z.array(order).max(300),
  instruments: z.array(instrument).min(1).max(20), data_versions: z.array(z.string()),
  chart: z.object({ code: z.string(), adjustment: z.literal("none"), volume_unit: z.literal("share"), bars: z.array(bar).max(250) }),
}).superRefine((value, context) => {
  if (value.current_date < value.start_date || value.current_date > value.end_date ||
    !value.instruments.some((item) => item.code === value.chart.code) ||
    value.chart.bars.some((item, index, bars) => item.time > value.current_date ||
      (index > 0 && item.time <= bars[index - 1].time) ||
      item.low > Math.min(item.open, item.close) || item.high < Math.max(item.open, item.close) || item.low > item.high)) {
    context.addIssue({ code: "custom", message: "回放日期、标的或日线范围异常" })
  }
})

export type ReplayDataset = z.infer<typeof dataset>
export type ReplayAccountSummary = z.infer<typeof accountSummary>
export type ReplayPosition = z.infer<typeof position>
export type ReplayOrder = z.infer<typeof order>
export type ReplayView = z.infer<typeof replayView>
export interface ReplayAvailability { enabled: boolean; datasets: ReplayDataset[] }
export const stockReplayAvailabilitySchema = z.object({ enabled: z.boolean(), datasets: z.array(dataset).max(100) })
export interface ReplayCommand { idempotency_key: string; expected_revision: number }
export interface ReplayCreateInput {
  idempotency_key: string
  name: string
  initial_cash: string
  start_date: string
  dataset_ids: string[]
  commission_rate?: string
  min_commission?: string
  slippage_rate?: string
}
export interface ReplayOrderInput extends ReplayCommand {
  code: string
  direction: "buy" | "sell"
  quantity: number
  order_type: "market" | "limit"
  limit_price: string | null
}

async function replayRequest<Output>(path: string, schema: z.ZodType<Output>, body?: unknown, signal?: AbortSignal): Promise<Output> {
  const timeout = AbortSignal.timeout(20000)
  const result = await request<unknown>(`/api/stock/replay${path}`, {
    method: body === undefined ? "GET" : "POST",
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
  })
  const parsed = schema.safeParse(result)
  if (!parsed.success) throw new Error("回放数据结构异常，请刷新或联系管理员核对数据。")
  return parsed.data
}

export function getStockReplayAvailability(signal?: AbortSignal): Promise<ReplayAvailability> {
  return replayRequest("/availability", z.object({ enabled: z.boolean(), datasets: z.array(dataset).max(100) }), undefined, signal)
}

export function listStockReplayAccounts(limit = 20, offset = 0, signal?: AbortSignal): Promise<ReplayAccountSummary[]> {
  if (!Number.isInteger(limit) || limit < 1 || limit > 100 || !Number.isInteger(offset) || offset < 0) throw new Error("账户分页参数无效")
  return replayRequest(`/accounts?limit=${limit}&offset=${offset}`, z.array(accountSummary), undefined, signal)
}

export function getStockReplayAccount(id: string, code?: string, signal?: AbortSignal): Promise<ReplayView> {
  return replayRequest(`/accounts/${encodeURIComponent(id)}${code ? `?code=${encodeURIComponent(code)}` : ""}`, replayView, undefined, signal)
}

export function createStockReplayAccount(input: ReplayCreateInput, signal?: AbortSignal): Promise<ReplayView> {
  return replayRequest("/accounts", replayView, input, signal)
}

export function submitStockReplayOrder(id: string, input: ReplayOrderInput, signal?: AbortSignal): Promise<ReplayView> {
  return replayRequest(`/accounts/${encodeURIComponent(id)}/orders`, replayView, input, signal)
}

export function advanceStockReplayAccount(id: string, input: ReplayCommand, signal?: AbortSignal): Promise<ReplayView> {
  return replayRequest(`/accounts/${encodeURIComponent(id)}/step`, replayView, input, signal)
}

export function cancelStockReplayOrder(id: string, orderId: string, input: ReplayCommand, signal?: AbortSignal): Promise<ReplayView> {
  return replayRequest(`/accounts/${encodeURIComponent(id)}/orders/${encodeURIComponent(orderId)}/cancel`, replayView, input, signal)
}
