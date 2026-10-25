"use client"

import Link from "next/link"
import { StockMarketOverview } from "@/components/market/stock-market-overview"
import { cn } from "@/lib/utils"
import { parseStockIndustry, stockMarketHref } from "@/lib/stock-market-navigation"

const control = "rounded-md border border-[var(--border)] px-3 py-2 text-xs hover:bg-[var(--bg-tertiary)] disabled:opacity-40"
const panel = "min-w-0 rounded-xl border border-[var(--border)] bg-[var(--bg-secondary)] p-4"
const percentage = (value: number | null): string => value === null ? "—" : `${value > 0 ? "+" : ""}${value.toFixed(2)}%`

export function StockDashboard(): React.JSX.Element {
  // Summary and read-only entry points; stock browsing and K-lines belong to the market route.
  return <StockMarketOverview collapsible presentation="compact">
    {(overview) => {
      const result = overview.boards
      const breadth = result?.boards.reduce((sum, board) => ({ advancing: sum.advancing + board.advancing, declining: sum.declining + board.declining, flat: sum.flat + board.flat }), { advancing: 0, declining: 0, flat: 0 })
      const sampleCount = breadth ? breadth.advancing + breadth.declining + breadth.flat : 0
      const rankedBoards = result?.boards.filter((board) => board.change_pct !== null).sort((left, right) => right.change_pct! - left.change_pct!).slice(0, 6) ?? []
      return <>
        <div className="flex min-w-0 flex-col gap-4 p-4 md:p-6">
          <header className="flex flex-wrap items-start justify-between gap-3">
            <div><p className="text-[10px] tracking-[0.2em] text-[var(--primary)]">A-SHARE / MARKET OVERVIEW</p><h1 className="mt-1 text-xl font-semibold">A股工作台</h1><p className="mt-1 text-xs text-[var(--text-muted)]">市场摘要与研究入口；板块浏览、选股筛选和K线各在对应页面。</p></div>
            <div className="flex flex-wrap gap-2"><Link href="/market" className={control}>行情与板块</Link><Link href="/market/screener" className={control}>多条件选股</Link><button className={control} disabled={overview.loading} onClick={overview.refresh}>{overview.loading ? "刷新中…" : "刷新看板"}</button></div>
          </header>
          {result && !overview.error && <>
            <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
              <section className={panel} aria-label="样本涨跌分布"><h2 className="text-sm font-semibold">可比样本涨跌</h2><p className="mt-1 text-[11px] leading-5 text-[var(--text-muted)]">已展示板块的 {sampleCount} 只可比样本{result.has_more ? " · 仅部分板块，未完整覆盖" : ""}，不代表全市场或官方指数。</p>
                <div role="img" aria-label={`样本涨${breadth?.advancing ?? 0} 跌${breadth?.declining ?? 0} 平${breadth?.flat ?? 0}`} className="my-4 flex h-3 overflow-hidden rounded-full bg-[var(--bg-tertiary)]"><span className="bg-[var(--accent-down)]" style={{ width: `${(breadth?.declining ?? 0) / Math.max(sampleCount, 1) * 100}%` }} /><span className="bg-[var(--text-muted)]/40" style={{ width: `${(breadth?.flat ?? 0) / Math.max(sampleCount, 1) * 100}%` }} /><span className="bg-[var(--accent-up)]" style={{ width: `${(breadth?.advancing ?? 0) / Math.max(sampleCount, 1) * 100}%` }} /></div>
                <div className="grid grid-cols-3 gap-2 text-center text-xs"><div className="rounded bg-[var(--bg-tertiary)] p-2 text-up">涨 <strong className="font-num">{breadth?.advancing ?? 0}</strong></div><div className="rounded bg-[var(--bg-tertiary)] p-2 text-down">跌 <strong className="font-num">{breadth?.declining ?? 0}</strong></div><div className="rounded bg-[var(--bg-tertiary)] p-2 text-[var(--text-muted)]">平 <strong className="font-num">{breadth?.flat ?? 0}</strong></div></div>
                {sampleCount === 0 && <p className="mt-3 text-xs text-[var(--text-muted)]">暂无可比行情数据，未以零涨跌替代缺价。</p>}
              </section>
              <section className={cn(panel, "xl:col-span-2")} aria-label="行业样本排行"><div className="flex items-center justify-between gap-2"><h2 className="text-sm font-semibold">行业样本涨幅排序</h2><Link href="/market" className="text-xs text-[var(--primary)]">查看全部板块 →</Link></div><p className="mt-1 text-[11px] text-[var(--text-muted)]">有可比样本的行业等权均值，最多展示6个；点击进入行情页查看该行业成分股。</p>
                <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3">{rankedBoards.map((board) => parseStockIndustry(board.industry).error
                  ? <div key={board.industry} className="rounded-lg border border-[var(--border)] p-3 text-xs text-[var(--text-muted)]">{board.industry} · 分类名称超出查询范围，请在行情页手动查找。</div>
                  : <Link key={board.industry} href={stockMarketHref(board.industry)} className="min-w-0 rounded-lg border border-[var(--border)] p-3 text-left hover:border-[var(--primary)]"><span className="block truncate text-xs font-medium">{board.industry}</span><strong className={cn("mt-1 block font-num text-lg", board.change_pct! > 0 ? "text-up" : board.change_pct! < 0 ? "text-down" : "text-[var(--text-muted)]")}>{percentage(board.change_pct)}</strong><span className="mt-1 block text-[10px] text-[var(--text-muted)]">样本 {board.comparable_count} / {board.count}</span></Link>)}</div>
                {rankedBoards.length === 0 && <p className="py-6 text-sm text-[var(--text-muted)]">暂无可比板块行情，可在股票目录中继续浏览。</p>}
              </section>
            </div>
            <p className="rounded-lg border border-[var(--accent-warn)]/30 p-3 text-xs leading-5 text-[var(--text-secondary)]">盘后历史日线 · 非实时盘口。行业为当前分类；样本均值不是行业指数，缺价不代表停牌。K线指标仅依赖已载入数据，不足周期显示 —；前复权研究图表不能代替回放撮合数据。</p>
          </>}
          <section className={panel} aria-label="A股研究与模拟入口"><h2 className="text-sm font-semibold">研究与模拟</h2><p className="mt-1 text-xs text-[var(--text-muted)]">因子定义、有效性检验、组合回测、模拟交易与历史结果各在专页完成。</p><div className="mt-3 grid grid-cols-2 gap-2 lg:grid-cols-5">{[
            ["/factor-lab", "因子库", "内置公式与自定义版本"], ["/factor-research", "有效性检验", "预处理、IC与分组分析"], ["/backtest", "组合回测", "参数、绩效与调仓明细"], ["/trading", "模拟交易", "历史日线回放与委托"], ["/history", "研究历史", "已保存结果与参数恢复"],
          ].map(([href, label, note]) => <Link key={href} href={href} className="rounded-lg border border-[var(--border)] p-3 hover:border-[var(--primary)]"><strong className="block text-sm">{label}</strong><span className="mt-1 block text-[10px] leading-5 text-[var(--text-muted)]">{note}</span></Link>)}</div></section>
        </div>
      </>
    }}
  </StockMarketOverview>
}
