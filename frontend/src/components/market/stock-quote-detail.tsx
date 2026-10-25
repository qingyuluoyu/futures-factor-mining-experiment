"use client"

import { useEffect, useState } from "react"
import { StockKlineChart } from "./stock-kline-chart"
import { StockFundamentalsHistory } from "./stock-fundamentals-history"
import { validateStockQuoteBars } from "./stock-quote-data"
import type { StockBar } from "@/lib/stock-api"
import { getStockKlineSources, getValidatedStockKlineApi, latestStockKlineSource, type StockKlineSource, type StockKlineSources, type ValidatedStockKlineResponse } from "@/lib/stock-kline-contract"
import { stockChangeBasisLabel, stockPriceChange } from "@/lib/stock-price-change"
import { getStockDetailsApi, type StockDetails } from "@/lib/stock-market-api"
import { useDisplayStore } from "@/stores/display"
import { cn } from "@/lib/utils"

const control = "rounded border border-[var(--border)] px-3 py-2 text-xs hover:bg-[var(--bg-tertiary)]"
const formatted = (value: number | null | undefined, suffix = ""): string => value == null ? "—" : `${value.toFixed(2)}${suffix}`

export function StockQuoteDetail({ code, onBack, backLabel = "← 返回板块 / 成分股" }: { code: string; onBack: () => void; backLabel?: string }): React.JSX.Element {
  const candleUp = useDisplayStore((state) => state.candleUp)
  const candleDown = useDisplayStore((state) => state.candleDown)
  const [details, setDetails] = useState<StockDetails | null>(null)
  const [bars, setBars] = useState<StockBar[]>([])
  const [hasMore, setHasMore] = useState(false)
  const [quoteMetadata, setQuoteMetadata] = useState<Pick<ValidatedStockKlineResponse, "price_adjustment" | "source" | "volume_unit" | "change_basis" | "coverage"> | null>(null)
  const [chartLoading, setChartLoading] = useState(true)
  const [detailLoading, setDetailLoading] = useState(true)
  const [chartError, setChartError] = useState("")
  const [detailError, setDetailError] = useState("")
  const [revision, setRevision] = useState(0)
  const [chartRevision, setChartRevision] = useState(0)
  const [detailRevision, setDetailRevision] = useState(0)
  const [tab, setTab] = useState<"chart" | "financial" | "coverage">("chart")
  const [sources, setSources] = useState<StockKlineSources | null>(null)
  const [sourceMode, setSourceMode] = useState<"loading" | "explicit" | "legacy" | "error">("loading")
  const [sourceChoice, setSourceChoice] = useState<{ code: string; source: StockKlineSource } | null>(null)
  const requestedSource = sourceChoice?.code === code ? sourceChoice.source : null
  useEffect(() => {
    const controller = new AbortController()
    setChartLoading(true)
    setBars([])
    setHasMore(false)
    setQuoteMetadata(null)
    setChartError("")
    setSourceMode("loading")
    void (async () => {
      let catalog: StockKlineSources
      try {
        catalog = await getStockKlineSources(code, controller.signal)
      } catch (error) {
        if (controller.signal.aborted) throw error
        if ([404, 405].includes((error as Error & { status?: number }).status ?? 0)) {
          setSources(null)
          setSourceMode("legacy")
          if (requestedSource) throw new Error("旧后端不支持来源目录，无法确认所选来源；可显式返回旧模式或重试")
          return getValidatedStockKlineApi(code, 1500, controller.signal)
        }
        setSources(null)
        setSourceMode("error")
        throw error
      }
      controller.signal.throwIfAborted()
      setSources(catalog)
      setSourceMode("explicit")
      const selected = requestedSource ?? latestStockKlineSource(catalog)
      if (!selected) throw new Error("当前没有开放且含本地日线的来源；未生成替代行情")
      if (!catalog.sources.find((item) => item.id === selected)?.available) throw new Error("所选来源当前不可用，未自动改读其他来源")
      return getValidatedStockKlineApi(code, 1500, controller.signal, selected)
    })()
      .then((value) => { if (!controller.signal.aborted) { setBars(validateStockQuoteBars(value.bars)); setHasMore(value.has_more); setQuoteMetadata({ price_adjustment: value.price_adjustment, source: value.source, volume_unit: value.volume_unit, change_basis: value.change_basis, coverage: value.coverage }) } })
      .catch((failure: unknown) => { if (!controller.signal.aborted) setChartError(failure instanceof Error ? failure.message : "日线加载失败") })
      .finally(() => { if (!controller.signal.aborted) setChartLoading(false) })
    return () => controller.abort()
  }, [code, revision, chartRevision, requestedSource])
  useEffect(() => {
    const controller = new AbortController()
    setDetailLoading(true)
    setDetails(null)
    setDetailError("")
    void getStockDetailsApi(code, controller.signal)
      .then((value) => { if (!controller.signal.aborted) setDetails(value) })
      .catch((failure: unknown) => { if (!controller.signal.aborted) setDetailError(failure instanceof Error ? failure.message : "详情加载失败") })
      .finally(() => { if (!controller.signal.aborted) setDetailLoading(false) })
    return () => controller.abort()
  }, [code, revision, detailRevision])
  const last = bars.at(-1)
  const previous = bars.at(-2)
  const changeInfo = stockPriceChange(last, previous, quoteMetadata?.change_basis ?? "unavailable")
  const change = changeInfo?.change ?? null
  const changePct = changeInfo?.changePct ?? null
  const valueClass = change === null || change === 0 ? "text-[var(--text-muted)]" : change > 0 ? "text-up" : "text-down"
  const priceAdjustmentLabel = quoteMetadata === null ? "等待数据口径" : quoteMetadata.price_adjustment === "none" ? "不复权" : "前复权"
  const sourceLabel = quoteMetadata === null ? "等待数据来源" : quoteMetadata.source === "teajoin_eod" ? "Teajoin 盘后快照" : "本地导入历史日线"
  return <div className="flex h-full min-h-0 flex-col overflow-y-auto [&>*]:shrink-0">
    <header className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--border)] p-4">
      <div className="flex min-w-0 flex-wrap items-center gap-3"><button className={control} onClick={onBack}>{backLabel}</button><div><h1 className="text-base font-semibold">{details?.instrument.name ?? code} <span className="font-num text-xs text-[var(--text-muted)]">{details?.instrument.instrument_id ?? code}</span></h1><p className="mt-1 text-xs text-[var(--text-muted)]">{details?.instrument.industry} · {priceAdjustmentLabel}日线 · {sourceLabel} · 非实时行情</p><p className="mt-1 text-[10px] text-[var(--text-muted)]">{stockChangeBasisLabel(quoteMetadata?.change_basis ?? "unavailable")}</p></div></div>
      <div className={cn("flex items-baseline gap-2 font-num", valueClass)}><strong className="text-2xl">{formatted(last?.close)}</strong><span className="text-xs">{changePct !== null ? `${changePct > 0 ? "+" : ""}${changePct.toFixed(2)}%` : "—"}</span><span className="text-[10px] text-[var(--text-muted)]">{last?.time ?? "暂无日线"}</span></div>
    </header>
    <nav aria-label="股票详情视图" className="flex flex-wrap gap-2 border-b border-[var(--border)] px-4 py-2">{([["chart", "K线图表"], ["financial", "估值与财务"], ["coverage", "数据覆盖与口径"]] as const).map(([value, label]) => <button key={value} aria-pressed={tab === value} className={cn(control, tab === value && "border-[var(--primary)] bg-[var(--primary)]/10 text-[var(--primary)]")} onClick={() => setTab(value)}>{label}</button>)}<button className={`${control} ml-auto`} onClick={() => setRevision((value) => value + 1)}>重新加载</button></nav>
    <section aria-label="日线来源选择" className="flex flex-wrap items-center gap-2 border-b border-[var(--border)] px-4 py-2 text-xs text-[var(--text-muted)]">
      {sourceMode === "loading" && <span role="status">读取本地来源目录…</span>}
      {sources?.code === code && <label>日线来源 <select aria-label="日线数据来源" className={control} value={requestedSource ?? latestStockKlineSource(sources) ?? ""} onChange={(event) => {
        const selected = sources.sources.find((item) => item.id === event.target.value)
        if (!selected?.available) return
        setBars([]); setHasMore(false); setQuoteMetadata(null); setChartLoading(true); setChartError("")
        setSourceChoice({ code, source: selected.id })
      }}>
        {!latestStockKlineSource(sources) && <option value="">无可用本地来源</option>}
        {sources.sources.map((item) => <option key={item.id} value={item.id} disabled={!item.available}>{item.id === "eod_raw" ? "盘后原价" : "历史前复权"} · {item.last_date ?? (item.reason === "eod_disabled" ? "未开放" : "无本地数据")} · {item.available_bars ?? "未知"}根</option>)}
      </select></label>}
      {sourceMode === "explicit" && <span>首次按最近可用日期选源；切源独立读取，不拼接复权与原价。</span>}
      {sourceMode === "legacy" && <span role="status">旧模式：后端没有来源目录，沿用历史优先读取；无法显式选择来源。</span>}
      {sourceMode === "legacy" && requestedSource && <button className={control} onClick={() => setSourceChoice(null)}>使用旧模式</button>}
      {(sourceMode === "legacy" || sourceMode === "error") && <button className={control} onClick={() => setChartRevision((value) => value + 1)}>重试来源目录</button>}
    </section>
    {quoteMetadata?.coverage && tab !== "financial" && <div aria-label="本次日线来源覆盖" className="space-y-1 border-b border-[var(--border)] bg-[var(--bg-secondary)] px-4 py-2 text-xs leading-relaxed text-[var(--text-muted)]">
      <p>本地所选源共 {quoteMetadata.coverage.available_bars} 根 · {quoteMetadata.coverage.first_date ?? "无日期"} 至 {quoteMetadata.coverage.last_date ?? "无日期"}；已载入 {bars.length} 根 · 历史完整性未核验。</p>
      <p>{quoteMetadata.coverage.window_status === "window_limited" ? "当前读取窗口有上限，另有更早本地数据。" : quoteMetadata.coverage.window_status === "local_history_exhausted" ? "已读完当前本地来源，不代表供应商没有更早历史。" : "当前所选本地来源尚无日线，不代表股票停牌或供应商没有历史。"} 请求 {quoteMetadata.coverage.requested_limit} 根，本次读取上限 {quoteMetadata.coverage.effective_limit} 根。</p>
    </div>}
    <>
      {detailLoading && <p role="status" className="px-4 py-2 text-xs text-[var(--text-muted)]">目录与财务加载中，K线独立读取…</p>}
      {detailError && <div role="alert" className="m-4 rounded border border-[var(--accent-danger)]/30 p-3 text-sm text-[var(--accent-danger)]">{detailError}<button className={`${control} ml-3`} onClick={() => setDetailRevision((value) => value + 1)}>重试估值与财务</button></div>}
      {tab === "chart" && <div className="min-w-0 shrink-0">{chartLoading ? <p role="status" className="p-8 text-sm text-[var(--text-muted)]">加载K线数据…</p> : chartError ? <div role="alert" className="p-8 text-sm text-[var(--accent-danger)]">{chartError}<button className={`${control} ml-3`} onClick={() => setChartRevision((value) => value + 1)}>重试K线</button></div> : bars.length > 0 ? <StockKlineChart bars={bars} upColor={candleUp} downColor={candleDown} symbolLabel={code} hasMore={hasMore} priceAdjustmentLabel={priceAdjustmentLabel} volumeUnit="手" changeBasis={quoteMetadata?.change_basis ?? "unavailable"} /> : <div className="m-6 rounded-xl border border-dashed border-[var(--border)] p-8"><h2 className="text-base font-semibold">证券已收录，尚未导入日线</h2><p className="mt-2 text-sm text-[var(--text-muted)]">可以查看目录与数据覆盖。本页不生成替代行情；并非股票停牌。模拟回放还需要单独的未复权开盘价及复权因子数据。</p></div>}</div>}
      {tab === "financial" && <div className="space-y-6 p-4 md:p-6">
        <StockFundamentalsHistory code={code} />
        {details && <>
        <section><h2 className="mb-3 text-sm font-semibold">每日估值 <span className="ml-2 text-xs font-normal text-[var(--text-muted)]">数据日 {details?.valuation?.trade_date ?? "未覆盖"}</span></h2>{details?.valuation ? <dl className="grid grid-cols-2 gap-3 lg:grid-cols-5">{[["PE (TTM)", formatted(details.valuation.pe_ttm)], ["市净率 PB", formatted(details.valuation.pb)], ["总市值", formatted(details.valuation.total_mv, " 亿元")], ["流通市值", formatted(details.valuation.circ_mv, " 亿元")], ["换手率", formatted(details.valuation.turnover_rate, "%")]].map(([label, value]) => <div key={label} className="rounded-lg border border-[var(--border)] bg-[var(--bg-secondary)] p-4"><dt className="text-xs text-[var(--text-muted)]">{label}</dt><dd className="mt-2 font-num text-lg">{value}</dd></div>)}</dl> : <p className="text-sm text-[var(--text-muted)]">该证券暂无估值覆盖，未以零值代替。</p>}</section>
        <section><h2 className="mb-2 text-sm font-semibold">已披露财务（最多8期）</h2><p className="mb-3 text-xs text-[var(--text-muted)]">仅公告日和报告期不晚于 {details?.as_of ?? "当前日期"} 的记录；缺公告日不展示，不证明历史修订时点完整。</p><div className="overflow-auto rounded-lg border border-[var(--border)]"><table className="w-full whitespace-nowrap text-right text-xs"><thead className="bg-[var(--bg-secondary)] text-[var(--text-muted)]"><tr>{["报告期", "公告日期", "ROE", "毛利率", "营收同比", "净利润同比", "EPS (元)", "BPS (元)"].map((label) => <th key={label} className="px-3 py-3">{label}</th>)}</tr></thead><tbody>{details?.financials.map((row) => <tr key={`${row.report_date}:${row.publish_date}`} className="border-t border-[var(--border)] font-num">{[row.report_date, row.publish_date, formatted(row.roe, "%"), formatted(row.gross_margin, "%"), formatted(row.revenue_yoy, "%"), formatted(row.netprofit_yoy, "%"), formatted(row.eps), formatted(row.bps)].map((value, index) => <td key={index} className="px-3 py-3">{value}</td>)}</tr>)}</tbody></table></div>{!details?.financials.length && <p className="mt-4 text-sm text-[var(--text-muted)]">暂无符合披露日期要求的财务记录。</p>}</section>
        </>}
      </div>}
      {tab === "coverage" && <div className="space-y-4 p-6 text-sm"><h2 className="font-semibold">真实覆盖范围</h2><dl className="grid grid-cols-1 gap-3 sm:grid-cols-2"><div>标准证券：{details?.instrument.instrument_id ?? code}</div><div>上市日：{details?.instrument.list_date ?? "未提供"}</div><div>已载入{priceAdjustmentLabel}日线：{bars.length} 根{hasMore ? "（另有更早数据）" : ""}</div><div>已载入区间：{bars[0]?.time ?? "无"} — {last?.time ?? "无"}</div><div>本地历史库覆盖：{details?.coverage.daily_rows ?? "—"} 条</div><div>本地历史库区间：{details?.coverage.first_date ?? "无"} — {details?.coverage.last_date ?? "无"}</div><div>图表来源：{sourceLabel}</div></dl><p className="text-xs leading-6 text-[var(--text-muted)]">目录使用本地已导入分类；图表来源与复权口径以本次日线响应为准。盘后快照不代表实时盘口或完整历史，历史库覆盖与已载入图表可能不同。时区 Asia/Shanghai；价格人民币元，成交量手（100股），市值亿元。前复权图表不能代替模拟盘未复权撮合数据；日线快照不证明复权因子或回放数据已完整。</p><ul className="list-inside list-disc space-y-2 text-xs text-[var(--text-muted)]">{details?.limitations.map((item) => <li key={item}>{item}</li>)}</ul></div>}
    </>
  </div>
}
