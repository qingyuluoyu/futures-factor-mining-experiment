"use client"

import { SuperFactorPage } from "@/components/super-factor/super-factor-page"
import { StockResearchHistory } from "@/components/factor-research/stock-research-history"
import { useAuthStore } from "@/stores/auth"

export default function Page() {
  const user = useAuthStore((state) => state.user)
  if (!user) return <p role="status" className="p-4">正在验证会话…</p>
  return user.market_type === "stock" ? <StockResearchHistory /> : <SuperFactorPage />
}
