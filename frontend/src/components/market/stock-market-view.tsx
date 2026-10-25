"use client"

import { useEffect } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import { StockBoardBrowser } from "./stock-board-browser"
import { StockQuoteDetail } from "./stock-quote-detail"
import { StockMarketOverview } from "./stock-market-overview"
import { useStockMarketStore } from "@/stores/stock-market"
import { parseStockIndustry, stockMarketHref } from "@/lib/stock-market-navigation"

export function StockMarketView(): React.JSX.Element {
  const router = useRouter()
  const params = useSearchParams()
  const selected = params.get("symbol")
  const industryScope = parseStockIndustry(params.get("industry"))
  const industry = industryScope.value ?? ""
  const code = selected && /^\d{6}$/.test(selected) ? selected : null
  const selectStock = useStockMarketStore((state) => state.selectStock)
  useEffect(() => { if (code && !industryScope.error) selectStock(code) }, [code, selectStock, industryScope.error])
  if (industryScope.error) return <div role="alert" className="space-y-3 p-4 text-sm text-[var(--accent-danger)]"><p>{industryScope.error}</p><button type="button" onClick={() => router.push("/market", { scroll: false })} className="underline">返回板块行情</button></div>
  return <div className="h-full min-w-0 flex-1 overflow-hidden">
    <div hidden={code !== null} inert={code !== null} className={code ? "hidden" : "h-full"}><StockMarketOverview collapsible presentation="compact" industry={industry} onIndustryChange={(value) => router.push(stockMarketHref(value), { scroll: false })}>{(overview) => <StockBoardBrowser {...overview} onSelect={(value) => { if (!/^\d{6}$/.test(value)) return; selectStock(value); router.push(stockMarketHref(industry, value), { scroll: false }) }} />}</StockMarketOverview></div>
    {code && <StockQuoteDetail key={code} code={code} onBack={() => router.push(stockMarketHref(industry), { scroll: false })} />}
  </div>
}
