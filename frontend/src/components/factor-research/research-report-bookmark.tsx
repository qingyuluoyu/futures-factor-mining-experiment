"use client"

import { useEffect, useRef, useState } from "react"
import { useAuthStore } from "@/stores/auth"
import { getStockReportBookmark, setStockReportBookmark, stockResearchScope, type StockReportBookmark } from "@/lib/stock-research-api"

export function ResearchReportBookmark({ jobId, token, scope, onChanged }: { jobId: string; token: string; scope: string; onChanged: () => void }): React.JSX.Element | null {
  const [view, setView] = useState<StockReportBookmark | null>(null)
  const [loading, setLoading] = useState(true)
  const [writing, setWriting] = useState(false)
  const [unknown, setUnknown] = useState(false)
  const [error, setError] = useState("")
  const mounted = useRef(false)
  const inflight = useRef(false)
  const controller = useRef<AbortController | null>(null)
  const revision = useRef(useAuthStore.getState().sessionRevision)
  const latest = useRef({ jobId, token, scope, onChanged })
  latest.current = { jobId, token, scope, onChanged }
  const sessionCurrent = () => {
    const auth = useAuthStore.getState()
    return auth.accessToken === token && auth.sessionRevision === revision.current && auth.user?.market_type === "stock"
      && stockResearchScope(auth.user.id, token) === scope && latest.current.jobId === jobId && latest.current.token === token && latest.current.scope === scope
  }
  const current = () => mounted.current && sessionCurrent()
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
      latest.current.onChanged()
    } catch (failure) {
      if (valid()) { setUnknown(true); setError(`保存结果尚未确认，请先查询收藏状态；未自动重写。${failure instanceof Error ? failure.message : "请求失败"}`) }
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
    void query()
    return () => { mounted.current = false; controller.current?.abort(); inflight.current = false }
  }, [jobId, token, scope])
  if (!sessionCurrent()) return null
  const saved = view?.job_id === jobId ? view : null
  return <section aria-label="报告收藏" className="space-y-2 rounded border border-[var(--border)] p-3 text-xs">
    <p>仅保存本人当前登录盘的报告引用，不修改研究结果、不新建任务；收藏不代表策略已验证。</p>
    {loading && <p role="status">读取收藏状态…</p>}
    {error && <p role="alert">{error}</p>}
    <div className="flex gap-4"><button type="button" disabled={loading || writing || unknown || !saved} onClick={() => void toggle()} className="underline disabled:opacity-40">{saved?.bookmarked ? "取消收藏" : "收藏报告"}</button><button type="button" disabled={loading || writing} onClick={() => void query()} className="underline disabled:opacity-40">重新查询收藏状态</button></div>
    {writing && <p role="status">正在保存收藏…</p>}
    {saved?.created_at && !unknown && <p>收藏时间：{saved.created_at}</p>}
  </section>
}
