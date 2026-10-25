"use client"

import { useEffect, useMemo, useState } from "react"
import dynamic from "next/dynamic"
import { Crosshair, Info, Maximize2, Minimize2, RotateCcw, ZoomIn, ZoomOut } from "lucide-react"
import type { StockBar } from "@/lib/stock-api"
import { formatStockVolume as formatVolume, stockChangeBasisLabel, stockPriceChange, STOCK_PRICE_CHANGE_METHOD_VERSION, type StockChangeBasis } from "@/lib/stock-price-change"
import { cn } from "@/lib/utils"
import { useStockMarketStore, type StockChartPreferences } from "@/stores/stock-market"
import { aggregateStockBars, prepareStockBars, stockBOLL, stockSMA,
  STOCK_CHART_METHOD_DESCRIPTION, STOCK_CHART_METHOD_VERSION, type StockChartPeriod } from "./stock-chart-data"
import { toTickChartData } from "./tick/adapter"
import { StockChartBoundary, StockChartLoading } from "./stock-chart-boundary"

const AVERAGES = [
  { period: 5, color: "#a1a1aa" },
  { period: 10, color: "#3b82f6" },
  { period: 20, color: "#f97316" },
  { period: 60, color: "#8b5cf6" },
]

function chartPillClass(active: boolean): string {
  return cn("rounded px-2 py-0.5 font-num text-[10px] font-medium transition-colors cursor-pointer disabled:cursor-not-allowed disabled:opacity-40",
    active ? "bg-[var(--primary)]/15 text-[var(--primary)]" : "bg-[var(--bg-tertiary)] text-[var(--text-muted)] hover:text-[var(--text-secondary)]")
}

function formatValue(value: number | undefined, digits = 2): string {
  return value == null ? "—" : value.toFixed(digits)
}

export function StockKlineChart({ bars, upColor, downColor, height, symbolLabel, hasMore = false, priceAdjustmentLabel = "前复权", volumeUnit = "手", changeBasis = "unavailable" }: {
  bars: StockBar[]
  upColor: string
  downColor: string
  height?: number
  symbolLabel?: string
  hasMore?: boolean
  priceAdjustmentLabel?: string
  volumeUnit?: "股" | "手"
  changeBasis?: StockChangeBasis
}): React.JSX.Element {
  const preferences = useStockMarketStore((state) => state.chartPreferences)
  const { period, visible, showMA, showVolume, showMACD, showRSI, showKDJ, showBOLL, showCrosshair, volumeCompareEnabled, volumeCompareDays } = preferences
  const setPreferences = useStockMarketStore((state) => state.setChartPreferences)
  const setPeriod = (value: StockChartPeriod): void => { setHoverTime(null); setPreferences({ period: value }) }
  const setVisible = (value: StockChartPreferences["visible"]): void => setPreferences({ visible: value })
  const [fullscreen, setFullscreen] = useState(false)
  const [hoverTime, setHoverTime] = useState<string | null>(null)
  const [availableHeight, setAvailableHeight] = useState(420)
  const [zoomCommand, setZoomCommand] = useState({ revision: 0, scale: 1 })
  const [chartAttempt, setChartAttempt] = useState(0)
  const retryChart = () => setChartAttempt((attempt) => attempt + 1)
  const TickCanvas = useMemo(() => dynamic(() => import("./tick/echarts-candlestick").then((module) => module.EChartsCandlestick), {
    ssr: false, loading: (state) => StockChartLoading({ error: state.error, onRetry: retryChart }),
  }), [chartAttempt])
  useEffect(() => {
    const resize = () => setAvailableHeight(Math.max(320, Math.min(680, window.innerHeight - (fullscreen ? 160 : 300))))
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") setFullscreen(false) }
    resize()
    window.addEventListener("resize", resize)
    window.addEventListener("keydown", escape)
    return () => { window.removeEventListener("resize", resize); window.removeEventListener("keydown", escape) }
  }, [fullscreen])
  const cleaned = useMemo(() => prepareStockBars(bars), [bars])
  const data = useMemo(() => aggregateStockBars(cleaned, period, hasMore), [cleaned, period, hasMore])
  const averages = useMemo(() => AVERAGES.map((definition) => ({
    ...definition, values: stockSMA(data, definition.period),
  })), [data])
  const boll = useMemo(() => stockBOLL(data), [data])
  const subPanes = useMemo(() => [showVolume && "vol", showMACD && "macd", showRSI && "rsi", showKDJ && "kdj"].filter((name): name is string => typeof name === "string"), [showVolume, showMACD, showRSI, showKDJ])
  const activeIndicators = useMemo(() => [...subPanes, ...(showBOLL ? ["boll"] : [])], [subPanes, showBOLL])
  const volumeCompare = useMemo(() => ({ enabled: volumeCompareEnabled, days: volumeCompareDays }), [volumeCompareEnabled, volumeCompareDays])
  const periodUnit = period === "week" ? "周" : period === "month" ? "月" : "日"
  const hoverIndex = hoverTime ? data.findIndex((bar) => bar.time === hoverTime) : -1
  const selectedIndex = hoverIndex < 0 ? data.length - 1 : hoverIndex
  const selected = data[selectedIndex]
  const effectiveBasis = changeBasis === "vendor_ex_rights_pre_close" && period !== "day" ? "unavailable" : changeBasis
  const changeInfo = stockPriceChange(selected, data[selectedIndex - 1], effectiveBasis)
  const previousClose = changeInfo?.reference ?? null
  const change = changeInfo?.changePct ?? null
  const basisLabel = changeBasis === "vendor_ex_rights_pre_close" && period !== "day"
    ? "不复权周/月K跨除权不可直接比较，涨跌幅不计算" : stockChangeBasisLabel(effectiveBasis)
  const selectedBOLL = boll.find((point) => point.time === selected?.time)

  const tickData = useMemo(() => toTickChartData(data, effectiveBasis), [data, effectiveBasis])
  const chartHeight = height && !fullscreen ? Math.max(320, height - 156) : availableHeight
  const zoom = (scale: number) => setZoomCommand((previous) => ({ revision: previous.revision + 1, scale }))
  const reset = () => { setVisible(60); setHoverTime(null); zoom(0) }

  const indicators: { key: "showMA" | "showVolume" | "showMACD" | "showRSI" | "showKDJ" | "showBOLL"; label: string; title: string }[] = [
    { key: "showVolume", label: "成交量", title: `VOL及5/10${periodUnit}均量（${volumeUnit}）` },
    { key: "showMACD", label: "MACD", title: "MACD(12,26,9)，首价EMA初始化，柱值为2×(DIF−DEA)" },
    { key: "showRSI", label: "RSI", title: "RSI(6,14,24)，Wilder平滑，首段简单均值" },
    { key: "showKDJ", label: "KDJ", title: "KDJ(9,3,3)，首个有效RSV初始化" },
    { key: "showBOLL", label: "BOLL", title: "BOLL(20,2)，样本标准差，上/中/下轨" },
    { key: "showMA", label: "MA", title: `5/10/20/60${periodUnit}简单均线` },
  ]

  return (
    <section aria-label="A股K线工作区" className={cn("flex min-w-0 shrink-0 flex-col bg-[var(--chart-bg)] [&>*]:shrink-0", fullscreen ? "fixed inset-0 top-[56px] z-40 overflow-y-auto" : "min-h-full")} style={height && !fullscreen ? { minHeight: height } : undefined}>
      <div className="flex flex-wrap items-center gap-1.5 border-b border-[var(--border)] px-3 py-2">
        <button type="button" disabled title="尚未接入分时数据源，日线不能转换为分时" className={chartPillClass(false)}>分时</button>
        {([{ value: "day", label: "日K" }, { value: "week", label: "周K" }, { value: "month", label: "月K" }] as const).map((item) => (
          <button key={item.value} type="button" aria-pressed={period === item.value} onClick={() => setPeriod(item.value)} className={chartPillClass(period === item.value)}>{item.label}</button>
        ))}
        <button type="button" disabled title="尚未接入分钟K线数据源" className={chartPillClass(false)}>分钟K</button>
        <div className="ml-auto flex items-center gap-2">
          <select aria-label="显示K线根数" value={visible} onChange={(event) => setVisible(event.target.value === "all" ? "all" : Number(event.target.value) as 60 | 120 | 250)} className="rounded border border-[var(--border)] bg-[var(--bg-secondary)] px-2 py-1 text-xs text-[var(--text-secondary)]">
            <option value={60}>近60根</option><option value={120}>近120根</option><option value={250}>近250根</option><option value="all">已载入全部</option>
          </select>
          <button type="button" aria-label="缩小K线视窗" title="缩小K线视窗" onClick={() => zoom(1.25)} className={chartPillClass(false)}><ZoomOut className="h-3.5 w-3.5" /></button>
          <button type="button" aria-label="放大K线视窗" title="放大K线视窗" onClick={() => zoom(0.8)} className={chartPillClass(false)}><ZoomIn className="h-3.5 w-3.5" /></button>
          <button type="button" aria-label="十字线" aria-pressed={showCrosshair} title="切换十字线，悬停查看各副图指标" onClick={() => setPreferences({ showCrosshair: !showCrosshair })} className={chartPillClass(showCrosshair)}><Crosshair className="h-3.5 w-3.5" /></button>
          <button type="button" aria-label="复位K线" title="复位到最新60根K线" onClick={reset} className={chartPillClass(false)}><RotateCcw className="h-3.5 w-3.5" /></button>
          <button type="button" aria-label={fullscreen ? "退出放大K线" : "放大K线"} title={fullscreen ? "退出放大（Esc）" : "放大图表工作区"} onClick={() => setFullscreen(!fullscreen)} className={chartPillClass(false)}>{fullscreen ? <Minimize2 className="h-3.5 w-3.5" /> : <Maximize2 className="h-3.5 w-3.5" />}</button>
        </div>
      </div>
      <div aria-label="K线指标选项" className="flex flex-wrap items-center gap-1.5 border-b border-[var(--border)] px-3 py-1.5">
        {indicators.map((indicator) => <button key={indicator.key} type="button" aria-pressed={preferences[indicator.key]} title={indicator.title} onClick={() => setPreferences({ [indicator.key]: !preferences[indicator.key] })} className={chartPillClass(preferences[indicator.key])}>{indicator.label}</button>)}
        {showVolume && <div className="ml-0.5 flex items-center gap-1.5 border-l border-[var(--border)] pl-2" title="本周期成交量÷此前N个同周期均量；不是盘中量比。超过60根时隐藏柱上数值，悬停仍可查看。">
          <span className="text-[10px] text-[var(--text-muted)]">量比</span>
          <button type="button" role="switch" aria-label="开启量能对比" aria-checked={volumeCompareEnabled} onClick={() => setPreferences({ volumeCompareEnabled: !volumeCompareEnabled })} className={cn("relative h-3.5 w-6 shrink-0 rounded-full transition-colors cursor-pointer", volumeCompareEnabled ? "bg-[var(--primary)]" : "bg-[var(--border)]")}><span className={cn("absolute left-0 top-0.5 h-2.5 w-2.5 rounded-full bg-white transition-transform", volumeCompareEnabled ? "translate-x-3" : "translate-x-0.5")} /></button>
          <select aria-label="量能对比周期" value={volumeCompareDays} disabled={!volumeCompareEnabled} onChange={(event) => setPreferences({ volumeCompareDays: Number(event.target.value) })} className="h-5 rounded border border-[var(--border)] bg-[var(--bg-secondary)] px-1 text-[10px] text-[var(--text-secondary)] disabled:opacity-40">
            {Array.from({ length: 20 }, (_, index) => index + 1).map((days) => <option key={days} value={days}>前{days}{periodUnit}均量</option>)}
          </select>
        </div>}
        <button type="button" disabled title="缺少涨停、炸板及事件数据，不能从复权日线推测异动" className={cn("ml-auto", chartPillClass(false))}>异动 · 未接入</button>
      </div>
      <div className="min-h-[64px] space-y-1 border-b border-[var(--border)] bg-[var(--bg-secondary)] px-4 py-2 font-num text-xs">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[var(--text-secondary)]">
          {symbolLabel && <strong className="text-[var(--text-primary)]">{symbolLabel}</strong>}
          <strong className="text-[var(--text-primary)]">{selected?.time ?? "--"}</strong>
          {([{ label: "开", field: "open" }, { label: "高", field: "high" }, { label: "低", field: "low" }, { label: "收", field: "close" }] as const).map((item) => (
            <span key={item.field}>{item.label} <b style={{ color: selected && previousClose ? (selected[item.field] >= previousClose ? upColor : downColor) : undefined }}>{selected?.[item.field].toFixed(2) ?? "--"}</b></span>
          ))}
          <span style={{ color: change != null ? (change >= 0 ? upColor : downColor) : undefined }}>{change != null ? `${change >= 0 ? "+" : ""}${change.toFixed(2)}%` : "--"}</span>
          <span title={selected ? `${selected.volume} ${volumeUnit}` : undefined}>量 {selected ? formatVolume(selected.volume) : "--"} {volumeUnit}</span>
        </div>
        <div className="flex flex-wrap gap-x-4 gap-y-1">
          {showMA && averages.map((average) => <span key={average.period} style={{ color: average.color }}>MA{average.period} {average.values.find((point) => point.time === selected?.time)?.value.toFixed(2) ?? "--"}</span>)}
          {showBOLL && <span className="text-fuchsia-500">BOLL(20,2) 上 {formatValue(selectedBOLL?.upper)} · 中 {formatValue(selectedBOLL?.middle)} · 下 {formatValue(selectedBOLL?.lower)}</span>}
        </div>
      </div>
      {cleaned.length < bars.length && <div role="status" className="px-4 py-1 text-xs text-[var(--accent-warn)]">已排除无效或重复日线，请核对数据源。</div>}
      <div className="relative min-w-0 shrink-0" data-chart-engine="tick-echarts">
        {data.length ? <StockChartBoundary key={`${symbolLabel}:${period}:${chartAttempt}`} onRetry={retryChart}><TickCanvas key={`${symbolLabel}:${period}`} data={tickData} height={chartHeight}
          symbol={symbolLabel} showMA={showMA} showInfoBar={false} visibleBars={visible}
          activeIndicators={activeIndicators}
          volumeCompare={volumeCompare}
          onHoverDate={setHoverTime} crosshairEnabled={showCrosshair}
          zoomRevision={zoomCommand.revision} zoomScale={zoomCommand.scale} /></StockChartBoundary>
          : <div role="status" className="flex min-h-80 items-center justify-center text-sm text-[var(--text-muted)]">没有可展示的有效日线。</div>}
      </div>
      <div className="flex flex-wrap justify-between gap-x-3 gap-y-1 border-t border-[var(--border)] px-4 py-1.5 text-[10px] text-[var(--text-muted)]">
        <span title={STOCK_PRICE_CHANGE_METHOD_VERSION}>{basisLabel}</span>
        <span>拖动平移 · 滚轮缩放 · 主副图联动十字线</span>
        <span>已载入 {cleaned.length} 根 · {priceAdjustmentLabel} · {cleaned[0]?.time ?? "—"} 至 {cleaned.at(-1)?.time ?? "—"} · {data.length} 根{period !== "day" ? " · 日线合成" : ""}{hasMore ? " · 尚有更早数据" : ""}{hasMore && period !== "day" ? " · 已排除边界首周期" : ""}</span>
        <span className="inline-flex items-center gap-1" title={STOCK_CHART_METHOD_DESCRIPTION}><Info className="h-3 w-3" />{STOCK_CHART_METHOD_VERSION} · 指标按已载入区间初始化，不足周期显示 —</span>
        <span>分时、分钟K和异动源未接入</span>
      </div>
    </section>
  )
}
