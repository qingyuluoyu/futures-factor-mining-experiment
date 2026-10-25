import type { ReplayCreateInput, ReplayDataset, ReplayOrderInput } from "../../lib/stock-replay-api.ts"

export function replayAccountStorageKey(userId: string, tradingMode: "live" | "virtual"): string {
  return `stock-replay-account:${encodeURIComponent(userId)}:${tradingMode}`
}

export interface ReplayMutation<Payload extends object> {
  signature: string
  body: Payload & { idempotency_key: string }
}

export function prepareReplayMutation<Payload extends object>(
  previous: ReplayMutation<Payload> | null,
  action: string,
  payload: Payload,
  uuid: () => string = () => crypto.randomUUID(),
): ReplayMutation<Payload> {
  const signature = JSON.stringify([action, payload])
  if (previous?.signature === signature) return previous
  return { signature, body: { ...payload, idempotency_key: uuid() } }
}

export function replayCreateError(
  input: Omit<ReplayCreateInput, "idempotency_key">,
  datasets: (Pick<ReplayDataset, "id" | "start_date" | "end_date"> & { code?: string })[],
): string | null {
  if (!input.name.trim() || input.name.trim().length > 64) return "账户名称需为1至64个字符"
  if (!/^\d+(\.\d{1,2})?$/.test(input.initial_cash) || Number(input.initial_cash) < 100 || Number(input.initial_cash) > 1000000000) return "初始资金需为100至1,000,000,000元，最多两位小数"
  if (input.dataset_ids.length < 1 || input.dataset_ids.length > 20) return "请选择1至20个数据快照"
  const selected = datasets.filter((item) => input.dataset_ids.includes(item.id))
  if (selected.length !== input.dataset_ids.length) return "快照已失效或重复，请重新选择"
  const codes = selected.flatMap((item) => item.code ? [item.code] : [])
  if (new Set(codes).size !== codes.length) return "同一股票只能选择一个快照版本"
  if (!/^\d{4}-\d{2}-\d{2}$/.test(input.start_date) || selected.some((item) => input.start_date < item.start_date || input.start_date > item.end_date)) return "起始日期必须位于所有快照的共同范围内；交易日由服务端校验"
  return null
}

export function replayOrderReason(reason: string | null): string | null {
  if (reason === null) return null
  const labels: Record<string, string> = {
    limit_up: "开盘涨停，按保守规则拒绝买入",
    limit_down: "开盘跌停，按保守规则拒绝卖出",
    limit_not_crossed_at_open: "开盘价未满足限价，委托继续等待",
    liquidity_limit: "超过当日成交量参与上限",
    insufficient_cash: "开盘撮合时可用资金不足（含费用与滑点）",
    t_plus_one_or_insufficient_position: "T+1限制或可卖持仓不足",
    user_cancelled: "用户撤单",
    end_of_replay: "回放结束，未成交委托已撤销",
  }
  return labels[reason] ? `${labels[reason]}（${reason}）` : reason
}

export function replayOrderError(
  input: Omit<ReplayOrderInput, "idempotency_key" | "expected_revision">,
  instruments: { code: string }[],
  positions: { code: string; available_quantity: number }[],
): string | null {
  if (!instruments.some((item) => item.code === input.code)) return "请选择当前账户快照中的股票"
  if (!Number.isSafeInteger(input.quantity) || input.quantity <= 0 || input.quantity % 100 !== 0) return "委托股数必须为100的正整数倍"
  if (input.quantity > 1000000) return "单笔委托最多1,000,000股"
  if (input.direction === "sell" && input.quantity > (positions.find((item) => item.code === input.code)?.available_quantity ?? 0)) return "委托股数超过次日可卖数量；买入需遵守T+1"
  if (input.order_type === "limit" && (!input.limit_price || !/^\d+(\.\d{1,2})?$/.test(input.limit_price) || Number(input.limit_price) <= 0 || Number(input.limit_price) > 1000000)) return "限价需大于0且不超过1,000,000元，最多两位小数"
  return null
}
