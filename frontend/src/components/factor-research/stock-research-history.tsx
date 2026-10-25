"use client"

import { useEffect, useRef, useState } from "react"
import Link from "next/link"
import { useAuthStore } from "@/stores/auth"
import { getStockResearchAvailability, getStockResearchJob, getStockResearchResult, listStockResearchJobs, listBookmarkedStockResearchJobs, getStockReportBookmarkAvailability, stockResearchScope, type ResearchAvailability, type ResearchJob, type ResearchKind, type ResearchResult } from "@/lib/stock-research-api"
import { ResearchReport } from "./research-report"
import { ResearchJobMetadata, researchStatusLabels } from "./research-job-panel"
import { ResearchReportActions } from "./research-report-actions"
import { ResearchComparisonPanel } from "./research-comparison-panel"
import { ResearchReportBookmark } from "./research-report-bookmark"

export function StockResearchHistory(): React.JSX.Element {
  const userId = useAuthStore((state) => state.user?.id)
  const token = useAuthStore((state) => state.accessToken)
  const revision = useAuthStore((state) => state.sessionRevision)
  const scope = stockResearchScope(userId, token)
  if (!scope || !token) return <p role="status" className="p-4">正在验证研究会话…</p>
  return <StockResearchHistoryWorkspace key={`${scope}:${token}:${revision}`} scope={scope} token={token} />
}

export function StockResearchHistoryWorkspace({ token, scope }: { token: string; scope: string }): React.JSX.Element {
  const [availability, setAvailability] = useState<ResearchAvailability | null>(null)
  const [items, setItems] = useState<ResearchJob[]>([])
  const [total, setTotal] = useState(0)
  const [hasMore, setHasMore] = useState(false)
  const [offset, setOffset] = useState(0)
  const [kind, setKind] = useState<ResearchKind | "">("")
  const [status, setStatus] = useState<ResearchJob["status"] | "">("")
  const [bookmarksOnly, setBookmarksOnly] = useState(false)
  const [bookmarkCapability, setBookmarkCapability] = useState<{ enabled: boolean; reason: string | null } | null>(null)
  const [bookmarkCapabilityError, setBookmarkCapabilityError] = useState("")
  const [bookmarkCapabilityRevision, setBookmarkCapabilityRevision] = useState(0)
  const [loadedQuery, setLoadedQuery] = useState("")
  const [selected, setSelected] = useState<ResearchJob | null>(null)
  const [detailId, setDetailId] = useState<string | null>(null)
  const [report, setReport] = useState<ResearchResult | null>(null)
  const [loading, setLoading] = useState(true)
  const [detailLoading, setDetailLoading] = useState(false)
  const [error, setError] = useState("")
  const [detailError, setDetailError] = useState("")
  const [comparison, setComparison] = useState<ResearchJob[]>([])
  const sessionRevision = useRef(useAuthStore.getState().sessionRevision)
  const mounted = useRef(false)
  const listController = useRef<AbortController | null>(null)
  const detailController = useRef<AbortController | null>(null)
  const requestVersion = useRef(0)
  const queryKey = `${offset}:${kind}:${status}:${bookmarksOnly}`
  const listVisible = loadedQuery === queryKey && !loading && !error
  const current = () => {
    const auth = useAuthStore.getState()
    return mounted.current && auth.accessToken === token && auth.sessionRevision === sessionRevision.current && auth.user?.market_type === "stock" && stockResearchScope(auth.user.id, token) === scope
  }
  const load = async () => {
    listController.current?.abort()
    const controller = new AbortController()
    listController.current = controller
    setLoading(true)
    setError("")
    setItems([])
    setTotal(0)
    setHasMore(false)
    try {
      const available = await getStockResearchAvailability(token, controller.signal)
      if (!current() || controller.signal.aborted) return
      setAvailability(available)
      if (!available.enabled) { detailController.current?.abort(); requestVersion.current++; setDetailLoading(false); setDetailError(""); setDetailId(null); setSelected(null); setReport(null); setComparison([]); return }
      const list = bookmarksOnly ? listBookmarkedStockResearchJobs : listStockResearchJobs
      const page = await list({ limit: 20, offset, ...(kind ? { kind } : {}), ...(status ? { status } : {}) }, token, controller.signal)
      if (!current() || controller.signal.aborted) return
      setItems(page.items)
      setTotal(page.total)
      setHasMore(page.has_more)
      setLoadedQuery(queryKey)
    } catch (failure) {
      if (current() && !controller.signal.aborted) setError(failure instanceof Error ? failure.message : "历史报告加载失败")
    } finally { if (current() && !controller.signal.aborted) setLoading(false) }
  }
  const view = async (id: string) => {
    if (!current()) return
    detailController.current?.abort()
    const controller = new AbortController()
    detailController.current = controller
    const version = ++requestVersion.current
    setDetailId(id)
    setSelected(null)
    setReport(null)
    setDetailError("")
    setDetailLoading(true)
    try {
      const job = await getStockResearchJob(id, token, controller.signal)
      if (!current() || controller.signal.aborted || version !== requestVersion.current) return
      setSelected(job)
      if (job.status === "completed" && job.has_result) {
        const result = await getStockResearchResult(id, job.kind, token, controller.signal)
        if (current() && !controller.signal.aborted && version === requestVersion.current) setReport(result)
      }
    } catch (failure) {
      if (current() && !controller.signal.aborted && version === requestVersion.current) {
        if ([400, 401, 403, 404, 422].includes((failure as { status?: number }).status ?? 0)) { setDetailId(null); setSelected(null); setReport(null) }
        setDetailError(failure instanceof Error ? failure.message : "报告详情加载失败")
      }
    } finally { if (current() && version === requestVersion.current && !controller.signal.aborted) setDetailLoading(false) }
  }
  const closeDetail = () => {
    if (!current()) return
    detailController.current?.abort()
    requestVersion.current++
    setDetailId(null)
    setSelected(null)
    setReport(null)
    setDetailError("")
    setDetailLoading(false)
  }
  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false; listController.current?.abort(); detailController.current?.abort() }
  }, [token, scope])
  useEffect(() => {
    void load()
    return () => listController.current?.abort()
  }, [token, scope, offset, kind, status, bookmarksOnly])
  useEffect(() => {
    const controller = new AbortController()
    setBookmarkCapabilityError("")
    const load = async () => {
      try {
        const capability = await getStockReportBookmarkAvailability(token, controller.signal)
        if (current() && !controller.signal.aborted) setBookmarkCapability(capability)
      } catch (failure) {
        if (current() && !controller.signal.aborted) setBookmarkCapabilityError(failure instanceof Error ? failure.message : "收藏功能检查失败")
      }
    }
    void load()
    return () => controller.abort()
  }, [token, scope, bookmarkCapabilityRevision])

  const toggleComparison = (job: ResearchJob) => {
    if (!current() || job.status !== "completed" || !job.has_result) return
    setComparison((saved) => saved.some((item) => item.id === job.id) ? saved.filter((item) => item.id !== job.id) : saved.length < 2 && (!saved[0] || saved[0].kind === job.kind) ? [...saved, job] : saved)
  }

  return <main className="space-y-4 p-4 text-[var(--text-primary)]">
    <div className="flex flex-wrap items-center gap-4">
      <h1 className="text-lg font-semibold">A 股历史研究报告</h1>
      <Link href="/factor-research" className="text-xs underline">创建研究任务</Link>
      <button type="button" disabled={loading} onClick={() => void load()} className="text-xs underline disabled:opacity-50">刷新历史报告</button>
      <label className="text-xs">研究类型 <select aria-label="研究类型" value={kind} onChange={(event) => { setKind(event.target.value as ResearchKind | ""); setOffset(0) }} className="rounded border border-[var(--border)] bg-[var(--bg-secondary)] p-1"><option value="">全部</option><option value="analysis">因子检验</option><option value="backtest">组合回测</option></select></label>
      <label className="text-xs">任务状态 <select aria-label="任务状态" value={status} onChange={(event) => { setStatus(event.target.value as ResearchJob["status"] | ""); setOffset(0) }} className="rounded border border-[var(--border)] bg-[var(--bg-secondary)] p-1"><option value="">全部状态</option>{Object.entries(researchStatusLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      {(bookmarkCapability?.enabled || bookmarksOnly) && <label className="flex items-center gap-1 text-xs"><input type="checkbox" aria-label="仅收藏报告" checked={bookmarksOnly} onChange={(event) => { setBookmarksOnly(event.target.checked); setOffset(0) }} />仅收藏报告（按收藏时间排序）</label>}
    </div>
    <p className="text-xs text-[var(--text-muted)]">报告按当前用户和登录盘隔离；研究结果均待验证，不代表可实盘策略。失败、取消和过期任务也会保留。筛选和翻页不改变已打开的详情与对照选择。</p>
    {loading && <p role="status" className="text-xs">加载历史报告…</p>}
    {error && <p role="alert" className="text-xs text-[var(--accent-danger)]">{error}</p>}
    {availability && !availability.enabled && <p role="status" className="text-xs">研究任务未启用：{availability.reason || "管理员尚未开启服务"}</p>}
    {availability?.enabled && <>
      {(bookmarkCapabilityError || bookmarkCapability?.reason) && <p role="status" className="text-xs">收藏功能：{bookmarkCapabilityError || bookmarkCapability?.reason} <button type="button" onClick={() => setBookmarkCapabilityRevision((value) => value + 1)} className="underline">重新检查收藏功能</button>；原报告查看不受影响。</p>}
      {listVisible && items.length === 0 && (offset > 0 ? <div className="space-y-2 text-sm"><p>当前页没有研究任务，结果可能已变化。可返回第一页核对，原筛选条件保留。</p><button type="button" onClick={() => setOffset(0)} className="text-xs underline">返回第一页报告</button></div> : <p className="text-sm">当前筛选条件下暂无研究任务。</p>)}
      <p className="text-xs text-[var(--text-muted)]">{availability.saved_report_artifacts ? "勾选两份同类型的已完成报告进行对照；选择可跨分页保留，最多两份。" : "当前服务尚未部署报告导出与对照；可继续查看报告及恢复配置。"}</p>
      <div className="space-y-2">{(listVisible ? items : []).map((job) => {
        const checked = comparison.some((item) => item.id === job.id)
        return <div key={job.id} className="flex items-start gap-3 rounded border border-[var(--border)] bg-[var(--bg-secondary)] p-3 text-xs">
          {availability.saved_report_artifacts && <input type="checkbox" aria-label={`选择对照 ${job.id}`} checked={checked} disabled={!checked && (job.status !== "completed" || !job.has_result || comparison.length >= 2 || Boolean(comparison[0] && comparison[0].kind !== job.kind))} onChange={() => toggleComparison(job)} className="mt-1" />}
          <button type="button" onClick={() => view(job.id)} className="min-w-0 flex-1 text-left"><span className="font-medium">{job.title}</span> · {job.kind === "analysis" ? "因子检验" : "组合回测"} · {researchStatusLabels[job.status]} · {job.progress}%<span className="mt-1 block break-all text-[var(--text-muted)]">{job.created_at} · {job.id}</span>{job.error && <span className="mt-1 block text-[var(--accent-danger)]">{job.error.code}：{job.error.message}</span>}</button>
        </div>
      })}</div>
      {availability.saved_report_artifacts && comparison.length > 0 && <ResearchComparisonPanel leftId={comparison[0]?.id} rightId={comparison[1]?.id} token={token} scope={scope} onClear={() => setComparison([])} />}
      <div className="flex items-center gap-4 text-xs"><button type="button" disabled={loading || offset === 0} onClick={() => setOffset(Math.max(0, offset - 20))} className="underline disabled:opacity-40">上一页</button><span>第 {Math.floor(offset / 20) + 1} 页{listVisible ? ` · 共 ${total} 个任务` : " · 总数待核对"}</span><button type="button" disabled={!listVisible || !hasMore || offset >= 10000} onClick={() => setOffset(offset + 20)} className="underline disabled:opacity-40">下一页</button></div>
    </>}
    {(detailId || selected || detailLoading || detailError) && <button type="button" onClick={closeDetail} className="text-xs underline">关闭报告详情</button>}
    {detailLoading && <p role="status" className="text-xs">加载报告详情…</p>}
    {detailError && <p role="alert" className="text-xs text-[var(--accent-danger)]">{detailError}{detailId && !selected && <button type="button" onClick={() => void view(detailId)} className="ml-3 underline">重试报告详情</button>}</p>}
    {selected && <div className="space-y-4"><ResearchJobMetadata job={selected} /><div className="flex gap-4 text-xs"><button type="button" onClick={() => void view(selected.id)} disabled={detailLoading} className="underline disabled:opacity-40">刷新此任务</button><Link href={`${selected.kind === "backtest" ? "/backtest" : "/factor-research"}?restore=${selected.id}`} className="underline">恢复配置到新研究</Link></div>{selected.error && <p role="alert" className="text-xs">{selected.error.code}：{selected.error.message}</p>}{bookmarkCapability?.enabled && selected.status === "completed" && selected.has_result && <ResearchReportBookmark key={selected.id} jobId={selected.id} token={token} scope={scope} onChanged={() => { if (bookmarksOnly) void load() }} />}{availability?.saved_report_artifacts && selected.status === "completed" && selected.has_result && <ResearchReportActions jobId={selected.id} token={token} scope={scope} />}{report && <ResearchReport job={selected} result={report} />}</div>}
  </main>
}
