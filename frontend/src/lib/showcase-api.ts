/** 首页公开展示 API（无需登录） */

import type {
  AITradingTask,
  EquityPoint,
  ProfitCloseBar,
} from "@/lib/ai-trading-api"

export interface ShowcasePayload {
  updated_at: string
  trading_mode: string
  tasks: AITradingTask[]
  equity_series: Record<string, EquityPoint[]>
  profit_items: ProfitCloseBar[]
  total_realized: number
  total_unrealized: number
  total_pnl: number
  running_count: number
  open_position_count: number
}

const API_BASE = (process.env.NEXT_PUBLIC_API_URL ?? "").replace(/\/$/, "")

/** 拉取运行中 AI 收益展示数据 */
export async function fetchShowcase(
  limit: number,
  tradingMode: string | null,
): Promise<ShowcasePayload> {
  const params = new URLSearchParams()
  params.set("limit", String(limit))
  if (tradingMode === "live" || tradingMode === "virtual") {
    params.set("trading_mode", tradingMode)
  }
  const response = await fetch(
    `${API_BASE}/api/showcase/ai-trading?${params.toString()}`,
    { cache: "no-store" },
  )
  if (!response.ok) {
    throw new Error(`展示数据加载失败: ${response.status}`)
  }
  return response.json() as Promise<ShowcasePayload>
}
