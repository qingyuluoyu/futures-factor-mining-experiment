"use client"

import { useEffect, useRef, useState } from "react"
import { useAuthStore } from "@/stores/auth"
import { stockResearchScope } from "@/lib/stock-research-api"
import { downloadStockResearchReport, startResearchReportDownload, type ResearchExportFormat } from "@/lib/stock-report-artifacts"

export function ResearchReportActions({ jobId, token, scope }: { jobId: string; token: string; scope: string }): React.JSX.Element {
  const sessionRevision = useAuthStore((state) => state.sessionRevision)
  const [busy, setBusy] = useState<ResearchExportFormat | null>(null)
  const [message, setMessage] = useState("")
  const [error, setError] = useState("")
  const mounted = useRef(false)
  const controller = useRef<AbortController | null>(null)
  const inflight = useRef(false)
  const current = (revision: number, request: AbortController) => {
    const auth = useAuthStore.getState()
    return mounted.current && controller.current === request && !request.signal.aborted && auth.accessToken === token && auth.sessionRevision === revision && auth.user?.market_type === "stock" && stockResearchScope(auth.user.id, token) === scope
  }
  useEffect(() => {
    mounted.current = true
    inflight.current = false
    setBusy(null); setMessage(""); setError("")
    return () => { mounted.current = false; controller.current?.abort() }
  }, [jobId, token, scope, sessionRevision])

  const download = async (format: ResearchExportFormat) => {
    if (inflight.current) return
    const request = new AbortController()
    controller.current = request
    const revision = useAuthStore.getState().sessionRevision
    if (!current(revision, request)) return
    inflight.current = true
    setBusy(format); setError(""); setMessage("")
    try {
      const file = await downloadStockResearchReport(jobId, format, token, request.signal)
      if (!current(revision, request)) return
      startResearchReportDownload(file)
      setMessage("下载已开始，请检查浏览器下载列表。")
    } catch (failure) {
      if (current(revision, request)) setError(failure instanceof Error ? failure.message : "报告导出失败")
    } finally {
      if (current(revision, request)) { inflight.current = false; setBusy(null) }
    }
  }

  return <section className="space-y-2 rounded border border-[var(--border)] p-3 text-xs">
    <div className="flex flex-wrap gap-3">
      <button type="button" disabled={busy !== null} onClick={() => download("json")} className="underline disabled:opacity-40">导出 JSON{busy === "json" ? "…" : ""}</button>
      <button type="button" disabled={busy !== null} onClick={() => download("csv")} className="underline disabled:opacity-40">导出 CSV{busy === "csv" ? "…" : ""}</button>
    </div>
    <p className="text-[var(--text-muted)]">导出保存的参数、方法、数据版本和明细；原报告若已截断，会保留截断标识，不会重建缺失交易。</p>
    {message && <p role="status">{message}</p>}
    {error && <p role="alert" className="text-[var(--accent-danger)]">{error}</p>}
  </section>
}
