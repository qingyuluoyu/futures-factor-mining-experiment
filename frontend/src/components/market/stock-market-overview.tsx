"use client"

import { useEffect, useState } from "react"
import { getStockBoardsApi, type StockBoardsResponse } from "@/lib/stock-market-api"
import { StockEodStatus } from "@/components/market/stock-eod-status"

/**
 * 股票行情总览的共享状态：板块目录、读取态、刷新令牌与当前行业。
 * 由 StockMarketOverview 独占持有，三个股票工作区（工作台 / 行情 / 行情筛选）共用，
 * 各页只保留自己的行业 UI（看板按钮 / 看板网格 / 下拉筛选）而不再各自拉取。
 */
export type StockMarketOverview = {
  boards: StockBoardsResponse | null
  error: string
  loading: boolean
  revision: number
  refresh: () => void
  industry: string
  onIndustryChange: (industry: string) => void
}

const control = "rounded-md border border-[var(--border)] px-3 py-2 text-xs hover:bg-[var(--bg-tertiary)] disabled:opacity-40"
const panel = "min-w-0 rounded-xl border border-[var(--border)] bg-[var(--bg-secondary)] p-4"
const adjustmentLabel = (value: string | undefined): string => value === "none" ? "不复权" : value === "qfq" ? "前复权" : "复权口径见个股详情"
const sourceLabel = (value: string | undefined): string => value === "teajoin_eod" ? "Teajoin 盘后快照" : value || "来源未提供"

/**
 * A股行情总览：独占一次 getStockBoardsApi 拉取、一个 revision、一个盘后同步状态、
 * 覆盖摘要卡与「盘后行情来源与缺口」审计块。页面通过 children 渲染函数取回共享状态，
 * 因此同一目录只会被读取一次，审计与覆盖呈现也只有一份。
 */
export function StockMarketOverview({
  industry: controlledIndustry,
  onIndustryChange,
  refreshKey = 0,
  collapsible = false,
  presentation = "full",
  suppressContext = false,
  children,
}: {
  industry?: string
  onIndustryChange?: (industry: string) => void
  refreshKey?: number
  collapsible?: boolean
  presentation?: "full" | "compact"
  suppressContext?: boolean
  children?: (overview: StockMarketOverview) => React.ReactNode
}): React.JSX.Element {
  const [boards, setBoards] = useState<StockBoardsResponse | null>(null)
  const [error, setError] = useState("")
  const [loading, setLoading] = useState(true)
  const [attempt, setAttempt] = useState(0)
  const [ownedIndustry, setOwnedIndustry] = useState("")
  const revision = refreshKey + attempt
  const industry = controlledIndustry ?? ownedIndustry
  const changeIndustry = (value: string) => {
    if (controlledIndustry === undefined) setOwnedIndustry(value)
    onIndustryChange?.(value)
  }
  useEffect(() => {
    const controller = new AbortController()
    setError("")
    setBoards(null)
    setLoading(true)
    void getStockBoardsApi("", controller.signal)
      .then((value) => { if (!controller.signal.aborted) { setBoards(value); setLoading(false) } })
      .catch((failure: unknown) => { if (!controller.signal.aborted) { setError(failure instanceof Error ? failure.message : "板块与行情覆盖读取失败"); setLoading(false) } })
    return () => controller.abort()
  }, [revision])
  const refresh = () => setAttempt((value) => value + 1)
  const overview: StockMarketOverview = { boards, error, loading, revision, refresh, industry, onIndustryChange: changeIndustry }
  const status = <StockEodStatus refreshKey={revision} />
  const coverage = boards && !error && <>
      <section aria-label="市场覆盖" className="grid grid-cols-2 gap-3 lg:grid-cols-4">{[
        ["证券目录", `${boards.coverage.instruments} 只`, "目录收录不代表已有日线"],
        ["当日行情覆盖", `${boards.coverage.quoted} / ${boards.coverage.instruments}`, "仅当前交易日有有效收盘价的证券"],
        ["可比日线样本", `${boards.coverage.comparable} 只`, `前观测日 ${boards.previous_date ?? "尚无"}`],
        ["行情交易日", boards.as_of ?? "尚无行情", `${sourceLabel(boards.source)} · ${adjustmentLabel(boards.price_adjustment)}`],
      ].map(([label, value, note]) => <div key={label} className={panel}><p className="text-[11px] text-[var(--text-muted)]">{label}</p><strong className="mt-2 block break-words font-num text-xl">{value}</strong><p className="mt-2 text-[10px] leading-4 text-[var(--text-muted)]">{note}</p></div>)}</section>
      <section aria-label="盘后行情来源与缺口" className="space-y-2 rounded-lg border border-[var(--border)] p-3 text-xs text-[var(--text-secondary)]">
        <p>盘后来源：{sourceLabel(boards.source)} · 行情交易日 {boards.as_of ?? "尚无行情"} · 非实时盘口{boards.price_adjustment === "none" ? " · 不复权" : boards.price_adjustment === "qfq" ? " · 前复权" : ""}</p>
        <p className="flex flex-wrap gap-x-4 gap-y-1">{boards.source_row_count !== undefined && <span>来源行数 {boards.source_row_count}</span>}{boards.missing_codes !== undefined && <span>目录缺行情 {boards.missing_codes.length} 只</span>}{boards.unknown_codes !== undefined && <span>目录未匹配 {boards.unknown_codes.length} 只（来源行情代码）</span>}</p>
        {(boards.missing_codes !== undefined || boards.unknown_codes !== undefined || boards.data_version || boards.collected_at || boards.reference_price || boards.license_scope) && <details className="space-y-1 text-[11px] text-[var(--text-muted)]">
          <summary className="cursor-pointer">查看代码与快照追溯</summary>
          {boards.missing_codes !== undefined && <p className="max-h-24 overflow-auto break-all">目录缺行情代码：{boards.missing_codes.length ? boards.missing_codes.join("、") : "本次快照无缺失"}</p>}
          {boards.unknown_codes !== undefined && <p className="max-h-24 overflow-auto break-all">目录未匹配代码：{boards.unknown_codes.length ? boards.unknown_codes.join("、") : "本次快照无未匹配"}</p>}
          {boards.data_version && <p title={boards.data_version}>数据版本：<code>{boards.data_version.slice(0, 12)}{boards.data_version.length > 12 ? "…" : ""}</code></p>}
          {boards.collected_at && <p>采集时间：<time dateTime={boards.collected_at}>{boards.collected_at}</time></p>}
          {boards.reference_price && <p>涨跌参考价：{boards.reference_price === "vendor_ex_rights_pre_close" ? "供应商除权参考前收盘价，不等同前一根日线收盘价" : boards.reference_price}</p>}
          {boards.license_scope && <p className="break-all">使用边界：{boards.license_scope}</p>}
        </details>}
      </section>
    </>
  return <div className="flex h-full min-h-0 min-w-0 flex-col overflow-y-auto">
    <div aria-label="行情数据上下文" hidden={suppressContext} inert={suppressContext} className="shrink-0 space-y-3 p-3 md:p-4">
      {loading && <p role="status" className="text-xs text-[var(--text-muted)]">加载板块与行情覆盖…</p>}
      {error && <div role="alert" className="rounded-lg border border-[var(--accent-danger)]/30 p-4 text-sm text-[var(--accent-danger)]">{error}<button className={`${control} ml-3`} onClick={refresh}>重试</button></div>}
      {presentation === "compact" ? <>
        {boards && !error && <section aria-label="行情简要上下文" className="flex flex-wrap gap-x-4 gap-y-1 text-xs leading-5 text-[var(--text-muted)]">
          <span>行情日 {boards.as_of ?? "尚无行情"}</span><span>{sourceLabel(boards.source)} · {adjustmentLabel(boards.price_adjustment)}</span><span>当日覆盖 {boards.coverage.quoted} / {boards.coverage.instruments} · 目录不等于历史完整 · 非实时盘口</span>
        </section>}
        <details aria-label="行情数据与同步详情" className="rounded-md border border-[var(--border)] p-3 text-xs text-[var(--text-secondary)]">
          <summary className="cursor-pointer">市场覆盖、来源追溯与盘后同步（展开查看）</summary>
          <div className="mt-3 space-y-3">{coverage}<div><p className="mb-2">盘后同步与影子归档状态</p>{status}</div></div>
        </details>
      </> : <>
        {coverage}
        {collapsible ? <details className="rounded-md border border-[var(--border)] p-3 text-xs text-[var(--text-secondary)]"><summary className="cursor-pointer">盘后同步与影子归档状态（展开查看）</summary><div className="mt-3">{status}</div></details> : status}
      </>}
    </div>
    <div data-market-content className={suppressContext ? "min-h-0 min-w-0 flex-1" : "min-w-0 shrink-0"}>{children?.(overview)}</div>
  </div>
}
