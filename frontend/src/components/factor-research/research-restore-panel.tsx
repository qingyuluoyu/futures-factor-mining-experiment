"use client"

import { useEffect, useRef, useState } from "react"
import Link from "next/link"
import { useSearchParams } from "next/navigation"
import { z } from "zod"
import { getStockResearchJob, researchCreateSchema, stockResearchScope, type ResearchJob, type ResearchKind } from "@/lib/stock-research-api"
import { useAuthStore } from "@/stores/auth"

export type ResearchRestoreDraft =
  | Pick<Extract<z.output<typeof researchCreateSchema>, { kind: "analysis" }>, "kind" | "parameters">
  | Pick<Extract<z.output<typeof researchCreateSchema>, { kind: "backtest" }>, "kind" | "parameters">

type Props = {
  token: string
  scope: string
  factors: ReadonlyArray<{ id: string }>
  catalogLoading: boolean
  catalogError?: string
  blocked: boolean
  expectedKind?: ResearchKind
  onApply: (draft: ResearchRestoreDraft) => void
}

function validatedDraft(job: ResearchJob, factors: Props["factors"]) {
  const parsed = researchCreateSchema.safeParse({ idempotency_key: job.id, kind: job.kind, parameters: job.params })
  if (!parsed.success) throw new Error("保存参数不符合当前研究契约，不能自动回填，请手动核对。")
  const ids = parsed.data.kind === "analysis" ? [parsed.data.parameters.factor_id] : parsed.data.parameters.factor_ids
  if (ids.some((id) => !factors.some((factor) => factor.id === id))) throw new Error("历史因子已失效或不在当前因子目录中，不能回填。")
  return parsed.data.kind === "analysis"
    ? { kind: parsed.data.kind, parameters: parsed.data.parameters }
    : { kind: parsed.data.kind, parameters: parsed.data.parameters }
}

export function ResearchRestorePanel(props: Props): React.JSX.Element | null {
  const { token, scope, factors, catalogLoading, catalogError, blocked, expectedKind } = props
  const sessionRevision = useAuthStore((state) => state.sessionRevision)
  const capturedSessionRevision = useRef(sessionRevision)
  const restoreId = useSearchParams().get("restore")
  const [state, setState] = useState<{ id: string; job: ResearchJob | null; loading: boolean; error: string }>({ id: "", job: null, loading: false, error: "" })
  const [retry, setRetry] = useState(0)
  const [applied, setApplied] = useState(false)
  const request = useRef<AbortController | null>(null)
  const latest = useRef({ ...props, restoreId })
  latest.current = { ...props, restoreId }
  const current = () => {
    const auth = useAuthStore.getState()
    return auth.accessToken === token && auth.sessionRevision === capturedSessionRevision.current && auth.user?.market_type === "stock" && stockResearchScope(auth.user.id, token) === scope && latest.current.token === token && latest.current.scope === scope && latest.current.restoreId === restoreId
  }

  useEffect(() => {
    const controller = new AbortController()
    request.current = controller
    setApplied(false)
    const valid = () => current() && request.current === controller && !controller.signal.aborted
    if (restoreId === null) return () => controller.abort()
    if (!z.uuid().safeParse(restoreId).success) {
      setState({ id: restoreId, job: null, loading: false, error: "历史研究ID无效，请从历史报告重新打开。" })
      return () => controller.abort()
    }
    setState({ id: restoreId, job: null, loading: true, error: "" })
    const load = async () => {
      try {
        if (!valid()) return
        const job = await getStockResearchJob(restoreId, token, controller.signal)
        if (!valid()) return
        if (job.id !== restoreId) throw new Error("保存研究与请求ID不匹配。")
        setState({ id: restoreId, job, loading: false, error: "" })
      } catch (error) {
        if (!valid()) return
        setState({ id: restoreId, job: null, loading: false, error: (error as { status?: number })?.status === 404 ? "历史研究不存在或当前账户/交易模式无权访问。" : error instanceof Error ? error.message : "历史配置加载失败" })
      }
    }
    void load()
    return () => controller.abort()
  }, [restoreId, token, scope, sessionRevision, retry])

  if (restoreId === null || !current()) return null
  const loaded = state.id === restoreId && !state.loading
  const job = loaded ? state.job : null
  const kindMismatch = Boolean(job && expectedKind && job.kind !== expectedKind)
  let validationError = ""
  if (job && !kindMismatch && !catalogLoading && !catalogError) {
    try { validatedDraft(job, factors) }
    catch (error) { validationError = error instanceof Error ? error.message : "保存参数无法恢复" }
  }
  const apply = () => {
    const snapshot = latest.current
    if (!current() || !job || request.current?.signal.aborted || snapshot.blocked || snapshot.catalogLoading || snapshot.catalogError || (snapshot.expectedKind && job.kind !== snapshot.expectedKind)) return
    try {
      snapshot.onApply(validatedDraft(job, snapshot.factors))
      setApplied(true)
    } catch (error) {
      setState({ id: restoreId, job, loading: false, error: error instanceof Error ? error.message : "保存参数无法恢复" })
    }
  }
  return (
    <section aria-label="历史配置回填" className="mb-4 space-y-2 rounded-lg border border-[var(--border)] p-3 text-xs">
      <h2 className="font-semibold">历史配置 → 新研究表单</h2>
      <p>仅复用配置。新研究使用提交时的当前方法与数据；历史方法不会随配置恢复，旧方法、不可变输入及 PIT 尚未认证，不能据此视为同版本复算。</p>
      {!loaded && <p role="status">历史配置加载中…</p>}
      {state.id === restoreId && state.error && <p role="alert">{state.error}</p>}
      {loaded && !job && state.error && <button type="button" onClick={() => setRetry((value) => value + 1)} className="underline">重新加载历史配置</button>}
      {job && <>
        <p>来源任务：{job.id} · 原方法：{job.method_version || "未记录"} · 原流程：{job.workflow_version || "未记录"}</p>
        <pre aria-label="保存研究参数" className="max-h-48 overflow-auto whitespace-pre-wrap break-all">{JSON.stringify(job.params, null, 2)}</pre>
        {kindMismatch && <p role="status">此配置属于{job.kind === "backtest" ? "组合回测" : "因子检验"}，请前往对应页面恢复；当前草稿不会被替换。<Link href={`${job.kind === "backtest" ? "/backtest" : "/factor-research"}?restore=${job.id}`} className="ml-2 underline">打开对应研究页面</Link></p>}
        {!kindMismatch && catalogLoading && <p role="status">正在核对当前因子目录…</p>}
        {(validationError || catalogError) && <p role="alert">{validationError || catalogError}</p>}
        {blocked && <p role="status">已有待执行、运行中或提交结果未知的任务，请先确认任务状态后再回填。</p>}
        {!kindMismatch && <button type="button" disabled={blocked || catalogLoading || Boolean(catalogError) || Boolean(validationError)} onClick={apply} className="underline disabled:opacity-50">应用到新研究表单</button>}
        {!kindMismatch && applied && <p role="status">已应用到表单并重新检查数据覆盖。请核对参数后，另行点击开始研究；本操作未创建任务。</p>}
      </>}
    </section>
  )
}
