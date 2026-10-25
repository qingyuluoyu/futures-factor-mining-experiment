import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import test from "node:test"
import { renderToStaticMarkup } from "react-dom/server"
import { jsx } from "react/jsx-runtime"
import { loadDashboardFrame, loadTsx } from "./dashboard-frame-harness.mjs"

test("futures dashboard keeps its exact Card hierarchy and original business slots", () => {
  const frame = loadDashboardFrame()
  const cards = loadTsx(readFileSync(new URL("../ui/card.tsx", import.meta.url), "utf8"), { "@/lib/utils": { cn: (...values) => values.filter(Boolean).join(" ") } })
  const legacy = loadTsx(`import {Card,CardHeader,CardTitle,CardContent} from '@/components/ui/card';
    export function Legacy(p) { return <div className="flex flex-col h-full">{p.channel}{p.assets}
      <div className="flex-1 overflow-auto p-4 space-y-4"><div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <Card className="lg:col-span-2"><CardHeader><CardTitle>自选行情</CardTitle></CardHeader><CardContent>{p.watchlist}</CardContent></Card>
        <div className="space-y-4"><Card><CardHeader><CardTitle>持仓概览</CardTitle></CardHeader><CardContent>{p.positions}</CardContent></Card>
        <Card><CardHeader><CardTitle>最近成交</CardTitle></CardHeader><CardContent>{p.trades}</CardContent></Card></div></div>
        <Card><CardHeader><CardTitle>AI 交易概览</CardTitle></CardHeader><CardContent>{p.ai}</CardContent></Card></div></div> }`, { "@/components/ui/card": cards })
  const props = Object.fromEntries(["channel", "assets", "watchlist", "positions", "trades", "ai"].map((key) => [key, jsx("span", { "data-slot": key, children: key })]))
  assert.equal(renderToStaticMarkup(jsx(frame.DashboardFrame, props)), renderToStaticMarkup(jsx(legacy.Legacy, props)))
  const source = readFileSync(new URL("../../app/(main)/dashboard/page.tsx", import.meta.url), "utf8")
  const slot = (name) => () => jsx("span", { "data-slot": name, children: name })
  const dependencies = {
    "@/components/dashboard/dashboard-frame": frame,
    "@/components/dashboard/active-channel-badge": { ActiveChannelBadge: slot("channel") },
    "@/components/dashboard/asset-summary": { AssetSummaryBar: slot("assets") },
    "@/components/dashboard/watchlist": { WatchlistTable: slot("watchlist") },
    "@/components/dashboard/position-overview": { PositionOverview: slot("positions") },
    "@/components/dashboard/recent-trades": { RecentTrades: slot("trades") },
    "@/components/dashboard/ai-trading-overview": { AiTradingOverview: slot("ai") },
    "@/components/dashboard/stock-dashboard": { StockDashboard: () => jsx("span", { children: "stock" }) },
    "@/stores/auth": { useAuthStore: (selector) => selector({ user: { market_type: "futures", id: "qa", trading_mode: "virtual" } }) },
  }
  assert.equal(renderToStaticMarkup(jsx(loadTsx(source, dependencies).default, {})), renderToStaticMarkup(jsx(legacy.Legacy, props)))
  dependencies["@/stores/auth"] = { useAuthStore: (selector) => selector({ user: { market_type: "stock", id: "qa-stock", trading_mode: "virtual" } }) }
  assert.equal(renderToStaticMarkup(jsx(loadTsx(source, dependencies).default, {})), "<span>stock</span>")
})

test("anonymous and authenticated dashboard reference the same presentation component", () => {
  const login = readFileSync(new URL("../landing/auth/login-dashboard-background.tsx", import.meta.url), "utf8")
  assert.match(login, /<DashboardFrame/)
  assert.doesNotMatch(login, /公开数据看板/)
  const source = readFileSync(new URL("./dashboard-frame.tsx", import.meta.url), "utf8")
  assert.doesNotMatch(source, /useAuth|useEffect|fetch\(|WebSocket|localStorage/)
})
