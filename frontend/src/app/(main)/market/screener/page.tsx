"use client"

import { Suspense } from "react"
import { ScreenerWorkspace } from "@/components/screener/screener-workspace"
import { StockScreenerWorkspace } from "@/components/market/stock-screener-workspace"
import { useAuthStore } from "@/stores/auth"

/** 行情筛选页（行情 → 行情筛选） */
export default function MarketScreenerPage(): React.JSX.Element {
  const user = useAuthStore((state) => state.user)
  const loaded = useAuthStore((state) => state.loaded)
  if (!loaded || !user) return <p role="status" className="p-4 text-sm">正在验证行情筛选会话…</p>
  if (user.market_type === "stock") return <StockScreenerWorkspace />
  return (
    <Suspense
      fallback={
        <div className="h-full flex items-center justify-center text-sm text-[var(--text-muted)]">
          加载行情筛选…
        </div>
      }
    >
      <ScreenerWorkspace />
    </Suspense>
  )
}
