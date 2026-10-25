"use client"

import { useEffect, useRef, useState } from "react"
import { useAuthStore } from "@/stores/auth"
import { captureAiScope } from "@/lib/ai-client-scope"
import { getStockArchiveCatalog, type StockArchiveCatalogPage } from "@/lib/stock-archive-catalog-api"
import { StockArchiveObservation } from "@/components/market/stock-archive-observation"

const control = "rounded border border-[var(--border)] bg-[var(--bg-secondary)] px-3 py-2 text-xs disabled:opacity-40"
type Selection = { date: string; offset: number; snapshot?: string; membership?: string }

export function StockArchiveCatalog({ refreshKey }: { refreshKey: number }): React.JSX.Element | null {
  const scopeKey = useAuthStore((state) => captureAiScope(state)?.key ?? null)
  const token = useAuthStore((state) => state.accessToken)
  const [draft, setDraft] = useState("")
  const [selection, setSelection] = useState<Selection>({ date: "", offset: 0 })
  const [page, setPage] = useState<StockArchiveCatalogPage | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")
  const [revision, setRevision] = useState(0)
  const [auditVersion, setAuditVersion] = useState<string | null>(null)
  const generation = useRef(0)
  const controller = useRef<AbortController | null>(null)
  useEffect(() => useAuthStore.subscribe((next, previous) => {
    if (captureAiScope(next)?.key === captureAiScope(previous)?.key && next.accessToken === previous.accessToken) return
    generation.current++
    controller.current?.abort()
    setPage(null); setError(""); setDraft(""); setSelection({ date: "", offset: 0 })
    setAuditVersion(null)
  }), [])
  useEffect(() => {
    const scope = captureAiScope(useAuthStore.getState())
    if (!scope || scope.market !== "stock") return
    const request = new AbortController()
    controller.current = request
    const epoch = generation.current
    const active = () => !request.signal.aborted && epoch === generation.current && captureAiScope(useAuthStore.getState())?.key === scope.key && useAuthStore.getState().accessToken === scope.accessToken
    setPage(null); setLoading(true); setError(""); setAuditVersion(null)
    void getStockArchiveCatalog({ limit: 20, offset: selection.offset, ...(selection.date ? { trade_date: selection.date } : {}), ...(selection.snapshot ? { snapshot_at: selection.snapshot } : {}), ...(selection.membership ? { membership_token: selection.membership } : {}) }, scope.accessToken, request.signal)
      .then((value) => { if (active()) setPage(value) })
      .catch((failure: unknown) => { if (active()) setError(failure instanceof Error ? failure.message : "归档目录读取失败") })
      .finally(() => { if (active()) setLoading(false) })
    return () => request.abort()
  }, [scopeKey, token, selection, refreshKey, revision])
  function select(next: Selection) {
    controller.current?.abort(); generation.current++
    setPage(null); setError(""); setLoading(true); setSelection(next); setAuditVersion(null)
  }
  const scope = captureAiScope(useAuthStore.getState())
  if (!scope || scope.market !== "stock") return null
  return <section aria-label="原价归档版本目录" className="space-y-3 rounded border border-[var(--border)] p-3 text-xs leading-6">
    <h2 className="font-semibold">原价影子归档版本目录</h2>
    <p>只读已有批次元数据，不下载价格、不调用模型。未复权日线，CNY，成交量单位为手（100股）。这不是逐行质量、历史完整性或PIT认证，不能直接用于回测或撮合。</p>
    <p>来源为用户配置的授权Teajoin兼容渠道；当前记录不是再分发授权认证。版本是已有快照与方法的标识，不代表独立核验或历史可获得时间已确认。旧前复权研究和回放注册集合均不改变。</p>
    <form className="flex flex-wrap items-end gap-2" onSubmit={(event) => {
      event.preventDefault()
      const today = new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Shanghai" }).format(new Date())
      if (draft && (!/^\d{4}-\d{2}-\d{2}$/.test(draft) || !Number.isFinite(Date.parse(draft)) || new Date(draft).toISOString().slice(0, 10) !== draft || draft > today)) { setError("归档交易日期必须有效且不晚于上海今天"); return }
      select({ date: draft, offset: 0 })
    }}>
      <label>交易日（可留空）<input type="date" aria-label="归档交易日期" className={control} value={draft} onChange={(event) => setDraft(event.target.value)} /></label>
      <button type="submit" className={control}>应用归档日期</button>
      <button type="button" className={control} onClick={() => { select({ date: selection.date, offset: 0 }); setRevision((value) => value + 1) }}>刷新归档版本</button>
    </form>
    <p>已应用交易日：{selection.date || "全部已有日期"}；翻页核对快照与目录成员，变化时拒绝并提示刷新，不自动重试。刷新重新锚定。当前元数据目录不会新建数据版本或研究任务。</p>
    {loading && <p role="status">正在读取归档版本…</p>}
    {error && <p role="alert" className="text-[var(--accent-danger)]">{error}</p>}
    {!loading && !error && page && <>
      <p>目录快照 {page.snapshot_at} · 共 {page.total} 个版本 · 第 {Math.floor(selection.offset / 20) + 1} 页</p>
      {page.items.length === 0 && <p>当前条件下暂无归档版本；不代表本地旧历史为空或全市场无行情。</p>}
      <div className="max-h-96 space-y-3 overflow-y-auto">{page.items.map((item) => <article key={item.batch_id} className="space-y-1 rounded border border-[var(--border)] p-2">
        <h3>{item.trade_date} · 覆盖 {item.row_count} / {item.expected_count} · 缺失 {item.missing_count} · 未匹配 {item.unknown_count}</h3>
        <p>采集 {item.source_collected_at} · 归档 {item.archived_at}</p>
        <p className="break-all">归档版本 {item.version}</p><p className="break-all">来源快照版本 {item.source_data_version}</p><p className="break-all">目录版本 {item.catalog_version}</p>
        <p>方法 {item.method_version} · 仅元数据核验，研究/回放资格均未授予。</p>
        <button type="button" className={control} onClick={() => setAuditVersion(item.version)}>核对本版本原价</button>
      </article>)}</div>
      <div className="flex gap-3"><button type="button" className={control} disabled={selection.offset === 0} onClick={() => select({ ...selection, offset: Math.max(0, selection.offset - 20), snapshot: page.snapshot_at, membership: page.membership_token })}>上一页归档版本</button><button type="button" className={control} disabled={!page.has_more || selection.offset >= 10000} onClick={() => select({ ...selection, offset: selection.offset + 20, snapshot: page.snapshot_at, membership: page.membership_token })}>下一页归档版本</button></div>
      {page.has_more && selection.offset >= 10000 && <p>已达目录分页边界，请按交易日缩小范围。</p>}
      {auditVersion && <><button type="button" className={control} onClick={() => setAuditVersion(null)}>关闭本版本核对</button><StockArchiveObservation key={auditVersion} version={auditVersion} /></>}
    </>}
  </section>
}
