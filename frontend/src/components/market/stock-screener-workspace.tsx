"use client"

import { useEffect, useState } from "react"
import Link from "next/link"
import { StockMembers } from "@/components/market/stock-board-browser"
import { StockQuoteDetail } from "@/components/market/stock-quote-detail"
import { StockEodStatus } from "@/components/market/stock-eod-status"
import { getStockBoardsApi, type StockBoardsResponse } from "@/lib/stock-market-api"
import { StockScreenResults } from "@/components/market/stock-screen-results"
import { StockDataAvailability } from "@/components/market/stock-data-availability"

export function StockScreenerWorkspace(): React.JSX.Element {
  const [selectedCode, setSelectedCode] = useState<string | null>(null)
  const [industry, setIndustry] = useState("")
  const [boards, setBoards] = useState<StockBoardsResponse | null>(null)
  const [error, setError] = useState("")
  const [revision, setRevision] = useState(0)
  const [advanced, setAdvanced] = useState(false)
  const [showAvailability, setShowAvailability] = useState(false)
  useEffect(() => {
    const controller = new AbortController()
    setBoards(null)
    setError("")
    void getStockBoardsApi("", controller.signal)
      .then((result) => { if (!controller.signal.aborted) setBoards(result) })
      .catch((failure: unknown) => { if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : "行业目录读取失败") })
    return () => controller.abort()
  }, [revision])
  return <><main hidden={selectedCode !== null} inert={selectedCode !== null} aria-label="A股行情筛选工作区" className={`${selectedCode ? "hidden" : "flex"} h-full min-h-0 flex-col gap-4 overflow-y-auto p-4 text-[var(--text-primary)]`}>
    <header className="flex flex-wrap items-center gap-4">
      <h1 className="text-lg font-semibold">A股行情筛选</h1>
      <Link href="/market" className="text-xs underline">返回板块行情</Link>
      <button type="button" onClick={() => setRevision((value) => value + 1)} className="text-xs underline">刷新筛选数据</button>
      <button type="button" aria-pressed={advanced} onClick={() => setAdvanced((value) => !value)} className="text-xs underline">{advanced ? "返回基础筛选" : "打开多条件筛选"}</button>
      <button type="button" aria-pressed={showAvailability} onClick={() => setShowAvailability((value) => !value)} className="text-xs underline">{showAvailability ? "收起数据覆盖资格" : "查看数据覆盖资格"}</button>
    </header>
    <p className="text-xs leading-6 text-[var(--text-muted)]">按当前行业目录、股票代码或名称筛选，支持涨跌幅排序和分页。历史日线 · 非实时；缺价不填零，也不代表停牌。点击股票进入K线与详情。</p>
    <details className="rounded border border-[var(--border)] p-3 text-xs">
      <summary className="cursor-pointer">盘后同步与来源审计（展开查看）</summary>
      <div className="mt-3"><StockEodStatus refreshKey={revision} /></div>
    </details>
    <section className="space-y-2 rounded border border-[var(--border)] p-3 text-xs" aria-label="行情筛选范围">
      {boards ? <p>证券目录 {boards.coverage.instruments} 只 · 行情覆盖 {boards.coverage.quoted} 只 · 行情交易日 {boards.as_of ?? "尚无日线"} · 来源 {boards.source} · {boards.price_adjustment === "none" ? "不复权" : boards.price_adjustment === "qfq" ? "前复权" : boards.price_adjustment}</p> : error ? <p role="alert" className="text-[var(--accent-danger)]">{error}，可刷新重试；股票列表单独查询。</p> : <p role="status">正在读取行业目录与行情覆盖…</p>}
      <label className="flex flex-wrap items-center gap-2">行业
        <select aria-label="筛选股票行业" value={industry} onChange={(event) => setIndustry(event.target.value)} className="rounded border border-[var(--border)] bg-[var(--bg-secondary)] p-2">
          <option value="">全部行业</option>
          {(boards?.boards ?? []).map((board) => <option key={board.industry} value={board.industry}>{board.industry}</option>)}
          {industry && !boards?.boards.some((board) => board.industry === industry) && <option value={industry}>{industry}（目录待核对）</option>}
        </select>
      </label>
      {boards?.has_more && <p role="status">行业选项仅显示前200个，未列出行业仍可在全部股票中按代码或名称搜索。</p>}
      <p className="text-[var(--text-muted)]">当前行业分类不等同历史成分，筛选结果不是回测股票池，也不代表全市场历史覆盖。</p>
    </section>
    {showAvailability && <StockDataAvailability refreshKey={revision} />}
    {advanced ? <StockScreenResults key={industry} industry={industry} refreshKey={revision} onSelect={setSelectedCode} /> : <StockMembers key={industry} industry={industry} refreshKey={revision} onSelect={setSelectedCode} />}
  </main>{selectedCode && <StockQuoteDetail key={selectedCode} code={selectedCode} onBack={() => setSelectedCode(null)} backLabel="← 返回行情筛选" />}</>
}
