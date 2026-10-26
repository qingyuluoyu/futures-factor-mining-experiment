"use client"

import { useEffect, useRef, useState } from "react"
import { useAuthStore } from "@/stores/auth"
import { getStockReportBookmark, setStockReportBookmark, stockResearchScope, type StockReportBookmark } from "@/lib/stock-research-api"
import { clearPendingWrite, newPendingWriteKey, pendingWriteFailureMessage, readPendingWrites, recordPendingWrite, type PendingWriteEntry } from "@/lib/stock-pending-writes"

/** 本组件登记的未确认写类型；服务端没有幂等键，用本地句柄配对登记与清除。 */
const PENDING_KIND = "report-bookmark"

/** 从条目签名还原「哪一份报告、想要什么状态」；签名不可解析时返回 null。 */
function bookmarkIntent(entry: PendingWriteEntry): { jobId: string; desired: boolean } | null {
  try {
    const parsed = JSON.parse(entry.signature) as { id?: unknown; bookmarked?: unknown }
    return typeof parsed.id === "string" && typeof parsed.bookmarked === "boolean" ? { jobId: parsed.id, desired: parsed.bookmarked } : null
  } catch {
    return null
  }
}

export function ResearchReportBookmark({ jobId, token, scope, onChanged }: { jobId: string; token: string; scope: string; onChanged: () => void }): React.JSX.Element | null {
  const [view, setView] = useState<StockReportBookmark | null>(null)
  const [loading, setLoading] = useState(true)
  const [writing, setWriting] = useState(false)
  const [unknown, setUnknown] = useState(false)
  const [error, setError] = useState("")
  // 跨页未确认写：卸载不清除，回到本页只提示，绝不替用户重写。
  const [pending, setPending] = useState<readonly PendingWriteEntry[]>([])
  const mounted = useRef(false)
  const inflight = useRef(false)
  const controller = useRef<AbortController | null>(null)
  const revision = useRef(useAuthStore.getState().sessionRevision)
  const latest = useRef({ jobId, token, scope, onChanged })
  latest.current = { jobId, token, scope, onChanged }
  const syncPending = () => setPending(readPendingWrites(scope).filter((entry) => entry.kind === PENDING_KIND))
  const sessionCurrent = () => {
    const auth = useAuthStore.getState()
    return auth.accessToken === token && auth.sessionRevision === revision.current && auth.user?.market_type === "stock"
      && stockResearchScope(auth.user.id, token) === scope && latest.current.jobId === jobId && latest.current.token === token && latest.current.scope === scope
  }
  const current = () => mounted.current && sessionCurrent()
  /** 读到权威状态后，只清除「记录意向与当前状态一致」的条目；不一致的保留提示。 */
  const reconcile = (saved: StockReportBookmark) => {
    for (const entry of pending) {
      const intent = bookmarkIntent(entry)
      if (intent && intent.jobId === jobId && intent.desired === saved.bookmarked) clearPendingWrite(scope, entry.idempotency_key)
    }
    syncPending()
  }
  const query = async () => {
    if (!current() || inflight.current) return
    controller.current?.abort()
    const request = new AbortController()
    controller.current = request
    const valid = () => current() && controller.current === request && !request.signal.aborted
    inflight.current = true
    setLoading(true)
    setError("")
    try {
      const saved = await getStockReportBookmark(jobId, token, request.signal)
      if (!valid()) return
      setView(saved)
      setUnknown(false)
      reconcile(saved)
      if (unknown) latest.current.onChanged()
    } catch (failure) {
      if (valid()) { setView(null); setError(failure instanceof Error ? failure.message : "收藏状态查询失败") }
    } finally {
      if (valid()) { inflight.current = false; setLoading(false) }
    }
  }
  const toggle = async () => {
    if (!current() || inflight.current || loading || unknown || !view || view.job_id !== jobId) return
    controller.current?.abort()
    const request = new AbortController()
    controller.current = request
    const valid = () => current() && controller.current === request && !request.signal.aborted
    inflight.current = true
    setWriting(true)
    setError("")
    try {
      const saved = await setStockReportBookmark(jobId, !view.bookmarked, token, request.signal)
      if (!valid()) return
      setView(saved)
      setUnknown(false)
      reconcile(saved)
      latest.current.onChanged()
    } catch (failure) {
      if (!valid()) return
      setUnknown(true)
      // 请求已发出而结果未知，此刻登记是唯一能跨页恢复它的机会；登记本身也失败时
      // 必须如实说明，不能仍暗示"离开页面、刷新都不会丢失"。
      const receipt = recordPendingWrite(scope, { kind: PENDING_KIND, idempotency_key: newPendingWriteKey(),
        signature: JSON.stringify({ id: jobId, bookmarked: !view.bookmarked }), created_at: new Date().toISOString() })
      setError(`保存结果尚未确认，请先查询收藏状态；未自动重写。${receipt.ok ? "" : pendingWriteFailureMessage(receipt.reason)}${failure instanceof Error ? failure.message : "请求失败"}`)
      syncPending()
    } finally {
      if (valid()) { inflight.current = false; setWriting(false) }
    }
  }
  useEffect(() => {
    mounted.current = true
    inflight.current = false
    setView(null)
    setWriting(false)
    setUnknown(false)
    syncPending()
    void query()
    return () => { mounted.current = false; controller.current?.abort(); inflight.current = false }
  }, [jobId, token, scope])
  if (!sessionCurrent()) return null
  const saved = view?.job_id === jobId ? view : null
  const outstanding = pending.filter((entry) => bookmarkIntent(entry)?.jobId === jobId)
  return <section aria-label="报告收藏" className="space-y-2 rounded border border-[var(--border)] p-3 text-xs">
    <p>仅保存本人当前登录盘的报告引用，不修改研究结果、不新建任务；收藏不代表策略已验证。</p>
    {loading && <p role="status">读取收藏状态…</p>}
    {error && <p role="alert">{error}</p>}
    {outstanding.length > 0 && <p role="status">上次的收藏保存结果未确认（{outstanding.map((entry) => `请求${bookmarkIntent(entry)?.desired ? "收藏" : "取消收藏"}`).join("；")}）。请查询或重新操作，未自动重写。</p>}
    <div className="flex gap-4"><button type="button" disabled={loading || writing || unknown || !saved} onClick={() => void toggle()} className="underline disabled:opacity-40">{saved?.bookmarked ? "取消收藏" : "收藏报告"}</button><button type="button" disabled={loading || writing} onClick={() => void query()} className="underline disabled:opacity-40">重新查询收藏状态</button></div>
    {writing && <p role="status">正在保存收藏…</p>}
    {saved?.created_at && !unknown && <p>收藏时间：{saved.created_at}</p>}
  </section>
}
