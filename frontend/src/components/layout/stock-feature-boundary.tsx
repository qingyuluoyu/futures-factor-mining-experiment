"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import { ShieldCheck } from "lucide-react"
import { useAuthStore } from "@/stores/auth"
import { supportsStockRoute } from "@/lib/market-routes"

export function StockFeatureBoundary({ children }: { children: React.ReactNode }): React.JSX.Element {
  const pathname = usePathname()
  const stockMarket = useAuthStore((state) => state.user?.market_type === "stock")
  const pending = useAuthStore((state) => state.marketSwitchPending)
  if (pending) return <section role="status" className="p-8 text-sm text-[var(--text-muted)]">正在确认市场切换，请稍候…</section>
  if (!stockMarket || supportsStockRoute(pathname)) return <>{children}</>
  return (
    <section className="mx-auto flex h-full max-w-2xl flex-col justify-center gap-5 px-8 py-12">
      <ShieldCheck className="h-9 w-9 text-[var(--primary)]" />
      <h1 className="text-xl font-semibold text-[var(--text-primary)]">A 股模拟盘 · 独立账户边界</h1>
      <p className="text-sm leading-7 text-[var(--text-secondary)]">此模块的股票链路尚未接通，因此不会加载期货账户、订单或交易指令。A股行情筛选、因子检验、组合回测和日线回放已提供独立入口；不会用期货的卖空、杠杆和保证金规则替代股票规则。</p>
      <p className="text-sm text-[var(--text-muted)]">期货原有模拟功能保留，可通过顶部切回期货继续使用。系统不需要接入真实资金账户。</p>
      <div className="flex flex-wrap gap-3">
        <Link href="/market" className="rounded bg-[var(--primary)] px-4 py-2 text-sm text-white">查看 A 股行情</Link>
        <Link href="/trading" className="rounded border border-[var(--border)] px-4 py-2 text-sm text-[var(--text-primary)]">进入日线回放</Link>
        <Link href="/factor-lab" className="rounded border border-[var(--border)] px-4 py-2 text-sm text-[var(--text-primary)]">进入因子实验室</Link>
        <Link href="/backtest" className="rounded border border-[var(--border)] px-4 py-2 text-sm text-[var(--text-primary)]">进入历史回测</Link>
      </div>
    </section>
  )
}
