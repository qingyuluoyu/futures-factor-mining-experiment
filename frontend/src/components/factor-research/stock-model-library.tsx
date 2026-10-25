"use client"

import { useEffect, useRef, useState } from "react"
import { getStockModelAvailability, getStockModel, getStockModelReport, listStockModels, type StockModelAvailability, type StockModelDetail, type StockModelPage, type StockModelReport } from "@/lib/stock-ml-models-api"
import { stockResearchScope } from "@/lib/stock-research-api"
import { useAuthStore } from "@/stores/auth"

type Identity = { token: string; scope: string }
type Command = { kind: "availability"; offset: number } | { kind: "list"; offset: number } | { kind: "detail"; id: string } | { kind: "report"; id: string }
type LibraryState = { identity: Identity; busy: boolean; availability: StockModelAvailability | null; page: StockModelPage | null; offset: number; detail: StockModelDetail | null; report: StockModelReport | null; error: string; retry: Command | null }
const LIMIT = 20
const buttonClass = "cursor-pointer rounded border border-[var(--border)] px-2 py-1.5 text-xs disabled:cursor-not-allowed disabled:opacity-50"
function empty(identity: Identity): LibraryState { return { identity, busy: false, availability: null, page: null, offset: 0, detail: null, report: null, error: "", retry: null } }

export function StockModelLibrary({ token, scope }: { token: string; scope: string }): React.JSX.Element {
  const identity = useRef<Identity>({ token, scope })
  if (identity.current.token !== token || identity.current.scope !== scope) identity.current = { token, scope }
  const [state, setState] = useState<LibraryState>(() => empty(identity.current))
  const active = useRef<AbortController | null>(null)
  const nonce = useRef(0)
  const openedIdentity = useRef(identity.current)
  const sessionRevision = useRef(useAuthStore.getState().sessionRevision)
  function sessionCurrent(origin: Identity) {
    const auth = useAuthStore.getState()
    return auth.loaded && auth.accessToken === origin.token && auth.sessionRevision === sessionRevision.current && auth.user?.market_type === "stock" && stockResearchScope(auth.user.id, origin.token) === origin.scope
  }
  function patch(origin: Identity, values: Partial<LibraryState>) {
    if (identity.current === origin) setState((previous) => previous.identity === origin ? { ...previous, ...values } : previous)
  }
  function cancel() { nonce.current++; active.current?.abort(); active.current = null }
  function run(command: Command) {
    if (active.current || !sessionCurrent(identity.current)) return
    const origin = identity.current, controller = new AbortController(), sequence = ++nonce.current
    active.current = controller
    const valid = () => identity.current === origin && nonce.current === sequence && !controller.signal.aborted && sessionCurrent(origin)
    const apply = (values: Partial<LibraryState>) => { if (valid()) patch(origin, values) }
    patch(origin, { busy: true, error: "", retry: null, report: null, ...(command.kind !== "report" ? { detail: null } : {}) })
    void (async () => {
      try {
        if (command.kind === "availability" || command.kind === "list") {
          if (command.kind === "availability") {
            const availability = await getStockModelAvailability(origin.token, controller.signal)
            if (!valid()) return
            apply({ availability })
            if (!availability.listing_enabled) { apply({ page: null, detail: null, report: null }); return }
          }
          const page = await listStockModels({ limit: LIMIT, offset: command.offset }, origin.token, controller.signal)
          apply({ page, offset: command.offset })
        } else if (command.kind === "detail") {
          const detail = await getStockModel(command.id, origin.token, controller.signal, origin.scope)
          apply({ detail })
        } else {
          const report = await getStockModelReport(command.id, origin.token, controller.signal)
          apply({ report })
        }
      } catch (error) {
        if (!valid()) return
        const failure = error as { status?: number; retryable?: boolean; requestId?: string; name?: string }
        const denied = failure.status !== undefined && [401, 403, 404].includes(failure.status)
        const retryable = failure.status !== undefined ? [408, 429, 500, 502, 503, 504].includes(failure.status) && failure.retryable !== false
          : failure.retryable === true || error instanceof TypeError || failure.name === "TimeoutError"
        const message = error instanceof Error ? error.message : "模型历史读取失败"
        apply({ error: `${message}${failure.requestId ? `（请求号：${failure.requestId}）` : ""}${denied ? "。原模型详情已清理，请重新读取本人模型。" : ""}`, retry: !denied && retryable ? command : null,
          ...(denied ? { detail: null, report: null, page: null, availability: null } : {}) })
      } finally {
        if (valid()) patch(origin, { busy: false })
        if (active.current === controller) active.current = null
      }
    })()
  }
  useEffect(() => {
    const origin = identity.current
    setState(empty(origin))
    if (openedIdentity.current === origin) run({ kind: "availability", offset: 0 })
    return cancel
  }, [token, scope])

  if (state.identity !== identity.current || !sessionCurrent(identity.current)) return <p role="status">正在清理旧会话的模型历史，请在当前A股会话重新打开…</p>
  const detail = state.detail, page = state.page
  return <section aria-label="本人A股模型历史" className="mb-4 space-y-3 rounded-lg border border-[var(--border)] p-3 text-xs">
    <h2 className="font-semibold">本人模型历史 · LightGBM排序</h2>
    <p className="leading-6 text-[var(--text-muted)]">普通聊天不属于机器学习训练。这里只读取已登记模型；未开放训练。score是排序分数，不是收益或上涨概率；特征重要性不代表因果。</p>
    <p>测试范围未封存，QA模型尚未完成真实数据验证，结果仅用于研究，不能据此认定样本外有效。</p>
    <button type="button" className={buttonClass} disabled={state.busy} onClick={() => run({ kind: "availability", offset: state.offset })}>重新读取本人模型</button>
    {state.busy && <p role="status">正在读取模型历史…</p>}
    {state.error && <p role="alert" className="text-[var(--accent-danger)]">{state.error}{page ? "；下方仍是上次已读取的列表。" : ""}</p>}
    {state.retry && <button type="button" className={buttonClass} disabled={state.busy} onClick={() => { if (state.retry) run(state.retry) }}>重试原请求</button>}
    {state.availability && !state.availability.listing_enabled && <p role="status">{state.availability.reason}。本人模型历史未开放，未读取模型列表。</p>}
    {!state.availability && !state.busy && !state.error && <p role="status">会话已切换，请重新读取本人模型能力。</p>}
    {state.availability?.listing_enabled && page && <>
      <p>共 {page.total} 个已登记模型 · 第 {Math.floor(state.offset / LIMIT) + 1} 页{page.items.length > 0 ? ` · ${state.offset + 1}–${state.offset + page.items.length}` : ""}（每页{LIMIT}个）。</p>
      {page.items.length === 0 && <p role="status">本页暂无模型{state.offset > 0 ? "，可返回第一页核对当前记录。" : "，尚无本人已登记模型。"}</p>}
      <ul className="space-y-2">{page.items.map((model) => <li key={model.id} className="space-y-1 rounded border border-[var(--border)] p-2">
        <p className="break-all">模型 {model.id} · {model.created_at} · {model.fold_count}个窗口 · {model.validation_status === "qa_only" ? "QA验证，未经认证" : "仅研究"} · {model.state === "quarantined" ? "隔离，仅查看登记信息" : "已登记"}</p>
        <p>特征：{model.feature_names.join("、")}</p>
        <button type="button" className={buttonClass} disabled={state.busy} onClick={() => run({ kind: "detail", id: model.id })}>查看模型详情</button>
      </li>)}</ul>
      <div className="flex flex-wrap gap-2">
        <button type="button" className={buttonClass} disabled={state.busy || state.offset === 0} onClick={() => run({ kind: "list", offset: Math.max(0, state.offset - LIMIT) })}>模型上一页</button>
        <button type="button" className={buttonClass} disabled={state.busy || !page.has_more || state.offset + LIMIT > 100000} onClick={() => run({ kind: "list", offset: state.offset + LIMIT })}>模型下一页</button>
        {state.offset > 0 && <button type="button" className={buttonClass} disabled={state.busy} onClick={() => run({ kind: "list", offset: 0 })}>返回模型第一页</button>}
        {page.has_more && state.offset + LIMIT > 100000 && <p>已达可浏览范围，请返回第一页。</p>}
      </div>
    </>}
    {detail && <article aria-label="模型详情" className="space-y-3 rounded border border-[var(--border)] p-3">
      <div className="flex flex-wrap items-center gap-2"><h3 className="font-semibold">模型详情</h3><button type="button" className={buttonClass} onClick={() => { cancel(); patch(identity.current, { busy: false, detail: null, report: null, error: "", retry: null }) }}>关闭模型详情</button></div>
      <p className="break-all">模型版本 {detail.id} · 报告SHA {detail.report_sha256}</p>
      <p className="break-all">数据集 {detail.dataset.dataset_id} · 版本 {detail.dataset.data_version} · 资格 {detail.dataset.qualification}</p>
      <p>{detail.state === "quarantined" ? "模型已隔离，计算报告不可读。" : "已登记模型，只读。"} {detail.validation_status === "qa_only" ? "QA数据，未经认证。" : "仅限研究。"} 最终测试未封存。</p>
      <details><summary className="cursor-pointer">固定训练参数</summary><pre className="max-h-64 overflow-auto whitespace-pre-wrap break-all p-2">{JSON.stringify(detail.parameters, null, 2)}</pre></details>
      {detail.folds.map((fold, index) => <section key={fold.fold_id} className="space-y-2 border-t border-[var(--border)] pt-2">
        <h4>窗口 {index + 1}</h4>
        <p>训练 {fold.split.train_start} 至 {fold.split.train_end}；验证 {fold.split.validation_start} 至 {fold.split.validation_end}；测试 {fold.split.test_start} 至 {fold.split.test_end}</p>
        <p>训练知识截止 {fold.model.training_knowledge_cutoff}；实际训练完成 {fold.model.created_at}；使用 {fold.model.trees_used} 棵树，耗时 {fold.model.elapsed_seconds} 秒。</p>
        <p className="break-all">方法 {fold.model.method_version} · 模型SHA {fold.model.model_sha256} · 窗口版本 {fold.fold_id}</p>
        <details><summary className="cursor-pointer">训练环境版本</summary><pre className="max-h-64 overflow-auto whitespace-pre-wrap break-all p-2">{JSON.stringify(fold.model.runtime, null, 2)}</pre></details>
        <div className="overflow-x-auto"><table className="w-full text-left"><caption className="text-left">特征重要性（gain，非因果）与固定版本</caption><thead><tr><th>特征</th><th>重要性</th><th>版本</th></tr></thead><tbody>{fold.model.feature_order.map((name) => <tr key={name}><td className="p-1">{name}</td><td className="p-1">{fold.model.feature_gain[name]}</td><td className="break-all p-1">{fold.model.feature_versions[name]}</td></tr>)}</tbody></table></div>
      </section>)}
      <ul>{detail.limitations.map((limitation, index) => <li key={index} className="py-1">{limitation}</li>)}</ul>
      {detail.state === "ready" && <button type="button" className={buttonClass} disabled={state.busy} onClick={() => run({ kind: "report", id: detail.id })}>读取已存计算报告</button>}
      {state.report && <details open><summary className="cursor-pointer">已存计算报告（只读，读取不触发评估）</summary><pre className="max-h-96 overflow-auto whitespace-pre-wrap break-all p-2">{JSON.stringify(state.report.report, null, 2)}</pre></details>}
    </article>}
  </section>
}
