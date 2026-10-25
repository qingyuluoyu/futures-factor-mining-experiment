"use client"

import { FactorLabPage } from "@/components/factor-lab/factor-lab-page"
import { StockResearchEntry } from "@/components/factor-research/stock-research-entry"
import { useAuthStore } from "@/stores/auth"

/** 因子实验室页面 */
export default function Page(): React.JSX.Element {
  const user = useAuthStore((state) => state.user)
  const loaded = useAuthStore((state) => state.loaded)
  if (!loaded || !user) return <p role="status" className="p-4 text-sm">正在验证因子实验室会话…</p>
  return user.market_type === "stock" ? <StockResearchEntry initialTab="analysis" title="A股因子实验室" view="library" /> : <FactorLabPage />
}
