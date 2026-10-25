import type { ReactNode } from "react"
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card"

export interface DashboardFrameProps {
  channel: ReactNode
  assets: ReactNode
  watchlist: ReactNode
  positions: ReactNode
  trades: ReactNode
  ai: ReactNode
}

/** Presentation only; both the private dashboard and anonymous backdrop use it. */
export function DashboardFrame({ channel, assets, watchlist, positions, trades, ai }: DashboardFrameProps): React.JSX.Element {
  return <div className="flex flex-col h-full">
    {channel}{assets}
    <div className="flex-1 overflow-auto p-4 space-y-4">
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <Card className="lg:col-span-2"><CardHeader><CardTitle>自选行情</CardTitle></CardHeader><CardContent>{watchlist}</CardContent></Card>
        <div className="space-y-4">
          <Card><CardHeader><CardTitle>持仓概览</CardTitle></CardHeader><CardContent>{positions}</CardContent></Card>
          <Card><CardHeader><CardTitle>最近成交</CardTitle></CardHeader><CardContent>{trades}</CardContent></Card>
        </div>
      </div>
      <Card><CardHeader><CardTitle>AI 交易概览</CardTitle></CardHeader><CardContent>{ai}</CardContent></Card>
    </div>
  </div>
}
