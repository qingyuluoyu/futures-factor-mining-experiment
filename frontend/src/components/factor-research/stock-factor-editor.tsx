"use client"

import { useEffect, useRef, useState } from "react"
import { disableCustomFactor, getCustomFactorAvailability, listCustomFactors, saveCustomFactor, validateCustomFactor, type CompiledFactor, type CustomFactor, type CustomFactorAvailability, type CustomFactorCommand, type CustomFactorPage } from "@/lib/stock-custom-factor-api"
import { clearPendingWrite, readPendingWrites, recordPendingWrite, type PendingWriteEntry } from "@/lib/stock-pending-writes"

type Identity = { token: string; scope: string }
type Draft = { name: string; expression: string; description: string; direction: "high" | "low" }
type EditorState = {
  identity: Identity; availability: CustomFactorAvailability | null; busy: boolean; error: string; notice: string
  page: CustomFactorPage; offset: number; history: CustomFactorPage | null; historyOffset: number
  draft: Draft; editing: CustomFactor | null; validation: CompiledFactor | null; validatedFor: string; confirmDisable: boolean
}
type PendingWrite = { identity: Identity; kind: "save"; body: CustomFactorCommand; editing?: { id: string; expected_revision: number } }
  | { identity: Identity; kind: "disable"; id: string; body: { idempotency_key: string; expected_revision: number } }

/** 把一次自定义因子写登记成跨页未确认写：只留参数与原幂等键，token 一律不落盘。 */
function factorPendingEntry(command: PendingWrite): PendingWriteEntry {
  if (command.kind === "save") {
    return {
      kind: "custom-factor-save",
      idempotency_key: command.body.idempotency_key,
      ...(command.editing ? { expected_revision: command.editing.expected_revision } : {}),
      signature: JSON.stringify({
        name: command.body.name, expression: command.body.expression, description: command.body.description, direction: command.body.direction,
        ...(command.editing ? { editing: command.editing } : {}),
      }),
      created_at: new Date().toISOString(),
    }
  }
  return {
    kind: "custom-factor-disable",
    idempotency_key: command.body.idempotency_key,
    expected_revision: command.body.expected_revision,
    signature: JSON.stringify({ id: command.id }),
    created_at: new Date().toISOString(),
  }
}

/** 由注册表条目重建同一条写命令，使「重试原请求」仍带原幂等键、原参数和原期望版本。 */
function factorPendingWrite(scope: string, identity: Identity): PendingWrite | null {
  const entry = readPendingWrites(scope).find((item) => item.kind === "custom-factor-save" || item.kind === "custom-factor-disable")
  if (!entry) return null
  let parameters: Record<string, unknown>
  try {
    parameters = JSON.parse(entry.signature)
  } catch {
    return null
  }
  if (!parameters || typeof parameters !== "object" || Array.isArray(parameters)) return null
  if (entry.kind === "custom-factor-save") {
    const { name, expression, description, direction, editing } = parameters
    if (typeof name !== "string" || typeof expression !== "string" || typeof description !== "string" || (direction !== "high" && direction !== "low")) return null
    return {
      identity, kind: "save",
      body: { name, expression, description, direction, idempotency_key: entry.idempotency_key },
      ...(editing && typeof editing === "object" ? { editing: editing as { id: string; expected_revision: number } } : {}),
    }
  }
  if (typeof parameters.id !== "string" || entry.expected_revision === undefined) return null
  return { identity, kind: "disable", id: parameters.id, body: { idempotency_key: entry.idempotency_key, expected_revision: entry.expected_revision } }
}

const LIMIT = 20
const controlClass = "rounded border border-[var(--border)] bg-[var(--bg-secondary)] px-2 py-1.5 text-xs disabled:opacity-50"
const buttonClass = `${controlClass} cursor-pointer disabled:cursor-not-allowed`
function empty(identity: Identity): EditorState {
  return { identity, availability: null, busy: false, error: "", notice: "", page: { items: [], total: 0, has_more: false }, offset: 0, history: null, historyOffset: 0,
    draft: { name: "", expression: "", direction: "high", description: "" }, editing: null, validation: null, validatedFor: "", confirmDisable: false }
}

export function StockFactorEditor({ token, scope }: { token: string; scope: string }): React.JSX.Element {
  const identity = useRef<Identity>({ token, scope })
  if (identity.current.token !== token || identity.current.scope !== scope) identity.current = { token, scope }
  const [state, setState] = useState<EditorState>(() => empty(identity.current))
  const active = useRef<AbortController | null>(null)
  const pending = useRef<PendingWrite | null>(null)

  function patch(origin: Identity, values: Partial<EditorState>) {
    if (identity.current !== origin) return
    setState((previous) => previous.identity === origin ? { ...previous, ...values } : previous)
  }

  function perform(task: (signal: AbortSignal, origin: Identity) => Promise<void>) {
    if (active.current) return
    const origin = identity.current
    const controller = new AbortController()
    active.current = controller
    patch(origin, { busy: true, error: "" })
    void (async () => {
      try { await task(controller.signal, origin) }
      catch (error) {
        if (!controller.signal.aborted) patch(origin, { error: error instanceof Error ? error.message : "自定义因子请求失败" })
      } finally {
        if (active.current === controller) active.current = null
        patch(origin, { busy: false })
      }
    })()
  }

  function load(offset = 0) {
    perform(async (signal, origin) => {
      const availability = await getCustomFactorAvailability(origin.token, signal)
      patch(origin, { availability })
      if (availability.definitions_enabled) {
        const page = await listCustomFactors({ limit: LIMIT, offset }, origin.token, signal)
        patch(origin, { page, offset })
      }
    })
  }

  useEffect(() => {
    const origin = identity.current
    pending.current = factorPendingWrite(origin.scope, origin)
    setState(empty(origin))
    load()
    return () => { active.current?.abort(); active.current = null }
  }, [token, scope])

  async function commit(command: PendingWrite, signal: AbortSignal) {
    const origin = command.identity
    if (identity.current !== origin || signal.aborted) return
    recordPendingWrite(origin.scope, factorPendingEntry(command))
    let saved: CustomFactor
    try {
      saved = command.kind === "save" ? await saveCustomFactor(command.body, origin.token, signal, command.editing)
        : await disableCustomFactor(command.id, command.body, origin.token, signal)
    } catch (error) {
      const status = (error as { status?: number })?.status
      if (identity.current === origin && status && [400, 401, 403, 404, 409, 422].includes(status)) {
        pending.current = null
        clearPendingWrite(origin.scope, command.body.idempotency_key)
      }
      throw error
    }
    if (identity.current !== origin || signal.aborted) return
    pending.current = null
    clearPendingWrite(origin.scope, command.body.idempotency_key)
    patch(origin, { editing: saved, draft: { name: saved.name, expression: saved.expression, description: saved.description, direction: saved.direction }, validation: null, validatedFor: "", confirmDisable: false, history: null,
      notice: command.kind === "save" ? `定义已保存：${saved.name} v${saved.revision}；未启动IC、回测或模型。` : "已停用；历史定义和已保存研究引用不会删除。" })
    try {
      const page = await listCustomFactors({ limit: LIMIT, offset: 0 }, origin.token, signal)
      patch(origin, { page, offset: 0 })
    } catch { patch(origin, { error: "定义写入已确认，但列表刷新失败；请刷新列表核对，不要重复新建。" }) }
  }

  function save() {
    if (state.busy || pending.current || !state.validation || state.validatedFor !== state.draft.expression || !state.draft.name.trim() || state.editing?.disabled) return
    perform(async (signal, origin) => {
      const command: PendingWrite = { identity: origin, kind: "save", body: { ...state.draft, idempotency_key: crypto.randomUUID() },
        ...(state.editing ? { editing: { id: state.editing.id, expected_revision: state.editing.latest_revision } } : {}) }
      pending.current = command
      await commit(command, signal)
    })
  }

  function select(record: CustomFactor) {
    if (state.busy || pending.current) return
    patch(identity.current, { editing: record, draft: { name: record.name, expression: record.expression, direction: record.direction, description: record.description }, validation: null, validatedFor: "", notice: "", history: null, confirmDisable: false })
  }

  function change(values: Partial<Draft>) {
    if (state.busy || pending.current || state.editing?.disabled) return
    patch(identity.current, { draft: { ...state.draft, ...values }, validation: null, validatedFor: "", notice: "", confirmDisable: false })
  }

  function history(offset = 0) {
    if (!state.editing) return
    const identifier = state.editing.id
    perform(async (signal, origin) => {
      const page = await listCustomFactors({ limit: LIMIT, offset }, origin.token, signal, identifier)
      patch(origin, { history: page, historyOffset: offset })
    })
  }

  if (state.identity !== identity.current) return <p role="status">正在清理旧会话的因子定义…</p>
  const unresolved = pending.current !== null
  const locked = state.busy || unresolved || Boolean(state.editing?.disabled)
  const canSave = !locked && Boolean(state.validation) && state.validatedFor === state.draft.expression && Boolean(state.draft.name.trim())
  return <section aria-label="A股自定义因子定义管理" className="mb-4 space-y-3 rounded-lg border border-[var(--border)] p-3 text-xs">
    <h2 className="font-semibold">自定义因子 · 不可变版本</h2>
    <p className="leading-6 text-[var(--text-muted)]">仅管理定义和历史版本，不代表已经通过数据资格检查；固定数据版本的IC与回测尚未接通，不会发送自定义ID到旧回测入口。保存、校验均不调用模型。</p>
    {state.busy && <p role="status">正在处理…</p>}
    {state.error && <p role="alert" className="text-[var(--accent-danger)]">{state.error}</p>}
    {state.notice && <p role="status">{state.notice}</p>}
    <button type="button" className={buttonClass} disabled={state.busy} onClick={() => load(state.offset)}>刷新定义列表</button>
    {state.availability && !state.availability.definitions_enabled && <p role="status">{state.availability.reason}。未开放定义写入。</p>}
    {state.availability?.definitions_enabled && <>
      <details><summary className="cursor-pointer">字段单位、可得时间和语法</summary>
        <p className="py-2 leading-6">允许 + - * /、abs、log、clip、rank、zscore、lag、rolling_mean、rolling_std。lag窗口1–244，rolling窗口2–244，合计回看不超过244个交易日。除零、非正log和无足够历史产生缺失，不补0。百分数以百分数单位输入，不暗中除100；标准化后才可按同口径合成。市场字段是否真实齐备由后续数据版本资格检查决定。</p>
        <ul>{state.availability.fields.map((field) => <li key={field.id} className="py-1"><code>{field.id}</code> · {field.unit} · {field.availability}</li>)}</ul>
      </details>
      <div className="flex flex-wrap gap-2">
        <button type="button" className={buttonClass} disabled={state.busy || unresolved} onClick={() => patch(identity.current, { ...empty(identity.current), availability: state.availability, page: state.page, offset: state.offset })}>新建定义</button>
        <span className="py-1.5">已存 {state.page.total} 个；每页 {LIMIT} 个（停用定义仍保留）。</span>
      </div>
      <ul className="grid gap-2 md:grid-cols-2">{state.page.items.map((record) => <li key={record.id} className="rounded border border-[var(--border)] p-2">
        <button type="button" className="cursor-pointer text-left underline disabled:opacity-50" disabled={state.busy || unresolved} onClick={() => select(record)}>{record.name} v{record.revision}{record.disabled ? "（已停用，只读）" : ""}</button>
        <p className="mt-1 break-all"><code>{record.expression}</code></p>
      </li>)}</ul>
      <div className="flex gap-2">
        <button type="button" className={buttonClass} disabled={state.busy || state.offset === 0} onClick={() => load(Math.max(0, state.offset - LIMIT))}>定义上一页</button>
        <button type="button" className={buttonClass} disabled={state.busy || !state.page.has_more} onClick={() => load(state.offset + LIMIT)}>定义下一页</button>
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        <label>名称<input aria-label="自定义因子名称" value={state.draft.name} disabled={locked} maxLength={80} className={`${controlClass} mt-1 block w-full`} onChange={(event) => change({ name: event.target.value })} /></label>
        <label>方向<select aria-label="自定义因子方向" value={state.draft.direction} disabled={locked} className={`${controlClass} mt-1 block w-full`} onChange={(event) => change({ direction: event.target.value as "high" | "low" })}><option value="high">因子越大越好</option><option value="low">因子越小越好</option></select></label>
      </div>
      <label className="block">表达式<textarea aria-label="自定义因子表达式" value={state.draft.expression} disabled={locked} maxLength={4096} rows={3} placeholder="close / lag(close, 20) - 1" className={`${controlClass} mt-1 w-full font-mono`} onChange={(event) => change({ expression: event.target.value })} /></label>
      <label className="block">经济含义与注意事项<textarea aria-label="自定义因子描述" value={state.draft.description} disabled={locked} maxLength={1000} rows={2} className={`${controlClass} mt-1 w-full`} onChange={(event) => change({ description: event.target.value })} /></label>
      {state.editing && <p>当前 {state.editing.name} v{state.editing.revision} · {state.editing.disabled ? "已停用，仅历史可读" : `编辑将生成v${state.editing.latest_revision + 1}，原版本保持不变`} · <code className="break-all">{state.editing.expression_version}</code></p>}
      {state.validation && <p role="status" className="break-all">语法已校验；依赖 {state.validation.fields.join("、")} · 回看 {state.validation.lookback} 交易日 · {state.validation.formula_version} · {state.validation.expression_version}。不是因子有效性或收益证明。</p>}
      <div className="flex flex-wrap gap-2">
        <button type="button" className={buttonClass} disabled={locked || !state.draft.expression.trim()} onClick={() => perform(async (signal, origin) => { const validation = await validateCustomFactor(state.draft.expression, origin.token, signal); patch(origin, { validation, validatedFor: state.draft.expression }) })}>校验表达式</button>
        <button type="button" className={buttonClass} disabled={!canSave} onClick={save}>保存定义</button>
        {state.editing && <button type="button" className={buttonClass} disabled={state.busy || unresolved} onClick={() => history()}>查看当前因子版本</button>}
        {state.editing && !state.editing.disabled && <button type="button" className={buttonClass} disabled={locked} onClick={() => patch(identity.current, { confirmDisable: true })}>停用当前因子</button>}
        {unresolved && <button type="button" className={buttonClass} disabled={state.busy} onClick={() => { const command = pending.current; if (command) perform((signal) => commit(command, signal)) }}>重试原请求</button>}
      </div>
      {unresolved && !state.busy && <p role="alert">上次写入结果未确认；原参数与原幂等键已保存在本浏览器，离开页面、切换页面或刷新都不会丢失，也不会自动重发。只有点击「重试原请求」才会用同一个幂等键重试，服务端据此去重，不会重复新建或改写历史版本；重试前请先核对定义列表。</p>}
      {state.confirmDisable && state.editing && <div role="alert" className="space-x-2"><span>停用后禁止新选用，不能编辑；历史版本不删除。是否确认？</span>
        <button type="button" className={buttonClass} disabled={locked} onClick={() => { const record = state.editing; if (record) perform(async (signal, origin) => { const command: PendingWrite = { identity: origin, kind: "disable", id: record.id, body: { expected_revision: record.latest_revision, idempotency_key: crypto.randomUUID() } }; pending.current = command; await commit(command, signal) }) }}>确认停用</button>
        <button type="button" className={buttonClass} disabled={state.busy || unresolved} onClick={() => patch(identity.current, { confirmDisable: false })}>取消停用</button>
      </div>}
      {state.history && <div className="space-y-2"><h3>版本记录 · 共 {state.history.total} 个</h3>{state.history.items.map((record) => <article key={`${record.id}:${record.revision}`} className="rounded border border-[var(--border)] p-2"><p>{record.name} v{record.revision} · {record.created_at} · {record.direction === "high" ? "越大越好" : "越小越好"}</p><pre className="my-1 whitespace-pre-wrap break-all">{record.expression}</pre><p className="break-all">{record.expression_version}</p><p>{record.description}</p></article>)}
        <button type="button" className={buttonClass} disabled={state.busy || state.historyOffset === 0} onClick={() => history(Math.max(0, state.historyOffset - LIMIT))}>版本上一页</button>
        <button type="button" className={buttonClass} disabled={state.busy || !state.history.has_more} onClick={() => history(state.historyOffset + LIMIT)}>版本下一页</button>
      </div>}
    </>}
  </section>
}
