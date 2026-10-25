"use client"

import { useEffect, useRef, useState } from "react"
import { captureAiScope } from "@/lib/ai-client-scope"
import { useAuthStore } from "@/stores/auth"
import { eodGapQuerySchema, getStockEodGaps, type EodGapQuery, type EodGapResponse } from "@/lib/stock-eod-gaps-api"

const control = "rounded border border-[var(--border)] bg-[var(--bg-secondary)] px-3 py-2 text-xs disabled:opacity-40"

export function StockEodGapBrowser({ refreshKey }: { refreshKey: number }): React.JSX.Element {
  const scopeKey = useAuthStore((state) => captureAiScope(state)?.key ?? null)
  const [keyword, setKeyword] = useState("")
  const [kind, setKind] = useState<EodGapQuery["kind"]>("all")
  const [applied, setApplied] = useState(() => eodGapQuerySchema.parse({}))
  const [result, setResult] = useState<EodGapResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState("")
  const [revision, setRevision] = useState(0)
  const generation = useRef(0)
  const controllerRef = useRef<AbortController | null>(null)

  useEffect(() => useAuthStore.subscribe((state, previous) => {
    if (captureAiScope(state)?.key === captureAiScope(previous)?.key) return
    generation.current++
    controllerRef.current?.abort()
    setResult(null)
    setError("")
    setLoading(false)
    setKeyword("")
    setKind("all")
    setApplied(eodGapQuerySchema.parse({}))
  }), [])

  useEffect(() => {
    const scope = captureAiScope(useAuthStore.getState())
    if (!scope || scope.market !== "stock") return
    const controller = new AbortController()
    controllerRef.current = controller
    const epoch = generation.current
    const active = () => !controller.signal.aborted && epoch === generation.current && captureAiScope(useAuthStore.getState())?.key === scope.key
    setLoading(true)
    setResult(null)
    setError("")
    void getStockEodGaps(applied, scope.accessToken, controller.signal)
      .then((value) => { if (active()) setResult(value) })
      .catch((failure: unknown) => { if (active()) setError(failure instanceof Error ? failure.message : "缺口读取失败，请重试") })
      .finally(() => { if (active()) setLoading(false) })
    return () => controller.abort()
  }, [applied, refreshKey, revision, scopeKey])

  function apply(next: EodGapQuery) {
    controllerRef.current?.abort()
    generation.current++
    setApplied(next)
    setResult(null)
    setError("")
  }

  return <section aria-label="逐证券盘后缺口" className="space-y-3 rounded border border-[var(--border)] p-3 text-xs leading-6">
    <h2 className="font-semibold">逐证券盘后缺口与来源</h2>
    <p className="text-[var(--text-muted)]">缺行情不是停牌结论；快照时未匹配目录的证券不会因后来入目录自动升级为可交易。名称/行业/上市日注释来自当前目录，不能代替历史成分。历史行数按查询时的股票库统计，交易日不晚于快照日，不证明当时可得，也不受分页指纹冻结；不认证连续性或复权锚点，不填价、不抓取、不改账户。</p>
    <form className="flex flex-wrap items-end gap-3" onSubmit={(event) => { event.preventDefault(); apply(eodGapQuerySchema.parse({ kind, keyword })) }}>
      <label>缺口类别 <select aria-label="盘后缺口类别" className={control} value={kind} onChange={(event) => setKind(event.target.value as EodGapQuery["kind"])}><option value="all">全部缺口</option><option value="missing">目录缺行情</option><option value="unknown">快照时未匹配目录</option></select></label>
      <label>代码或当前名称 <input aria-label="缺口代码或名称" className={control} value={keyword} maxLength={64} onChange={(event) => setKeyword(event.target.value)} /></label>
      <button type="submit" className={control}>应用缺口条件</button>
      <button type="button" className={control} onClick={() => { setKeyword(""); setKind("all"); apply(eodGapQuerySchema.parse({})) }}>重置缺口条件</button>
      <button type="button" className={control} onClick={() => { controllerRef.current?.abort(); generation.current++; setResult(null); setRevision((value) => value + 1) }}>重试缺口查询</button>
    </form>
    <p>已应用类别：{applied.kind === "all" ? "全部" : applied.kind === "missing" ? "目录缺行情" : "快照时未匹配目录"} · 关键词：{applied.keyword || "无"}；来源或目录变化需重新应用条件回到第一页。</p>
    {loading && <p role="status">正在只读查询盘后缺口…</p>}
    {error && <p role="alert" className="text-[var(--accent-danger)]">{error}</p>}
    {result?.state === "disabled" && <p role="status">盘后读取未启用，未查询缺口；不能解释为零缺口。</p>}
    {result?.state === "no_snapshot" && <p role="status">没有可用的已完成盘后快照，不能解释为全市场无缺口。</p>}
    {result?.state === "ready" && result.snapshot && result.totals && <>
      <div className="space-y-1 break-all"><p>快照日 {result.snapshot.trade_date} · 缺行情 {result.totals.missing} 只 · 快照时未匹配目录 {result.totals.unknown} 只</p><p>来源 {result.snapshot.provider} · <a href={result.snapshot.source_uri} target="_blank" rel="noreferrer" className="underline">来源接口说明地址</a> · 采集于 {result.snapshot.collected_at}</p><p>未复权盘后快照 · 版本 {result.snapshot.data_version}</p><p>{result.snapshot.source_evidence_recorded ? "记录了来源证据元数据，尚未在本查询独立核验" : "未记录来源证据元数据"}；内容哈希不等于交易所独立认证，运营账户配置不等于转授权/再分发认证。</p></div>
      {result.items.length === 0 ? <p role="status">当前条件没有缺口记录；不代表全历史或全市场金融质量已通过。</p> : <div className="overflow-x-auto"><table className="w-full min-w-[760px] text-left"><thead><tr>{["证券/当前名称", "缺口类别", "当前目录注释", "截至快照日历史", "详情"].map((label) => <th key={label} className="p-2">{label}</th>)}</tr></thead><tbody>{result.items.map((row) => <tr key={row.code} className="border-t border-[var(--border)]"><td className="p-2">{row.instrument_id ?? row.code}<br />{row.name ?? "当前目录无名称"}</td><td className="p-2">{row.kind === "missing" ? "目录缺行情，原因待核验" : "快照时未匹配目录，保持隔离"}</td><td className="p-2">{row.catalog_status === "present_now" ? "当前目录存在" : row.catalog_status === "absent_now" ? "当前目录仍缺失" : "当前交易所标识冲突"}<br />{row.industry ?? "行业未提供"} · 上市日 {row.current_list_date ?? "未提供"}</td><td className="p-2">{row.history.rows} 条<br />{row.history.first_date ?? "无起点"} — {row.history.last_date ?? "无终点"}<br />旧前复权库，非原价撮合凭证</td><td className="p-2">{row.catalog_status === "present_now" ? <a href={`/market?symbol=${row.code}`} className="underline">当前目录只读详情</a> : "不开放未核对标的跳转"}</td></tr>)}</tbody></table></div>}
      <div className="flex flex-wrap items-center gap-3"><span>符合条件 {result.total} 条 · 第 {Math.floor(applied.offset / applied.limit) + 1} 页</span><button type="button" className={control} disabled={loading || applied.offset === 0} onClick={() => apply({ ...applied, offset: Math.max(0, applied.offset - applied.limit), expected_fingerprint: result.query_fingerprint })}>上一页缺口</button><button type="button" className={control} disabled={loading || !result.has_more} onClick={() => apply({ ...applied, offset: applied.offset + applied.limit, expected_fingerprint: result.query_fingerprint })}>下一页缺口</button></div>
    </>}
  </section>
}
