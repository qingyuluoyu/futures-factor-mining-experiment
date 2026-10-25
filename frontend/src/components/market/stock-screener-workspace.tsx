"use client"

import { useState } from "react"
import Link from "next/link"
import { StockMembers } from "@/components/market/stock-board-browser"
import { StockQuoteDetail } from "@/components/market/stock-quote-detail"
import { StockMarketOverview } from "@/components/market/stock-market-overview"
import { StockScreenResults } from "@/components/market/stock-screen-results"
import { StockDataAvailability } from "@/components/market/stock-data-availability"

export function StockScreenerWorkspace(): React.JSX.Element {
  const [selectedCode, setSelectedCode] = useState<string | null>(null)
  const [advanced, setAdvanced] = useState(true)
  const [visitedBasic, setVisitedBasic] = useState(false)
  const [showAvailability, setShowAvailability] = useState(false)
  return <StockMarketOverview collapsible presentation="compact" suppressContext={selectedCode !== null}>
    {(overview) => <><main hidden={selectedCode !== null} inert={selectedCode !== null} aria-label="A股行情筛选工作区" className={`${selectedCode ? "hidden" : "flex"} min-w-0 flex-col gap-4 p-4 text-[var(--text-primary)]`}>
      <header className="flex flex-wrap items-center gap-4">
        <h1 className="text-lg font-semibold">A股行情筛选</h1>
        <Link href="/market" className="text-xs underline">返回板块行情</Link>
        <button type="button" onClick={overview.refresh} className="text-xs underline">刷新筛选数据</button>
        <nav aria-label="股票筛选模式" className="flex flex-wrap gap-3">
          <button type="button" aria-pressed={advanced} onClick={() => setAdvanced(true)} className="text-xs underline">多条件筛选</button>
          <button type="button" aria-pressed={!advanced} onClick={() => { setVisitedBasic(true); setAdvanced(false) }} className="text-xs underline">基础股票目录</button>
        </nav>
        <button type="button" aria-pressed={showAvailability} onClick={() => setShowAvailability((value) => !value)} className="text-xs underline">{showAvailability ? "收起数据覆盖资格" : "查看数据覆盖资格"}</button>
      </header>
      <p className="text-xs leading-6 text-[var(--text-muted)]">按当前行业、股票代码或名称及价格、涨跌幅、成交量条件筛选，显式应用条件后分页查看结果；也可选择基础股票目录。历史日线 · 非实时；缺价不填零，也不代表停牌。点击股票进入K线与详情。</p>
      <section className="space-y-2 rounded border border-[var(--border)] p-3 text-xs" aria-label="行情筛选范围">
        <label className="flex flex-wrap items-center gap-2">行业
          <select aria-label="筛选股票行业" value={overview.industry} onChange={(event) => overview.onIndustryChange(event.target.value)} className="rounded border border-[var(--border)] bg-[var(--bg-secondary)] p-2">
            <option value="">全部行业</option>
            {(overview.boards?.boards ?? []).map((board) => <option key={board.industry} value={board.industry}>{board.industry}</option>)}
            {overview.industry && !overview.boards?.boards.some((board) => board.industry === overview.industry) && <option value={overview.industry}>{overview.industry}（目录待核对）</option>}
          </select>
        </label>
        {overview.boards?.has_more && <p role="status">行业选项仅显示前200个，未列出行业仍可在全部股票中按代码或名称搜索。</p>}
        <p className="text-[var(--text-muted)]">当前行业分类不等同历史成分，筛选结果不是回测股票池，也不代表全市场历史覆盖。</p>
      </section>
      {showAvailability && <StockDataAvailability refreshKey={overview.revision} />}
      <section aria-label="多条件筛选结果" hidden={!advanced} inert={!advanced} className="min-w-0">
        <StockScreenResults key={overview.industry} industry={overview.industry} refreshKey={overview.revision} onSelect={setSelectedCode} />
      </section>
      {visitedBasic && <section aria-label="基础股票目录结果" hidden={advanced} inert={advanced} className="min-w-0">
        <StockMembers key={overview.industry} industry={overview.industry} refreshKey={overview.revision} onSelect={setSelectedCode} />
      </section>}
    </main>{selectedCode && <StockQuoteDetail key={selectedCode} code={selectedCode} onBack={() => setSelectedCode(null)} backLabel="← 返回行情筛选" />}</>}</StockMarketOverview>
}
