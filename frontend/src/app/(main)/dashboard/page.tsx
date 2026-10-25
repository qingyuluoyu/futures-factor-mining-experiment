"use client"

import { DashboardFrame } from "@/components/dashboard/dashboard-frame"
import { AssetSummaryBar } from "@/components/dashboard/asset-summary"
import { ActiveChannelBadge } from "@/components/dashboard/active-channel-badge"
import { WatchlistTable } from "@/components/dashboard/watchlist"
import { PositionOverview } from "@/components/dashboard/position-overview"
import { RecentTrades } from "@/components/dashboard/recent-trades"
import { AiTradingOverview } from "@/components/dashboard/ai-trading-overview"
import { useAuthStore } from "@/stores/auth"
import { StockDashboard } from "@/components/dashboard/stock-dashboard"

export default function DashboardPage(): React.JSX.Element {
  const market = useAuthStore((state) => state.user?.market_type)
  const userId = useAuthStore((state) => state.user?.id)
  const tradingMode = useAuthStore((state) => state.user?.trading_mode)
  return market === "stock" ? <StockDashboard key={`${userId}:${tradingMode}`} /> : <FuturesDashboardPage />
}

/** 工作台 —— 实时账户/持仓/成交 + AI 交易概览（信号筛选已移至 行情→行情筛选） */
function FuturesDashboardPage(): React.JSX.Element {
  return <DashboardFrame
    channel={<ActiveChannelBadge />} assets={<AssetSummaryBar />}
    watchlist={<WatchlistTable />} positions={<PositionOverview />}
    trades={<RecentTrades />} ai={<AiTradingOverview />}
  />
}
