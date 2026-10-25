"use client"

import { useEffect, useRef, useState } from "react"
import { captureAiScope } from "@/lib/ai-client-scope"
import { useAuthStore } from "@/stores/auth"
import { getStockScreenApi, parseStockScreenDraft, type StockScreenResponse } from "@/lib/stock-screen-api"

const fields = [["keyword", "代码或名称"], ["min_close", "收盘价下限"], ["max_close", "收盘价上限"], ["min_change_pct", "涨跌幅下限"], ["max_change_pct", "涨跌幅上限"], ["min_volume", "成交量下限"]] as const
const control = "rounded border border-[var(--border)] bg-[var(--bg-secondary)] px-3 py-2 text-xs disabled:opacity-40"
const emptyDraft = () => ({ keyword: "", min_close: "", max_close: "", min_change_pct: "", max_change_pct: "", min_volume: "", sort: "code", direction: "asc", data_only: "false" })

export function StockScreenResults({ industry, refreshKey, onSelect }: { industry: string; refreshKey: number; onSelect: (code: string) => void }): React.JSX.Element {
  const scopeKey = useAuthStore((state) => captureAiScope(state)?.key ?? null)
  const [draft, setDraft] = useState<Record<string, string>>(emptyDraft)
  const [applied, setApplied] = useState(() => parseStockScreenDraft({}, industry))
  const [result, setResult] = useState<StockScreenResponse | null>(null)
  const [error, setError] = useState("")
  const [snapshotChanged, setSnapshotChanged] = useState(false)
  const [loading, setLoading] = useState(false)
  const [revision, setRevision] = useState(0)
  const generation = useRef(0)
  const controllerRef = useRef<AbortController | null>(null)
  const lastRefresh = useRef(refreshKey)

  useEffect(() => useAuthStore.subscribe((state, previous) => {
    if (captureAiScope(state)?.key === captureAiScope(previous)?.key) return
    generation.current++
    controllerRef.current?.abort()
    setResult(null)
    setLoading(false)
    setError("")
    setSnapshotChanged(false)
    setDraft(emptyDraft())
    setApplied(parseStockScreenDraft({}, industry))
  }), [industry])

  useEffect(() => {
    const scope = captureAiScope(useAuthStore.getState())
    if (!scope || scope.market !== "stock") return
    if (lastRefresh.current !== refreshKey) {
      lastRefresh.current = refreshKey
      // A manual workspace refresh establishes a new snapshot; replaying a
      // pagination fingerprint here would keep failing after an EOD update.
      if (applied.offset !== 0 || applied.expected_fingerprint !== null) {
        restartApplied()
        return
      }
    }
    const controller = new AbortController()
    controllerRef.current = controller
    const epoch = generation.current
    const active = () => !controller.signal.aborted && epoch === generation.current && captureAiScope(useAuthStore.getState())?.key === scope.key
    setLoading(true)
    setResult(null)
    setError("")
    setSnapshotChanged(false)
    void getStockScreenApi(applied, controller.signal).then((value) => { if (active()) setResult(value) })
      .catch((failure: unknown) => {
        if (!active()) return
        const conflict = (failure as { status?: number } | null)?.status === 409 && applied.expected_fingerprint !== null
        setSnapshotChanged(conflict)
        setError(failure instanceof Error ? failure.message : "筛选读取失败，请重试")
      })
      .finally(() => { if (active()) setLoading(false) })
    return () => controller.abort()
  }, [applied, refreshKey, revision, scopeKey])

  function restartApplied() {
    controllerRef.current?.abort()
    generation.current++
    setResult(null)
    setError("")
    setSnapshotChanged(false)
    setApplied((value) => ({ ...value, offset: 0, expected_fingerprint: null }))
  }

  function apply(event: React.FormEvent) {
    event.preventDefault()
    try {
      const next = parseStockScreenDraft(draft, industry)
      controllerRef.current?.abort()
      generation.current++
      setApplied(next)
      setResult(null)
      setError("")
      setSnapshotChanged(false)
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "筛选条件无效")
    }
  }

  const volumeUnit = result?.volume_unit === "lot_100_shares" ? "手（100股）" : result?.volume_unit ?? "快照原始单位，见结果说明"
  return <section aria-label="A股多条件筛选" className="space-y-3">
    <form onSubmit={apply} className="space-y-3 rounded border border-[var(--border)] p-3 text-xs">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-3">{fields.map(([key, label]) => <label key={key} className="flex flex-col gap-1">{label}{key.includes("close") ? "（元/股）" : key.includes("change") ? "（%）" : key === "min_volume" ? `（${volumeUnit}）` : ""}<input aria-label={label} maxLength={key === "keyword" ? 64 : 40} inputMode={key === "keyword" ? "text" : "decimal"} className={control} value={draft[key]} onChange={(event) => setDraft((current) => ({ ...current, [key]: event.target.value }))} /></label>)}</div>
      <div className="flex flex-wrap items-center gap-3">
        <label>排序 <select aria-label="多条件筛选排序" className={control} value={draft.sort} onChange={(event) => setDraft((current) => ({ ...current, sort: event.target.value }))}><option value="code">代码</option><option value="name">名称</option><option value="change_pct">涨跌幅</option></select></label>
        <select aria-label="多条件筛选排序方向" className={control} value={draft.direction} onChange={(event) => setDraft((current) => ({ ...current, direction: event.target.value }))}><option value="asc">升序</option><option value="desc">降序</option></select>
        <label><input type="checkbox" checked={draft.data_only === "true"} onChange={(event) => setDraft((current) => ({ ...current, data_only: String(event.target.checked) }))} /> 仅当前快照有价</label>
        <button type="submit" className={control}>应用条件</button>
        <button type="button" className={control} onClick={() => { controllerRef.current?.abort(); generation.current++; setDraft(emptyDraft()); setApplied(parseStockScreenDraft({}, industry)) }}>重置条件</button>
        <button type="button" className={control} disabled={loading} onClick={restartApplied}>按已应用条件刷新第一页</button>
      </div>
      <p className="text-[var(--text-muted)]">条件同时满足（AND）；编辑后点击应用才查询。数值条件仅使用当前快照有效行情，缺失或过期不补0；结果不是历史研究股票池，也不表示可回放交易。</p>
    </form>
    <p aria-label="已应用筛选条件" className="text-xs leading-6 text-[var(--text-muted)]">已应用：{applied.industry || "全部行业"} · 关键词 {applied.keyword || "不限"} · {applied.sort === "code" ? "代码" : applied.sort === "name" ? "名称" : "涨跌幅"}{applied.direction === "asc" ? "升序" : "降序"} · {applied.data_only ? "仅当前快照有价" : "含仅目录证券"}{fields.filter(([key]) => key !== "keyword").map(([key, label]) => {
      const value = applied[key as "min_close" | "max_close" | "min_change_pct" | "max_change_pct" | "min_volume"]
      return value === null ? null : <span key={key}> · {label} {value}{key.includes("close") ? " 元/股" : key.includes("change") ? "%" : ` ${volumeUnit}`}</span>
    })}。编辑表单后，点击“应用条件”才会改变当前查询。</p>
    {error && <p role="alert" className="text-xs text-[var(--accent-danger)]">{error} {snapshotChanged ? <span>行情或目录快照已变化，请按已应用条件刷新第一页；未应用的编辑不会被提交。</span> : <button type="button" className={control} onClick={() => setRevision((value) => value + 1)}>重试已应用条件</button>}</p>}
    {loading && <p role="status" className="text-xs">正在筛选真实日线快照…</p>}
    {result && <>
      <p className="text-xs">符合条件 {result.total} 只 · 行情日 {result.as_of ?? "无行情"} · 来源 {result.source} · {result.price_adjustment === "none" ? "不复权" : result.price_adjustment === "qfq" ? "前复权" : result.price_adjustment} · 成交量单位 {volumeUnit}</p>
      <div className="overflow-auto"><table className="w-full text-left text-xs"><thead><tr>{["证券", "名称", "收盘价（元）", "涨跌幅（%）", `成交量（${volumeUnit}）`].map((label) => <th key={label} className="p-2">{label}</th>)}</tr></thead><tbody>{result.items.map((item) => <tr key={item.instrument_id} className="border-t border-[var(--border)]"><td className="p-2">{item.instrument_id}</td><td><button type="button" className="underline" onClick={() => onSelect(item.code)}>{item.name}</button></td><td>{item.close ?? "—"}</td><td>{item.change_pct ?? "—"}</td><td>{item.volume ?? "—"}</td></tr>)}</tbody></table></div>
      {result.items.length === 0 && <p role="status" className="text-xs">{applied.offset > 0 ? "当前页没有股票，结果可能已变化；可按已应用条件刷新第一页。" : "没有满足已应用条件的股票；未补价或扩大筛选范围。"}</p>}
      <div className="flex items-center gap-3 text-xs"><button type="button" className={control} disabled={loading || applied.offset === 0} onClick={() => setApplied((value) => ({ ...value, offset: Math.max(0, value.offset - value.limit), expected_fingerprint: result.screen_fingerprint }))}>上一页</button><span>第 {Math.floor(applied.offset / applied.limit) + 1} 页，每页 {applied.limit} 条</span><button type="button" className={control} disabled={loading || !result.has_more || applied.offset + applied.limit > 10000} onClick={() => setApplied((value) => ({ ...value, offset: value.offset + value.limit, expected_fingerprint: result.screen_fingerprint }))}>下一页</button></div>
      {result.has_more && applied.offset + applied.limit > 10000 && <p role="status" className="text-xs text-[var(--accent-warn)]">已到分页上限，请缩小已应用筛选范围；当前结果未完整展示。</p>}
    </>}
  </section>
}
