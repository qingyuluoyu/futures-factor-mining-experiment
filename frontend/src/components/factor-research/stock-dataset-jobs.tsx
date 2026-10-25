"use client"

import { useEffect, useRef, useState } from "react"
import { cancelStockDatasetJob, getStockDatasetJob, getStockDatasetJobAvailability, listStockDatasetJobs, submitStockDatasetJob, type CreateDatasetJob, type DatasetJobDetail, type DatasetJobPage } from "@/lib/stock-dataset-jobs-api"

type Identity = { token: string; scope: string }
type State = { identity: Identity; page: DatasetJobPage | null; offset: number; accepting: boolean; reason: string; loading: boolean; error: string; detail: DatasetJobDetail | null }
const LIMIT = 20
const labels = { pending: "等待执行", running: "执行中", completed: "观察归档已生成", cancelled: "已取消", expired: "已过期", failed: "失败" }
const buttonClass = "rounded border border-[var(--border)] px-2 py-1.5 text-xs disabled:opacity-50"
function initial(identity: Identity): State { return { identity, page: null, offset: 0, accepting: false, reason: "", loading: true, error: "", detail: null } }

export function StockDatasetJobs({ token, scope, command }: { token: string; scope: string; command?: CreateDatasetJob }): React.JSX.Element {
  const identity = useRef<Identity>({ token, scope })
  if (identity.current.token !== token || identity.current.scope !== scope) identity.current = { token, scope }
  const [state, setState] = useState<State>(() => initial(identity.current))
  const current = useRef<AbortController | null>(null)
  const visible = state.identity === identity.current ? state : initial(identity.current)

  function perform(work: (origin: Identity, signal: AbortSignal) => Promise<Partial<State>>) {
    current.current?.abort()
    const controller = new AbortController()
    current.current = controller
    const origin = identity.current
    const patch = (values: Partial<State>) => {
      if (identity.current !== origin || current.current !== controller || controller.signal.aborted) return
      setState((previous) => previous.identity === origin ? { ...previous, ...values } : previous)
    }
    patch({ loading: true, error: "" })
    void (async () => {
      try { patch(await work(origin, controller.signal)) }
      catch (error) { patch({ error: error instanceof Error ? error.message.slice(0, 400) : "观察任务请求失败" }) }
      finally { patch({ loading: false }) }
    })()
  }

  function load(offset = 0) {
    perform(async (origin, signal) => {
      const availability = await getStockDatasetJobAvailability(origin.token, signal)
      const page = await listStockDatasetJobs({ limit: LIMIT, offset }, origin.token, signal)
      return { page, offset, accepting: availability.accepting, reason: availability.reason }
    })
  }

  function inspect(identifier: string) {
    perform(async (origin, signal) => ({ detail: await getStockDatasetJob(identifier, origin.token, signal) }))
  }

  function cancel() {
    if (!visible.detail || !["pending", "running"].includes(visible.detail.status) || visible.loading) return
    const identifier = visible.detail.id
    perform(async (origin, signal) => {
      const detail = await cancelStockDatasetJob(identifier, origin.token, signal)
      return { detail, page: visible.page ? { ...visible.page, items: visible.page.items.map((item) => item.id === identifier ? { ...item, status: detail.status, step: detail.step, finished_at: detail.finished_at, error_code: detail.error_code } : item) } : null }
    })
  }

  function submit() {
    if (!command || !visible.accepting || visible.loading || visible.error) return
    perform(async (origin, signal) => ({ detail: await submitStockDatasetJob(command, origin.token, signal) }))
  }

  useEffect(() => {
    setState(initial(identity.current))
    if (token && scope) load(0)
    return () => current.current?.abort()
  }, [token, scope])

  if (!token || !scope) return <section aria-label="观察数据任务">请先登录A股研究会话</section>
  return <section aria-label="观察数据任务" className="space-y-3 rounded border border-[var(--border)] p-3 text-sm">
    <div className="flex items-center justify-between"><h3>固定观察数据任务</h3><button type="button" className={buttonClass} disabled={visible.loading} onClick={() => load(visible.offset)}>刷新任务</button></div>
    <p>只生成观察归档，不代表历史数据合格，不启动IC、回测或机器学习；不自动轮询或重试。</p>
    {visible.reason && <p>{visible.reason}</p>}
    {visible.loading && <p role="status">正在读取或确认任务…</p>}
    {visible.error && <p role="alert">{visible.error}。提交超时后请保留原幂等键核对，不要换键重复创建。</p>}
    {command && <div className="space-y-2 rounded border p-2"><p>待确认：{command.parameters.pool_id}，暖机{command.parameters.warmup_start}，{command.parameters.start} 至 {command.parameters.end}；{command.parameters.instruments.length}只证券、{command.parameters.source_ids.length}个固定来源。</p>
      <p className="break-all">股票池版本：{command.parameters.pool_version}；幂等键：{command.idempotency_key}</p>
      <button type="button" className={buttonClass} disabled={!visible.accepting || visible.loading || !!visible.error} onClick={submit}>确认创建观察归档任务</button></div>}
    {visible.page && <><p>任务共{visible.page.total}条；不是研究次数额度。</p><ul>{visible.page.items.map((item) => <li key={item.id} className="flex flex-wrap items-center justify-between gap-2 border-b py-2"><span className="break-all">{item.id} · {labels[item.status]}</span><button type="button" className={buttonClass} disabled={visible.loading || !!visible.error} onClick={() => inspect(item.id)}>查看固定参数</button></li>)}</ul>
      <div className="flex gap-2"><button type="button" className={buttonClass} disabled={visible.loading || visible.offset === 0} onClick={() => load(Math.max(0, visible.offset - LIMIT))}>上一页</button><button type="button" className={buttonClass} disabled={visible.loading || !visible.page.has_more || !!visible.error} onClick={() => load(visible.offset + LIMIT)}>下一页</button></div></>}
    {visible.detail && <div className="space-y-2 rounded border p-2"><h4>{labels[visible.detail.status]}</h4><p className="break-all">任务：{visible.detail.id}；请求：{visible.detail.request_id}</p><p>{visible.detail.parameters.pool_id} · {visible.detail.parameters.start} 至 {visible.detail.parameters.end}</p>
      <p className="break-all">固定来源：{visible.detail.parameters.source_ids.join("、")}</p><p className="break-all">股票池版本：{visible.detail.parameters.pool_version}</p>
      {visible.detail.error_code && <p>结束代码：{visible.detail.error_code}</p>}
      {visible.detail.result && <p className="break-all">观察数据集：{visible.detail.result.dataset_id}；版本：{visible.detail.result.data_version}；不可直接回测。</p>}
      <button type="button" className={buttonClass} disabled={visible.loading || !["pending", "running"].includes(visible.detail.status)} onClick={cancel}>取消所选任务</button></div>}
  </section>
}
