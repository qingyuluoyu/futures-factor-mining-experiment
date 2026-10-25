"use client"

import { useEffect } from "react"
import { useAuthStore } from "@/stores/auth"
import { usePaperTradingStore } from "@/stores/paper-trading"

export function FinancialSessionSync(): null {
  const token = useAuthStore((state) => state.accessToken)
  const revision = useAuthStore((state) => state.sessionRevision)
  const userId = useAuthStore((state) => state.user?.id)
  const market = useAuthStore((state) => state.user?.market_type)
  const mode = useAuthStore((state) => state.user?.trading_mode)
  const loaded = useAuthStore((state) => state.loaded)
  const pending = useAuthStore((state) => state.marketSwitchPending)
  const refresh = usePaperTradingStore((state) => state.refresh)
  useEffect(() => {
    if (!pending && loaded && token && userId && market === "futures") void refresh()
  }, [pending, loaded, token, revision, userId, market, mode, refresh])
  return null
}
