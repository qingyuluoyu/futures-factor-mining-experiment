"use client"

import { useEffect, useRef, useState } from "react"
import { cancelStockDatasetJob, getStockDatasetJob, getStockDatasetJobAvailability, listStockDatasetJobs, submitStockDatasetJob, type CreateDatasetJob, type DatasetJobDetail, type DatasetJobPage } from "@/lib/stock-dataset-jobs-api"
import { clearPendingWrite, readPendingWrites, recordPendingWrite, type PendingWriteEntry } from "@/lib/stock-pending-writes"

type Identity = { token: string; scope: string }
type State = { identity: Identity; page: DatasetJobPage | null; offset: number; accepting: boolean; reason: string; loading: boolean; error: string; detail: DatasetJobDetail | null; retry: CreateDatasetJob | null; unregistered: boolean }
const LIMIT = 20
const labels = { pending: "等待执行", running: "执行中", completed: "观察归档已生成", cancelled: "已取消", expired: "已过期", failed: "失败" }
const buttonClass = "rounded border border-[var(--border)] px-2 py-1.5 text-xs disabled:opacity-50"
/** 本组件登记的未确认写类型；重试必须用签名里的**原**幂等键，服务端据此去重。 */
const PENDING_KIND = "dataset-job"

/** 从条目签名还原原始提交命令；签名不可解析或形状不对时返回 null，绝不猜一个命令出来。 */
function pendingCommand(entry: PendingWriteEntry): CreateDatasetJob | null {
  try {
    const parsed: unknown = JSON.parse(entry.signature)
    if (!parsed || typeof parsed !== "object") return null
    const value = parsed as { idempotency_key?: unknown; parameters?: { instruments?: unknown; source_ids?: unknown } }
    if (typeof value.idempotency_key !== "string" || !value.parameters || typeof value.parameters !== "object") return null
    if (!Array.isArray(value.parameters.instruments) || !Array.isArray(value.parameters.source_ids)) return null
    return parsed as CreateDatasetJob
  } catch {
    return null
  }
}

/** 取最近一条可还原的未确认提交；换页回来（没有 command）也据此提供重试。 */
function latestPendingJob(scope: string): CreateDatasetJob | null {
  const entries = readPendingWrites(scope).filter((entry) => entry.kind === PENDING_KIND)
  for (let index = entries.length - 1; index >= 0; index -= 1) {
    const command = pendingCommand(entries[index])
    if (command) return command
  }
  return null
}

function initial(identity: Identity): State {
  return { identity, page: null, offset: 0, accepting: false, reason: "", loading: true, error: "", detail: null,
    retry: identity.scope ? latestPendingJob(identity.scope) : null, unregistered: false }
}

export function StockDatasetJobs({ token, scope, command }: { token: string; scope: string; command?: CreateDatasetJob }): React.JSX.Element {
  const identity = useRef<Identity>({ token, scope })
  if (identity.current.token !== token || identity.current.scope !== scope) identity.current = { token, scope }
  const [state, setState] = useState<State>(() => initial(identity.current))
  const current = useRef<AbortController | null>(null)
  const visible = state.identity === identity.current ? state : initial(identity.current)

  /** 与旧版一致地回报成败，供提交方决定是否清除未确认写。 */
  function perform(work: (origin: Identity, signal: AbortSignal) => Promise<Partial<State>>): Promise<boolean> {
    current.current?.abort()
    const controller = new AbortController()
    current.current = controller
    const origin = identity.current
    const patch = (values: Partial<State>) => {
      if (identity.current !== origin || current.current !== controller || controller.signal.aborted) return
      setState((previous) => previous.identity === origin ? { ...previous, ...values } : previous)
    }
    patch({ loading: true, error: "" })
    return (async () => {
      try { patch(await work(origin, controller.signal)); return true }
      catch (error) { patch({ error: error instanceof Error ? error.message.slice(0, 400) : "观察任务请求失败" }); return false }
      finally { patch({ loading: false }) }
    })()
  }

  function load(offset = 0) {
    void perform(async (origin, signal) => {
      const availability = await getStockDatasetJobAvailability(origin.token, signal)
      const page = await listStockDatasetJobs({ limit: LIMIT, offset }, origin.token, signal)
      return { page, offset, accepting: availability.accepting, reason: availability.reason }
    })
  }

  function inspect(identifier: string) {
    void perform(async (origin, signal) => ({ detail: await getStockDatasetJob(identifier, origin.token, signal) }))
  }

  function cancel() {
    if (!visible.detail || !["pending", "running"].includes(visible.detail.status) || visible.loading) return
    const identifier = visible.detail.id
    void perform(async (origin, signal) => {
      const detail = await cancelStockDatasetJob(identifier, origin.token, signal)
      return { detail, page: visible.page ? { ...visible.page, items: visible.page.items.map((item) => item.id === identifier ? { ...item, status: detail.status, step: detail.step, finished_at: detail.finished_at, error_code: detail.error_code } : item) } : null }
    })
  }

  /** 发出请求前先按原键登记；读回核实，登记被静默丢弃（如参数超出单字段上限）时不承诺可重试。 */
  function register(job: CreateDatasetJob): void {
    recordPendingWrite(scope, { kind: PENDING_KIND, idempotency_key: job.idempotency_key,
      signature: JSON.stringify(job), created_at: new Date().toISOString() })
    const registered = readPendingWrites(scope).some((entry) => entry.kind === PENDING_KIND && entry.idempotency_key === job.idempotency_key)
    setState((previous) => previous.identity === identity.current
      ? { ...previous, retry: registered ? job : previous.retry, unregistered: !registered } : previous)
  }

  /** ``retry`` 为重发原请求：未知结果的错误提示仍在也放行，否则一旦出错就再也提交不了。 */
  function submit(candidate: CreateDatasetJob | undefined, retry: boolean) {
    if (!candidate || !visible.accepting || visible.loading) return
    if (!retry && visible.error) return
    const job = candidate
    register(job)
    void (async () => {
      const settled = await perform(async (origin, signal) => ({ detail: await submitStockDatasetJob(job, origin.token, signal) }))
      if (!settled) return
      clearPendingWrite(scope, job.idempotency_key)
      setState((previous) => previous.identity === identity.current ? { ...previous, retry: null, unregistered: false } : previous)
    })()
  }

  useEffect(() => {
    setState(initial(identity.current))
    if (token && scope) load(0)
    return () => current.current?.abort()
  }, [token, scope])

  if (!token || !scope) return <section aria-label="观察数据任务">请先登录A股研究会话</section>
  // 命令区自己的提交入口在「有错误提示」时被禁用；换页回来没有 command 时更没有入口。
  // 这两种情况都必须另有一条重试原请求的路，否则未知结果一来就成了死路。
  const retryable = visible.retry && (!command || command.idempotency_key !== visible.retry.idempotency_key || !!visible.error) ? visible.retry : null
  return <section aria-label="观察数据任务" className="space-y-3 rounded border border-[var(--border)] p-3 text-sm">
    <div className="flex items-center justify-between"><h3>固定观察数据任务</h3><button type="button" className={buttonClass} disabled={visible.loading} onClick={() => load(visible.offset)}>刷新任务</button></div>
    <p>只生成观察归档，不代表历史数据合格，不启动IC、回测或机器学习；不自动轮询或重试。</p>
    {visible.reason && <p>{visible.reason}</p>}
    {visible.loading && <p role="status">正在读取或确认任务…</p>}
    {visible.error && <p role="alert">{visible.error}。提交超时后请保留原幂等键核对，不要换键重复创建。</p>}
    {visible.unregistered && <p role="alert">本次提交无法登记跨页重试（参数过大或字段超限）。请保留当前幂等键，不要换键重复创建。</p>}
    {retryable && <div className="space-y-2 rounded border border-dashed p-2"><p>有一次观察任务提交结果未确认（{retryable.parameters.pool_id}，{retryable.parameters.start} 至 {retryable.parameters.end}）。不会自动重发。</p>
      <p className="break-all">原幂等键：{retryable.idempotency_key}</p>
      <button type="button" className={buttonClass} disabled={!visible.accepting || visible.loading} onClick={() => submit(retryable, true)}>用原幂等键重试</button></div>}
    {command && <div className="space-y-2 rounded border p-2"><p>待确认：{command.parameters.pool_id}，暖机{command.parameters.warmup_start}，{command.parameters.start} 至 {command.parameters.end}；{command.parameters.instruments.length}只证券、{command.parameters.source_ids.length}个固定来源。</p>
      <p className="break-all">股票池版本：{command.parameters.pool_version}；幂等键：{command.idempotency_key}</p>
      <button type="button" className={buttonClass} disabled={!visible.accepting || visible.loading || !!visible.error} onClick={() => submit(command, false)}>确认创建观察归档任务</button></div>}
    {visible.page && <><p>任务共{visible.page.total}条；不是研究次数额度。</p><ul>{visible.page.items.map((item) => <li key={item.id} className="flex flex-wrap items-center justify-between gap-2 border-b py-2"><span className="break-all">{item.id} · {labels[item.status]}</span><button type="button" className={buttonClass} disabled={visible.loading || !!visible.error} onClick={() => inspect(item.id)}>查看固定参数</button></li>)}</ul>
      <div className="flex gap-2"><button type="button" className={buttonClass} disabled={visible.loading || visible.offset === 0} onClick={() => load(Math.max(0, visible.offset - LIMIT))}>上一页</button><button type="button" className={buttonClass} disabled={visible.loading || !visible.page.has_more || !!visible.error} onClick={() => load(visible.offset + LIMIT)}>下一页</button></div></>}
    {visible.detail && <div className="space-y-2 rounded border p-2"><h4>{labels[visible.detail.status]}</h4><p className="break-all">任务：{visible.detail.id}；请求：{visible.detail.request_id}</p><p>{visible.detail.parameters.pool_id} · {visible.detail.parameters.start} 至 {visible.detail.parameters.end}</p>
      <p className="break-all">固定来源：{visible.detail.parameters.source_ids.join("、")}</p><p className="break-all">股票池版本：{visible.detail.parameters.pool_version}</p>
      {visible.detail.error_code && <p>结束代码：{visible.detail.error_code}</p>}
      {visible.detail.result && <p className="break-all">观察数据集：{visible.detail.result.dataset_id}；版本：{visible.detail.result.data_version}；不可直接回测。</p>}
      <button type="button" className={buttonClass} disabled={visible.loading || !["pending", "running"].includes(visible.detail.status)} onClick={cancel}>取消所选任务</button></div>}
  </section>
}
