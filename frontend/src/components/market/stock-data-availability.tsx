"use client"

import { useEffect, useRef, useState } from "react"
import { captureAiScope } from "@/lib/ai-client-scope"
import { useAuthStore } from "@/stores/auth"
import { StockEodGapBrowser } from "@/components/market/stock-eod-gap-browser"
import { StockArchiveCatalog } from "@/components/market/stock-archive-catalog"
import { getStockDataAvailability, parseAvailabilityPeriod, type AvailabilityPeriod, type AvailabilityRead, type StockDataAvailability as AvailabilityResult } from "@/lib/stock-data-availability-api"

const control = "rounded border border-[var(--border)] bg-[var(--bg-secondary)] px-3 py-2 text-xs disabled:opacity-40"
const card = "space-y-2 rounded border border-[var(--border)] p-3 text-xs leading-6"

function ReadFailure({ result }: { result: AvailabilityRead<unknown> | undefined }): React.JSX.Element | null {
  return result?.state === "failed" ? <p role="alert" className="text-[var(--accent-danger)]">{result.message}{result.request_id ? ` · 请求 ${result.request_id}` : ""}</p> : null
}

export function StockDataAvailability({ refreshKey }: { refreshKey: number }): React.JSX.Element {
  const scopeKey = useAuthStore((state) => captureAiScope(state)?.key ?? null)
  const [draft, setDraft] = useState({ start_date: "", end_date: "" })
  const [applied, setApplied] = useState<AvailabilityPeriod>({})
  const [result, setResult] = useState<AvailabilityResult | null>(null)
  const [error, setError] = useState("")
  const [loading, setLoading] = useState(false)
  const [revision, setRevision] = useState(0)
  const [visibleDatasets, setVisibleDatasets] = useState(20)
  const [showGaps, setShowGaps] = useState(false)
  const [showArchive, setShowArchive] = useState(false)
  const generation = useRef(0)
  const controllerRef = useRef<AbortController | null>(null)

  useEffect(() => useAuthStore.subscribe((state, previous) => {
    if (captureAiScope(state)?.key === captureAiScope(previous)?.key) return
    generation.current++
    controllerRef.current?.abort()
    setResult(null)
    setError("")
    setLoading(false)
    setDraft({ start_date: "", end_date: "" })
    setApplied({})
    setVisibleDatasets(20)
    setShowGaps(false)
    setShowArchive(false)
  }), [])

  useEffect(() => {
    const scope = captureAiScope(useAuthStore.getState())
    if (!scope || scope.market !== "stock") return
    const controller = new AbortController()
    controllerRef.current = controller
    const epoch = generation.current
    const active = () => !controller.signal.aborted && epoch === generation.current && captureAiScope(useAuthStore.getState())?.key === scope.key
    setResult(null)
    setError("")
    setLoading(true)
    setVisibleDatasets(20)
    void getStockDataAvailability(applied, scope.accessToken, controller.signal)
      .then((value) => { if (active()) setResult(value) })
      .catch((failure: unknown) => { if (active()) setError(failure instanceof Error ? failure.message : "数据覆盖读取失败，请重试") })
      .finally(() => { if (active()) setLoading(false) })
    return () => controller.abort()
  }, [applied, refreshKey, revision, scopeKey])

  function applyPeriod(next: AvailabilityPeriod) {
    controllerRef.current?.abort()
    generation.current++
    setApplied(next)
    setResult(null)
    setError("")
  }

  const market = result?.market.state === "ready" ? result.market.data : null
  const coverage = result?.coverage.state === "ready" ? result.coverage.data : null
  const replay = result?.replay.state === "ready" ? result.replay.data : null
  const research = result?.research.state === "ready" ? result.research.data : null

  return <section aria-label="A股数据覆盖资格" className="space-y-3 text-xs">
    <p className="leading-6 text-[var(--text-muted)]">目录、当日行情、历史观测、研究预检和回放注册是不同范围；各来源独立读取，并非同一不可变数据版本。有记录不代表连续历史、授权审计或金融质量已通过。本页只读，不抓取数据或创建任务。</p>
    <form className="flex flex-wrap items-end gap-3" onSubmit={(event) => { event.preventDefault(); try { applyPeriod(parseAvailabilityPeriod(draft)) } catch (failure) { setError(failure instanceof Error ? failure.message : "覆盖区间无效") } }}>
      <label className="flex flex-col gap-1">研究覆盖开始<input type="date" aria-label="覆盖开始日期" className={control} value={draft.start_date} onChange={(event) => setDraft((current) => ({ ...current, start_date: event.target.value }))} /></label>
      <label className="flex flex-col gap-1">研究覆盖结束<input type="date" aria-label="覆盖结束日期" className={control} value={draft.end_date} onChange={(event) => setDraft((current) => ({ ...current, end_date: event.target.value }))} /></label>
      <button type="submit" className={control}>查询覆盖区间</button>
      <button type="button" className={control} onClick={() => { setDraft({ start_date: "", end_date: "" }); applyPeriod({}) }}>重置推荐区间</button>
      <button type="button" className={control} onClick={() => { controllerRef.current?.abort(); generation.current++; setRevision((value) => value + 1); setResult(null) }}>重试已应用区间</button>
    </form>
    <p>已应用研究区间：{applied.start_date ?? "留空，由服务端选择"} — {applied.end_date ?? "留空，由服务端选择"}；空区间使用既有推荐窗口，不自动查询五年。此区间不改变行情日期或回放数据集。</p>
    {loading && <p role="status">正在读取各来源覆盖，最长约20秒；不调用模型或上游行情。</p>}
    {error && <p role="alert" className="text-[var(--accent-danger)]">{error}</p>}
    <div className="grid gap-3 md:grid-cols-2">
      <section className={card} aria-label="目录和当前行情覆盖"><h2 className="font-semibold">证券目录与当日行情</h2><ReadFailure result={result?.market} />
        {market && <><p>证券目录 {market.coverage.instruments} 只 · 当前行情 {market.coverage.quoted} / {market.coverage.instruments} · 可比涨跌 {market.coverage.comparable} 只</p><p>交易日 {market.as_of ?? "尚无日线"} · 来源 {market.source} · {market.price_adjustment === "none" ? "未复权" : market.price_adjustment === "qfq" ? "前复权" : market.price_adjustment}</p>
          {market.missing_codes ? <p>目录缺行情 {market.missing_codes.length} 只：{market.missing_codes.slice(0, 30).join("、") || "无"}{market.missing_codes.length > 30 ? "（仅展示前30只）" : ""}</p> : <p>当前来源未提供逐证券缺口清单，不能据此判定停牌。</p>}
          {market.unknown_codes && <p>来源未匹配目录 {market.unknown_codes.length} 只：{market.unknown_codes.slice(0, 30).join("、") || "无"}{market.unknown_codes.length > 30 ? "（仅展示前30只）" : ""}</p>}
        </>}
      </section>
      <section className={card} aria-label="历史观测覆盖"><h2 className="font-semibold">本地历史观测（非连续历史认证）</h2><ReadFailure result={result?.coverage} />
        {coverage && <><p>历史观测 {coverage.available.securities} 只 · {coverage.available.price_rows} 条 · {coverage.available.observed_dates} 个观测日</p><p>{coverage.available.first_trade_date ?? "无历史起点"} — {coverage.available.last_trade_date ?? "无历史终点"}</p><p>研究输入为前复权未版本化历史；行数不证明交易日历完整，也不能用于未复权模拟撮合。</p><p className="text-[var(--text-muted)]">读取时间 {coverage.as_of}</p></>}
      </section>
      <section className={card} aria-label="研究预检与执行能力"><h2 className="font-semibold">所选研究区间预检</h2><ReadFailure result={result?.coverage} /><ReadFailure result={result?.research} />
        {coverage && <><p>实际预检区间 {coverage.requested?.start_date ?? "无"} — {coverage.requested?.end_date ?? "无"}</p><p>{coverage.selected.securities} 只 · {coverage.selected.observed_dates} 个观测日 · {coverage.selected.price_rows} 条 · 缺价 {coverage.selected.missing_price_cells} 格</p><p>{coverage.status === "within_limits" ? "仅在资源上限内，不保证研究成功" : coverage.status === "over_limit" ? "超过快照资源上限，请缩短区间" : "区间无本地价量数据"}；预检不等于样本/时点/策略验证通过。</p><ul>{coverage.warnings.slice(0, 8).map((warning, index) => <li key={index}>{warning}</li>)}</ul></>}
        {research && <p>研究执行器：{research.accepting ? "当前可接受请求，仍需任务参数与数据校验" : research.reason ?? "当前不能接受新任务"}</p>}
        <a href="/factor-research" className="underline">打开研究页核验参数与因子依赖</a>
      </section>
      <section className={card} aria-label="回放注册覆盖"><h2 className="font-semibold">未复权日线回放注册</h2><ReadFailure result={result?.replay} />
        {replay && <>{!replay.enabled ? <p>回放未启用或版本迁移未就绪；未读取注册集合，不能解释为全库没有数据。</p> : <><p>本次返回 {replay.datasets.length} 个数据集，涉及 {new Set(replay.datasets.map((dataset) => dataset.code)).size} 只展示证券；接口最多100个数据集，不代表全部注册总数或全市场可交易。</p>{replay.datasets.length === 0 && <p>本次未返回已注册数据集。</p>}<ul className="max-h-64 space-y-2 overflow-y-auto">{replay.datasets.slice(0, visibleDatasets).map((dataset) => <li key={dataset.id}>{dataset.instrument_id} {dataset.name} · {dataset.start_date} — {dataset.end_date} · 来源 {dataset.source} · 版本 {dataset.version}</li>)}</ul>{visibleDatasets < replay.datasets.length && <button type="button" className={control} onClick={() => setVisibleDatasets((value) => Math.min(value + 20, 100))}>显示更多数据集</button>}</>}
          <p>注册不代表任何日期均可下单；创建/推进账户仍由回放服务校验数据、板块规则和权限，不接真实账户。</p></>}
        <a href="/trading" className="underline">打开A股日线模拟盘核验可选集合</a>
      </section>
    </div>
    <button type="button" className={control} aria-pressed={showGaps} onClick={() => setShowGaps((value) => !value)}>{showGaps ? "收起逐证券盘后缺口" : "查看逐证券盘后缺口"}</button>
    {showGaps && <StockEodGapBrowser refreshKey={refreshKey} />}
    <button type="button" className={control} aria-pressed={showArchive} onClick={() => setShowArchive((value) => !value)}>{showArchive ? "收起原价归档版本目录" : "查看原价归档版本目录"}</button>
    {showArchive && <StockArchiveCatalog refreshKey={refreshKey} />}
  </section>
}
