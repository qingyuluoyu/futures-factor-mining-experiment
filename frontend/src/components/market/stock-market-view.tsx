"use client"

import { useEffect } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import { StockBoardBrowser } from "./stock-board-browser"
import { StockQuoteDetail } from "./stock-quote-detail"
import { useStockMarketStore } from "@/stores/stock-market"

export function StockMarketView(): React.JSX.Element {
  const router = useRouter()
  const params = useSearchParams()
  const selected = params.get("symbol")
  const code = selected && /^\d{6}$/.test(selected) ? selected : null
  const selectStock = useStockMarketStore((state) => state.selectStock)
  useEffect(() => { if (code) selectStock(code) }, [code, selectStock])
  return <div className="h-full min-w-0 flex-1 overflow-hidden">
    <div className={code ? "hidden" : "h-full"}><StockBoardBrowser onSelect={(value) => { selectStock(value); router.push(`/market?symbol=${value}`, { scroll: false }) }} /></div>
    {code && <StockQuoteDetail key={code} code={code} onBack={() => router.push("/market", { scroll: false })} />}
  </div>
}
