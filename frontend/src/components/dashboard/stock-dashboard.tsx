"use client"

import { useEffect, useState } from "react"
import Link from "next/link"
import { StockMembers } from "@/components/market/stock-board-browser"
import { StockQuoteDetail } from "@/components/market/stock-quote-detail"
import { getStockBoardsApi, type StockBoardsResponse } from "@/lib/stock-market-api"
import { cn } from "@/lib/utils"
import { StockEodStatus } from "@/components/market/stock-eod-status"

const control = "rounded-md border border-[var(--border)] px-3 py-2 text-xs hover:bg-[var(--bg-tertiary)] disabled:opacity-40"
const panel = "min-w-0 rounded-xl border border-[var(--border)] bg-[var(--bg-secondary)] p-4"
const percentage = (value: number | null): string => value === null ? "—" : `${value > 0 ? "+" : ""}${value.toFixed(2)}%`

export function StockDashboard(): React.JSX.Element {
  const [result, setResult] = useState<StockBoardsResponse | null>(null)
  const [error, setError] = useState("")
  const [loading, setLoading] = useState(true)
  const [revision, setRevision] = useState(0)
  const [industry, setIndustry] = useState("")
  const [code, setCode] = useState<string | null>(null)
  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setError("")
    void getStockBoardsApi("", controller.signal)
      .then((value) => { if (!controller.signal.aborted) setResult(value) })
      .catch((failure: unknown) => { if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : "市场看板加载失败") })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [revision])
  const breadth = result?.boards.reduce((sum, board) => ({ advancing: sum.advancing + board.advancing, declining: sum.declining + board.declining, flat: sum.flat + board.flat }), { advancing: 0, declining: 0, flat: 0 })
  const sampleCount = breadth ? breadth.advancing + breadth.declining + breadth.flat : 0
  const adjustment = result?.price_adjustment === "none" ? "不复权" : result?.price_adjustment === "qfq" ? "前复权" : "复权口径见个股详情"
  const source = result?.source === "teajoin_eod" ? "Teajoin 盘后快照" : result?.source || "来源未提供"
  const rankedBoards = result?.boards.filter((board) => board.change_pct !== null).sort((left, right) => right.change_pct! - left.change_pct!).slice(0, 6) ?? []
  return <><div hidden={code !== null} inert={code !== null} className={cn("h-full min-w-0 flex-col gap-4 overflow-auto p-4 md:p-6", code ? "hidden" : "flex")}>
    <header className="flex flex-wrap items-start justify-between gap-3">
      <div><p className="text-[10px] tracking-[0.2em] text-[var(--primary)]">A-SHARE / MARKET OVERVIEW</p><h1 className="mt-1 text-xl font-semibold">A股市场看板</h1><p className="mt-1 text-xs text-[var(--text-muted)]">覆盖与交易日 → 样本涨跌 → 行业与个股 → K线指标</p></div>
      <div className="flex flex-wrap gap-2"><Link href="/market" className={control}>行情与板块</Link><Link href="/factor-research" className={control}>因子研究</Link><button className={control} disabled={loading} onClick={() => setRevision((value) => value + 1)}>{loading ? "刷新中…" : "刷新看板"}</button></div>
    </header>
    <StockEodStatus refreshKey={revision} />
    {loading && <p role="status" className="text-xs text-[var(--text-muted)]">加载市场覆盖与样本统计…</p>}
    {error && <div role="alert" className="rounded-lg border border-[var(--accent-danger)]/30 p-4 text-sm text-[var(--accent-danger)]">{error}<button className={`${control} ml-3`} onClick={() => setRevision((value) => value + 1)}>重试</button></div>}
    {result && !error && <>
      <section aria-label="市场覆盖" className="grid grid-cols-2 gap-3 lg:grid-cols-4">{[
        ["证券目录", `${result.coverage.instruments} 只`, "目录收录不代表已有日线"],
        ["当日行情覆盖", `${result.coverage.quoted} / ${result.coverage.instruments}`, "仅当前交易日有有效收盘价的证券"],
        ["可比日线样本", `${result.coverage.comparable} 只`, `前观测日 ${result.previous_date ?? "尚无"}`],
        ["行情交易日", result.as_of ?? "尚无行情", `${source} · ${adjustment}`],
      ].map(([label, value, note]) => <div key={label} className={panel}><p className="text-[11px] text-[var(--text-muted)]">{label}</p><strong className="mt-2 block break-words font-num text-xl">{value}</strong><p className="mt-2 text-[10px] leading-4 text-[var(--text-muted)]">{note}</p></div>)}</section>
      <section aria-label="盘后行情来源与缺口" className="space-y-2 rounded-lg border border-[var(--border)] p-3 text-xs text-[var(--text-secondary)]">
        <p>盘后来源：{source} · 行情交易日 {result.as_of ?? "尚无行情"} · 非实时盘口</p>
        <p className="flex flex-wrap gap-x-4 gap-y-1">{result.source_row_count !== undefined && <span>来源行数 {result.source_row_count}</span>}{result.missing_codes !== undefined && <span>目录缺行情 {result.missing_codes.length} 只</span>}{result.unknown_codes !== undefined && <span>目录未匹配 {result.unknown_codes.length} 只（来源行情代码）</span>}</p>
        {(result.missing_codes !== undefined || result.unknown_codes !== undefined || result.data_version || result.collected_at || result.reference_price || result.license_scope) && <details className="space-y-1 text-[11px] text-[var(--text-muted)]">
          <summary className="cursor-pointer">查看代码与快照追溯</summary>
          {result.missing_codes !== undefined && <p className="max-h-24 overflow-auto break-all">目录缺行情代码：{result.missing_codes.length ? result.missing_codes.join("、") : "本次快照无缺失"}</p>}
          {result.unknown_codes !== undefined && <p className="max-h-24 overflow-auto break-all">目录未匹配代码：{result.unknown_codes.length ? result.unknown_codes.join("、") : "本次快照无未匹配"}</p>}
          {result.data_version && <p title={result.data_version}>数据版本：<code>{result.data_version.slice(0, 12)}{result.data_version.length > 12 ? "…" : ""}</code></p>}
          {result.collected_at && <p>采集时间：<time dateTime={result.collected_at}>{result.collected_at}</time></p>}
          {result.reference_price && <p>涨跌参考价：{result.reference_price === "vendor_ex_rights_pre_close" ? "供应商除权参考前收盘价，不等同前一根日线收盘价" : result.reference_price}</p>}
          {result.license_scope && <p className="break-all">使用边界：{result.license_scope}</p>}
        </details>}
      </section>
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <section className={panel} aria-label="样本涨跌分布"><h2 className="text-sm font-semibold">可比样本涨跌</h2><p className="mt-1 text-[11px] leading-5 text-[var(--text-muted)]">已展示板块的 {sampleCount} 只可比样本{result.has_more ? " · 仅部分板块，未完整覆盖" : ""}，不代表全市场或官方指数。</p>
          <div role="img" aria-label={`样本涨${breadth?.advancing ?? 0} 跌${breadth?.declining ?? 0} 平${breadth?.flat ?? 0}`} className="my-4 flex h-3 overflow-hidden rounded-full bg-[var(--bg-tertiary)]"><span className="bg-[var(--accent-down)]" style={{ width: `${(breadth?.declining ?? 0) / Math.max(sampleCount, 1) * 100}%` }} /><span className="bg-[var(--text-muted)]/40" style={{ width: `${(breadth?.flat ?? 0) / Math.max(sampleCount, 1) * 100}%` }} /><span className="bg-[var(--accent-up)]" style={{ width: `${(breadth?.advancing ?? 0) / Math.max(sampleCount, 1) * 100}%` }} /></div>
          <div className="grid grid-cols-3 gap-2 text-center text-xs"><div className="rounded bg-[var(--bg-tertiary)] p-2 text-up">涨 <strong className="font-num">{breadth?.advancing ?? 0}</strong></div><div className="rounded bg-[var(--bg-tertiary)] p-2 text-down">跌 <strong className="font-num">{breadth?.declining ?? 0}</strong></div><div className="rounded bg-[var(--bg-tertiary)] p-2 text-[var(--text-muted)]">平 <strong className="font-num">{breadth?.flat ?? 0}</strong></div></div>
          {sampleCount === 0 && <p className="mt-3 text-xs text-[var(--text-muted)]">暂无可比行情数据，未以零涨跌替代缺价。</p>}
        </section>
        <section className={cn(panel, "xl:col-span-2")} aria-label="行业样本排行"><div className="flex items-center justify-between gap-2"><h2 className="text-sm font-semibold">行业样本涨幅排序</h2><Link href="/market" className="text-xs text-[var(--primary)]">查看全部板块 →</Link></div><p className="mt-1 text-[11px] text-[var(--text-muted)]">有可比样本的行业等权均值，最多展示6个；点击筛选下方股票。</p>
          <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3">{rankedBoards.map((board) => <button key={board.industry} className={cn("min-w-0 rounded-lg border border-[var(--border)] p-3 text-left hover:border-[var(--primary)]", industry === board.industry && "border-[var(--primary)] bg-[var(--primary)]/5")} onClick={() => setIndustry(board.industry)}><span className="block truncate text-xs font-medium">{board.industry}</span><strong className={cn("mt-1 block font-num text-lg", board.change_pct! > 0 ? "text-up" : board.change_pct! < 0 ? "text-down" : "text-[var(--text-muted)]")}>{percentage(board.change_pct)}</strong><span className="mt-1 block text-[10px] text-[var(--text-muted)]">样本 {board.comparable_count} / {board.count}</span></button>)}</div>
          {rankedBoards.length === 0 && <p className="py-6 text-sm text-[var(--text-muted)]">暂无可比板块行情，可在股票目录中继续浏览。</p>}
        </section>
      </div>
      <p className="rounded-lg border border-[var(--accent-warn)]/30 p-3 text-xs leading-5 text-[var(--text-secondary)]">盘后历史日线 · 非实时盘口。行业为当前分类；样本均值不是行业指数，缺价不代表停牌。K线指标仅依赖已载入数据，不足周期显示 —；前复权研究图表不能代替回放撮合数据。</p>
    </>}
    <section className={cn(panel, "flex min-h-[500px] flex-col gap-3")} aria-label="股票浏览与K线入口"><header className="flex items-center justify-between gap-2"><div><h2 className="text-sm font-semibold">{industry ? `${industry} · 成分股票` : "股票浏览"}</h2><p className="mt-1 text-[11px] text-[var(--text-muted)]">搜索股票，点击名称打开K线、估值与财务。</p></div>{industry && <button className={control} onClick={() => setIndustry("")}>全部股票</button>}</header><StockMembers key={industry} industry={industry} refreshKey={revision} onSelect={setCode} compact /></section>
  </div>{code && <StockQuoteDetail key={code} code={code} onBack={() => setCode(null)} backLabel="← 返回市场看板" />}</>
}
