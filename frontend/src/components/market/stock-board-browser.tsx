"use client"

import { useEffect, useState } from "react"
import { ArrowLeft, ArrowRight, RefreshCw, Search } from "lucide-react"
import { getStockMembersApi, type StockBoardsResponse, type StockMembersResponse } from "@/lib/stock-market-api"
import { cn } from "@/lib/utils"
import { selectStockBoards, type StockBoardSelection } from "./stock-board-order"

const control = "rounded-md border border-[var(--border)] bg-[var(--bg-secondary)] px-3 py-2 text-xs text-[var(--text-primary)] disabled:opacity-40"
const movementClass = (value: number | null): string => value === null || value === 0 ? "text-[var(--text-muted)]" : value > 0 ? "text-up" : "text-down"
const percentage = (value: number | null): string => value === null ? "—" : `${value > 0 ? "+" : ""}${value.toFixed(2)}%`

export function StockMembers({ industry = "", activeCode = "", onSelect, compact = false, refreshKey = 0 }: {
  industry?: string
  activeCode?: string
  onSelect: (code: string) => void
  compact?: boolean
  refreshKey?: number
}): React.JSX.Element {
  const [keyword, setKeyword] = useState("")
  const [sort, setSort] = useState<"code" | "name" | "change_pct">("code")
  const [direction, setDirection] = useState<"asc" | "desc">("asc")
  const [dataOnly, setDataOnly] = useState(false)
  const [offset, setOffset] = useState(0)
  const [result, setResult] = useState<StockMembersResponse | null>(null)
  const [error, setError] = useState("")
  const [loading, setLoading] = useState(true)
  const [revision, setRevision] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setError("")
    setResult(null)
    const timer = setTimeout(() => {
      void getStockMembersApi({ industry, keyword: keyword.trim(), sort, direction, dataOnly, offset }, controller.signal)
        .then((value) => { if (!controller.signal.aborted) setResult(value) })
        .catch((failure: unknown) => { if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : "成分股加载失败") })
        .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    }, 200)
    return () => { clearTimeout(timer); controller.abort() }
  }, [industry, keyword, sort, direction, dataOnly, offset, revision, refreshKey])
  return <div className="flex min-w-0 shrink-0 flex-col gap-3">
    <div className="flex flex-wrap items-center gap-2">
      <label className="flex min-w-40 flex-1 items-center gap-2 rounded-md border border-[var(--border)] px-3 py-2"><Search size={14} aria-hidden /><input aria-label="搜索股票代码或名称" maxLength={64} value={keyword} onChange={(event) => { setKeyword(event.target.value); setOffset(0) }} placeholder="股票代码 / 名称" className="w-full bg-transparent text-xs outline-none" /></label>
      <select aria-label="股票排序" value={sort} onChange={(event) => { setSort(event.target.value as typeof sort); setOffset(0) }} className={control}><option value="code">代码</option><option value="name">名称</option><option value="change_pct">涨跌幅</option></select>
      <button className={control} onClick={() => { setDirection(direction === "asc" ? "desc" : "asc"); setOffset(0) }}>{direction === "asc" ? "升序 ↑" : "降序 ↓"}</button>
      <label className="flex items-center gap-2 text-xs text-[var(--text-secondary)]"><input type="checkbox" checked={dataOnly} onChange={(event) => { setDataOnly(event.target.checked); setOffset(0) }} />仅有当日日线</label>
    </div>
    {error ? <div role="alert" className="rounded-md border border-[var(--accent-danger)]/30 p-4 text-sm text-[var(--accent-danger)]">{error}<button className={`${control} ml-3`} onClick={() => setRevision((value) => value + 1)}>重试</button></div> : loading ? <p role="status" className="p-5 text-sm text-[var(--text-muted)]">加载成分股…</p> : result && <>
      <p className="text-xs text-[var(--text-muted)]">{industry || "全部股票"} · 匹配 {result.total} 只 · 行情日 {result.as_of ?? "尚无行情"}；目录收录不代表已有日线，缺价不代表停牌。</p>
      <div className="shrink-0 overflow-x-auto rounded-lg border border-[var(--border)]">
        <table className={cn("w-full whitespace-nowrap text-left text-xs", compact ? "min-w-[32rem]" : "min-w-[40rem]")}><thead className="sticky top-0 z-10 bg-[var(--bg-secondary)] text-[var(--text-muted)]"><tr><th className="px-3 py-3">股票 / 标准代码</th>{!compact && <th className="px-3">行业</th>}<th className="px-3 text-right">收盘价</th><th className="px-3 text-right">涨跌幅</th><th className="px-3">数据</th></tr></thead>
          <tbody>{result.items.map((item) => <tr key={item.code} className={cn("border-t border-[var(--border)] hover:bg-[var(--bg-tertiary)]", activeCode === item.code && "bg-[var(--primary)]/10")}>
            <td className="px-3 py-1"><button onClick={() => onSelect(item.code)} className="w-full py-2 text-left hover:text-[var(--primary)]"><span className="block font-medium">{item.name}</span><span className="font-num text-[10px] text-[var(--text-muted)]">{item.instrument_id}</span></button></td>
            {!compact && <td className="px-3 text-[var(--text-muted)]">{item.industry}</td>}
            <td className="px-3 text-right font-num">{item.close?.toFixed(2) ?? "—"}</td><td className={cn("px-3 text-right font-num", movementClass(item.change_pct))}>{percentage(item.change_pct)}</td>
            <td className="px-3 text-[var(--text-muted)]">{item.data_status === "ready" ? "日线" : item.data_status === "missing_previous" ? "缺前日价" : item.data_status === "invalid_quote" ? "行情异常" : item.data_status === "stale_quote" ? `缺当日价 · ${item.last_trade_date ?? "有历史"}` : "仅目录"}</td>
          </tr>)}</tbody></table>
        {result.items.length === 0 && <div className="space-y-3 p-8 text-center text-sm text-[var(--text-muted)]">{offset > 0 ? <><p>当前页没有股票，结果可能已变化。可返回第一页或调整关键词、数据筛选。</p><button aria-label="返回第一页股票" className={control} onClick={() => setOffset(0)}>返回第一页</button></> : <p>没有符合条件的股票，请调整关键词或数据筛选。</p>}</div>}
      </div>
      <div className="flex items-center justify-between text-xs text-[var(--text-muted)]"><span>{result.items.length === 0 ? "0" : `${offset + 1}–${offset + result.items.length}`} / {result.total}</span><div className="flex gap-2"><button aria-label="上一页股票" className={control} disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}><ArrowLeft size={14} /></button><button aria-label="下一页股票" className={control} disabled={!result.has_more || offset + 50 > 10000} onClick={() => setOffset(Math.min(10000, offset + 50))}><ArrowRight size={14} /></button></div></div>
      {result.has_more && offset + 50 > 10000 && <p role="status" className="text-xs text-[var(--accent-warn)]">已到分页上限，请使用行业、关键词或日线筛选缩小结果；当前列表未完整展示。</p>}
    </>}
  </div>
}

/**
 * 行情看板网格：只负责行业浏览与成分股，板块目录、盘后同步状态、覆盖摘要与来源审计
 * 由 StockMarketOverview 独占提供（本组件不再自行读取，也不复制审计/覆盖块）。
 */
export function StockBoardBrowser({ boards, error, loading, revision, refresh, industry, onIndustryChange, onSelect }: {
  boards: StockBoardsResponse | null
  error: string
  loading: boolean
  revision: number
  refresh: () => void
  industry: string
  onIndustryChange: (industry: string) => void
  onSelect: (code: string) => void
}): React.JSX.Element {
  const [drilled, setDrilled] = useState(false)
  // A nonempty parent/URL selection is already a member view; only "all stocks" is local state.
  const showMembers = Boolean(industry) || drilled
  useEffect(() => { if (industry) setDrilled(false) }, [industry])
  const [keyword, setKeyword] = useState("")
  const [sort, setSort] = useState<NonNullable<StockBoardSelection["sort"]>>("count")
  const [direction, setDirection] = useState<"asc" | "desc">("desc")
  const [comparableOnly, setComparableOnly] = useState(false)
  const visibleBoards = selectStockBoards(boards?.boards ?? [], { keyword, sort, direction, comparableOnly })
  return <div className="flex min-w-0 shrink-0 flex-col gap-3 p-3 md:p-4">
    <header className="flex flex-wrap items-start justify-between gap-3"><div><div className="mb-1 text-[10px] tracking-[0.2em] text-[var(--primary)]">A-SHARE / INDUSTRIES</div><h1 className="text-xl font-semibold">{showMembers ? industry || "全部股票" : "行业板块"}</h1><p className="mt-1 text-xs text-[var(--text-muted)]">先选板块，再浏览成分股和个股图表。历史日线 · 非实时盘口</p></div><div className="flex gap-2">{showMembers && <button className={control} onClick={() => { setDrilled(false); onIndustryChange("") }}>← 板块总览</button>}<button className={control} onClick={() => { setDrilled(true); onIndustryChange("") }}>全部股票</button><button aria-label="刷新板块" className={control} onClick={refresh}><RefreshCw size={14} /></button></div></header>
    <p className="rounded-md border border-[var(--accent-warn)]/30 bg-[var(--accent-warn)]/5 p-3 text-xs leading-relaxed text-[var(--text-secondary)]">板块涨跌 = 有可比日线样本的等权平均，不是官方行业指数，也不代表全板块涨跌。行业使用当前 stock_basic 分类，不能用于历史成分回测。未覆盖股票只展示真实目录，不填假行情。</p>
    {showMembers ? <StockMembers key={industry} industry={industry} onSelect={onSelect} refreshKey={revision} /> : <>
      <label className="flex items-center gap-2 rounded-md border border-[var(--border)] px-3 py-2"><Search size={15} /><input aria-label="搜索行业板块" maxLength={64} placeholder="搜索行业板块，例如：银行、半导体、软件服务" value={keyword} onChange={(event) => setKeyword(event.target.value)} className="w-full bg-transparent text-sm outline-none" /></label>
      <div className="flex flex-wrap items-center gap-3 text-xs">
        <label>板块排序 <select aria-label="板块排序" value={sort} className={control} onChange={(event) => { const value = event.target.value as typeof sort; setSort(value); setDirection(value === "name" ? "asc" : "desc") }}><option value="count">证券数量</option><option value="change_pct">样本涨跌幅</option><option value="name">行业名称</option></select></label>
        <button aria-label="板块排序方向" className={control} onClick={() => setDirection((value) => value === "asc" ? "desc" : "asc")}>{direction === "desc" ? "降序 ↓" : "升序 ↑"}</button>
        <label className="flex items-center gap-2"><input type="checkbox" aria-label="仅显示可比板块" checked={comparableOnly} onChange={(event) => setComparableOnly(event.target.checked)} />仅显示可比板块</label>
        {boards && <span className="text-[var(--text-muted)]">显示 {visibleBoards.length} / {boards.boards.length} 个板块 · 缺涨跌数据始终排后，不补零</span>}
      </div>
      {error ? <div role="alert" className="text-sm text-[var(--accent-danger)]">行业板块未载入，请重试或使用股票搜索。</div> : !boards ? <p role="status" className="text-sm text-[var(--text-muted)]">加载板块…</p> : <>
        <div className="grid min-w-0 grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">{visibleBoards.map((item) => <button key={item.industry} data-stock-board={item.industry} onClick={() => { onIndustryChange(item.industry); setDrilled(false) }} className="min-w-0 rounded-lg border border-[var(--border)] bg-[var(--bg-secondary)] p-2.5 text-left transition-colors hover:border-[var(--primary)]"><div className="flex min-w-0 items-center justify-between gap-1"><h2 className="truncate text-xs font-semibold" title={item.industry}>{item.industry}</h2><ArrowRight size={12} className="shrink-0 text-[var(--text-muted)]" /></div><div className="my-2 flex flex-wrap items-baseline justify-between gap-1"><span className={cn("font-num text-lg font-semibold", movementClass(item.change_pct))}>{percentage(item.change_pct)}</span><span className="text-[10px] text-[var(--text-muted)]">{item.count} 只</span></div><p className="text-[10px] leading-4 text-[var(--text-muted)]">可比样本 {item.comparable_count} / {item.count}</p><p className="text-[10px] leading-4 text-[var(--text-muted)]">涨 {item.advancing} 跌 {item.declining} 平 {item.flat}</p><p className="mt-1.5 truncate text-[10px] text-[var(--text-secondary)]" title={item.leader?.name}>{item.leader ? `样本领涨 ${item.leader.name} ${percentage(item.leader.change_pct)}` : "尚无可比日线 · 可浏览目录"}</p></button>)}</div>
        {visibleBoards.length === 0 && <p className="p-8 text-center text-sm text-[var(--text-muted)]">没有匹配的行业板块。</p>}
        {boards.has_more && <p role="alert" className="text-xs text-[var(--accent-warn)]">板块超过当前200个展示上限，请使用股票搜索；未将列表宣称为完整。</p>}
      </>}
    </>}
  </div>
}
