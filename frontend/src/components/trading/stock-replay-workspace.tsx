"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import Link from "next/link"
import { ArrowRight, ChevronLeft, ChevronRight, Loader2, Plus, RefreshCw } from "lucide-react"
import { StockKlineChart } from "@/components/market/stock-kline-chart"
import { StockPendingWritesBanner } from "@/components/factor-research/stock-pending-writes-banner"
import { StockReplayOrderHistory } from "./stock-replay-order-history"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { useAuthStore } from "@/stores/auth"
import { useDisplayStore } from "@/stores/display"
import { cn } from "@/lib/utils"
import {
  advanceStockReplayAccount, cancelStockReplayOrder, createStockReplayAccount,
  getStockReplayAccount, getStockReplayAvailability, listStockReplayAccounts, submitStockReplayOrder,
  type ReplayAccountSummary, type ReplayAvailability, type ReplayCommand, type ReplayCreateInput,
  type ReplayOrderInput, type ReplayView,
} from "@/lib/stock-replay-api"
import { prepareReplayMutation, replayAccountStorageKey, replayCreateError, replayOrderError, replayOrderReason } from "./stock-replay-state"
import { clearPendingWrite, pendingWriteFailureMessage, readPendingWrites, recordPendingWrite, type PendingWriteEntry } from "@/lib/stock-pending-writes"

type ReplayTab = "overview" | "orders" | "positions" | "assets"
const REPLAY_PAGES = [
  { value: "overview", label: "交易回放", title: "A股日线回放", href: "/trading" },
  { value: "positions", label: "持仓", title: "A股回放持仓", href: "/positions" },
  { value: "orders", label: "订单", title: "A股回放订单", href: "/orders" },
  { value: "assets", label: "资产", title: "A股回放资产", href: "/assets" },
] as const
type Mutation =
  | { kind: "create"; body: ReplayCreateInput }
  | { kind: "order"; accountId: string; body: ReplayOrderInput }
  | { kind: "step"; accountId: string; body: ReplayCommand }
  | { kind: "cancel"; accountId: string; orderId: string; body: ReplayCommand }

function replayAction(command: Mutation): string {
  if (command.kind === "create") return "create"
  if (command.kind === "order") return `order:${command.accountId}`
  if (command.kind === "step") return `step:${command.accountId}`
  return `cancel:${command.accountId}:${command.orderId}`
}

function replayIdempotencyKey(command: Mutation): string {
  const key = (command.body as { idempotency_key?: unknown }).idempotency_key
  return typeof key === "string" ? key : ""
}

/** 把一次回放写登记成跨页未确认写：只留 action + 负载 + 原幂等键，token 一律不落盘。 */
function replayPendingEntry(command: Mutation): PendingWriteEntry {
  const { idempotency_key: _key, expected_revision: revision, ...payload } = command.body as unknown as Record<string, unknown>
  return {
    kind: `replay-${command.kind}`,
    idempotency_key: replayIdempotencyKey(command),
    ...(typeof revision === "number" ? { expected_revision: revision } : {}),
    signature: JSON.stringify([replayAction(command), payload]),
    created_at: new Date().toISOString(),
  }
}

/** 由注册表条目重建同一条写命令，使「重试同一请求」仍带原幂等键和原参数。 */
function replayMutationFromEntry(entry: PendingWriteEntry): Mutation | null {
  let action: unknown, payload: unknown
  try {
    [action, payload] = JSON.parse(entry.signature)
  } catch {
    return null
  }
  if (typeof action !== "string" || !payload || typeof payload !== "object" || Array.isArray(payload)) return null
  const body = { ...(payload as Record<string, unknown>), idempotency_key: entry.idempotency_key, ...(entry.expected_revision === undefined ? {} : { expected_revision: entry.expected_revision }) }
  if (entry.kind === "replay-create") return { kind: "create", body } as Mutation
  if (entry.kind === "replay-order" && action.startsWith("order:")) return { kind: "order", accountId: action.slice(6), body } as Mutation
  if (entry.kind === "replay-step" && action.startsWith("step:")) return { kind: "step", accountId: action.slice(5), body } as Mutation
  if (entry.kind === "replay-cancel" && action.startsWith("cancel:")) {
    const rest = action.slice(7), split = rest.lastIndexOf(":")
    if (split <= 0 || split === rest.length - 1) return null
    return { kind: "cancel", accountId: rest.slice(0, split), orderId: rest.slice(split + 1), body } as Mutation
  }
  return null
}

/** 挂载时回填未确认写，只回填当前用户与登录盘自己的条目。 */
function replayPendingRetry(scope: string): Mutation | null {
  const entry = readPendingWrites(scope).find((item) => item.kind.startsWith("replay-"))
  return entry ? replayMutationFromEntry(entry) : null
}

const fieldClass = "w-full rounded border border-[var(--border)] bg-[var(--bg-primary)] px-2.5 py-2 text-sm text-[var(--text-primary)] disabled:opacity-50"
const statusLabels = { pending: "待下一开盘", filled: "已成交", rejected: "已拒绝", cancelled: "已撤单" }

function money(value: string, digits = 2): string {
  return Number(value).toLocaleString("zh-CN", { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

function errorMessage(error: unknown): string {
  if (error instanceof Error && (error.name === "TimeoutError" || error.name === "AbortError")) return "请求超时，结果尚未确认，请重试同一请求。"
  return error instanceof Error ? error.message : "请求失败，请重试"
}

export function StockReplayWorkspace({ initialTab = "overview" }: { initialTab?: ReplayTab }): React.JSX.Element {
  const user = useAuthStore((state) => state.user)
  const loaded = useAuthStore((state) => state.loaded)
  if (!loaded) return <div role="status" className="p-6 text-sm text-[var(--text-muted)]">正在验证登录状态…</div>
  if (!user) return <div role="status" className="p-6 text-sm text-[var(--text-muted)]">请先登录后使用A股日线回放。</div>
  const tradingMode = user.trading_mode === "virtual" ? "virtual" : "live"
  return <ReplaySession key={`${user.id}:${tradingMode}`} userId={user.id} tradingMode={tradingMode} initialTab={initialTab} />
}

function ReplaySession({ userId, tradingMode, initialTab }: { userId: string; tradingMode: "live" | "virtual"; initialTab: ReplayTab }): React.JSX.Element {
  const upColor = useDisplayStore((state) => state.candleUp)
  const downColor = useDisplayStore((state) => state.candleDown)
  const [availability, setAvailability] = useState<ReplayAvailability | null>(null)
  const [accounts, setAccounts] = useState<ReplayAccountSummary[]>([])
  const [offset, setOffset] = useState(0)
  const [accountId, setAccountId] = useState("")
  const [code, setCode] = useState("")
  const [view, setView] = useState<ReplayView | null>(null)
  const [booting, setBooting] = useState(true)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const pendingWriteScope = `${userId}:${tradingMode}`
  const [retry, setRetry] = useState<Mutation | null>(() => replayPendingRetry(pendingWriteScope))
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const tab = initialTab
  const page = REPLAY_PAGES.find((item) => item.value === tab)!
  const [showCreate, setShowCreate] = useState(false)
  const [name, setName] = useState("A股日线回放")
  const [initialCash, setInitialCash] = useState("1000000")
  const [startDate, setStartDate] = useState("")
  const [datasetIds, setDatasetIds] = useState<string[]>([])
  const [direction, setDirection] = useState<"buy" | "sell">("buy")
  const [quantity, setQuantity] = useState("100")
  const [orderType, setOrderType] = useState<"market" | "limit">("market")
  const [limitPrice, setLimitPrice] = useState("")
  const readSequence = useRef(0)
  const mounted = useRef(true)
  const mutationInFlight = useRef(false)
  const accountListSequence = useRef(0)
  const storageKey = replayAccountStorageKey(userId, tradingMode)

  const isCurrentSession = useCallback((): boolean => {
    const user = useAuthStore.getState().user
    return mounted.current && user?.id === userId && (user.trading_mode === "virtual" ? "virtual" : "live") === tradingMode && user.market_type === "stock"
  }, [userId, tradingMode])

  const rememberAccount = useCallback((id: string): void => {
    if (!isCurrentSession()) return
    try { localStorage.setItem(storageKey, id) } catch {}
  }, [isCurrentSession, storageKey])

  const loadAccounts = useCallback(async (pageOffset: number, signal?: AbortSignal): Promise<ReplayAccountSummary[]> => {
    const sequence = ++accountListSequence.current
    const rows = await listStockReplayAccounts(20, pageOffset, signal)
    if (isCurrentSession() && !signal?.aborted && sequence === accountListSequence.current) {
      setAccounts(rows)
      setOffset(pageOffset)
    }
    return rows
  }, [isCurrentSession])

  const initialize = useCallback(async (signal?: AbortSignal): Promise<void> => {
    setBooting(true)
    setError(null)
    try {
      const available = await getStockReplayAvailability(signal)
      if (!isCurrentSession() || signal?.aborted) return
      setAvailability(available)
      if (!available.enabled) return
      const rows = await loadAccounts(0, signal)
      if (!isCurrentSession() || signal?.aborted) return
      let remembered: string | null = null
      try { remembered = localStorage.getItem(storageKey) } catch {}
      setAccountId(remembered || rows[0]?.id || "")
      if (available.datasets[0]) {
        setDatasetIds([available.datasets[0].id])
        setStartDate(available.datasets[0].start_date)
      }
      setShowCreate(rows.length === 0)
    } catch (failure) {
      if (isCurrentSession() && !signal?.aborted) setError(errorMessage(failure))
    } finally {
      if (isCurrentSession() && !signal?.aborted) setBooting(false)
    }
  }, [isCurrentSession, loadAccounts, storageKey])

  useEffect(() => {
    mounted.current = true
    const controller = new AbortController()
    void initialize(controller.signal)
    return () => {
      mounted.current = false
      readSequence.current += 1
      controller.abort()
    }
  }, [initialize])

  const refreshView = useCallback(async (id: string, selectedCode?: string, signal?: AbortSignal): Promise<void> => {
    const sequence = ++readSequence.current
    setLoading(true)
    setView(null)
    try {
      const result = await getStockReplayAccount(id, selectedCode || undefined, signal)
      if (!isCurrentSession() || sequence !== readSequence.current || signal?.aborted) return
      if (result.id !== id || result.trading_mode !== tradingMode || (selectedCode && result.chart.code !== selectedCode)) throw new Error("回放账户或标的数据不匹配，请刷新")
      setView(result)
      rememberAccount(id)
    } catch (failure) {
      if (isCurrentSession() && sequence === readSequence.current && !signal?.aborted) {
        setError(errorMessage(failure))
        if ((failure as { status?: number })?.status === 404) {
          try { localStorage.removeItem(storageKey) } catch {}
          setAccountId("")
        }
      }
    } finally {
      if (isCurrentSession() && sequence === readSequence.current && !signal?.aborted) setLoading(false)
    }
  }, [isCurrentSession, rememberAccount, storageKey, tradingMode])

  useEffect(() => {
    if (!accountId || !availability?.enabled) return
    const controller = new AbortController()
    setError(null)
    void refreshView(accountId, code, controller.signal)
    return () => controller.abort()
  }, [accountId, code, availability?.enabled, refreshView])

  async function runMutation(command: Mutation): Promise<void> {
    if (mutationInFlight.current || (retry && command !== retry) || !isCurrentSession()) return
    mutationInFlight.current = true
    setBusy(true)
    setError(null)
    setMessage(null)
    setRetry(null)
    try {
      const registration = recordPendingWrite(pendingWriteScope, replayPendingEntry(command))
      if (!registration.ok) {
        if (isCurrentSession()) {
          setError(pendingWriteFailureMessage(registration.reason))
          setRetry(command)
        }
        return
      }
      // A locally rejected command must not invalidate a read when no mutation was sent.
      readSequence.current += 1
      let result: ReplayView
      switch (command.kind) {
        case "create": result = await createStockReplayAccount(command.body); break
        case "order": result = await submitStockReplayOrder(command.accountId, command.body); break
        case "step": result = await advanceStockReplayAccount(command.accountId, command.body); break
        case "cancel": result = await cancelStockReplayOrder(command.accountId, command.orderId, command.body); break
      }
      if (!isCurrentSession()) return
      if (result.trading_mode !== tradingMode || (command.kind !== "create" && result.id !== command.accountId)) throw new Error("回放账户响应不匹配，请重试同一请求")
      accountListSequence.current += 1
      setRetry(null)
      clearPendingWrite(pendingWriteScope, replayIdempotencyKey(command))
      setLoading(false)
      rememberAccount(result.id)
      setAccounts((previous) => {
        const summary = { id: result.id, name: result.name, current_date: result.current_date, status: result.status, revision: result.revision }
        return previous.some((item) => item.id === result.id) ? previous.map((item) => item.id === result.id ? summary : item) : [summary, ...previous].slice(0, 20)
      })
      if (command.kind === "create") {
        setShowCreate(false)
        setCode("")
        setAccountId(result.id)
      }
      setView(result)
      setMessage(command.kind === "order" ? "委托已记录，将在推进后的下一交易日开盘按规则撮合。" : command.kind === "step" ? `已推进至 ${result.current_date}，请核对委托成交或拒绝原因。` : command.kind === "cancel" ? "撤单已记录。" : "独立回放账户已创建，已有账户历史保留。")
      if (command.kind !== "create" && code && result.chart.code !== code) await refreshView(result.id, code)
    } catch (failure) {
      if (!isCurrentSession()) return
      const status = (failure as { status?: number })?.status
      setError(errorMessage(failure))
      if (status !== undefined && status >= 400 && status < 500) {
        setRetry(null)
        clearPendingWrite(pendingWriteScope, replayIdempotencyKey(command))
        if (command.kind !== "create") await refreshView(command.accountId, code)
      } else {
        setRetry(command)
      }
    } finally {
      mutationInFlight.current = false
      if (isCurrentSession()) setBusy(false)
    }
  }

  const currentView = view?.id === accountId ? view : null
  const selectedCode = code || currentView?.chart.code || ""
  const selectedInstrument = currentView?.instruments.find((item) => item.code === selectedCode)
  const locked = busy || retry !== null
  const canTrade = Boolean(currentView && !loading && !locked && currentView.status === "active")

  function createAccount(event: React.FormEvent): void {
    event.preventDefault()
    if (locked) return
    const input = { name: name.trim(), initial_cash: initialCash, start_date: startDate, dataset_ids: datasetIds, commission_rate: "0.0003", min_commission: "5", slippage_rate: "0.001" }
    const invalid = replayCreateError(input, availability?.datasets ?? [])
    if (invalid) { setError(invalid); return }
    void runMutation({ kind: "create", body: prepareReplayMutation(null, "create", input).body })
  }

  function submitOrder(event: React.FormEvent): void {
    event.preventDefault()
    if (!currentView || !canTrade) return
    const input = { code: selectedCode, direction, quantity: Number(quantity), order_type: orderType, limit_price: orderType === "limit" ? limitPrice : null }
    const invalid = replayOrderError(input, currentView.instruments, currentView.positions)
    if (invalid) { setError(invalid); return }
    void runMutation({ kind: "order", accountId, body: prepareReplayMutation(null, `order:${accountId}`, { ...input, expected_revision: currentView.revision }).body })
  }

  function advance(): void {
    if (!currentView || !canTrade) return
    void runMutation({ kind: "step", accountId, body: prepareReplayMutation(null, `step:${accountId}`, { expected_revision: currentView.revision }).body })
  }

  function cancelOrder(orderId: string): void {
    if (!currentView || !canTrade) return
    void runMutation({ kind: "cancel", accountId, orderId, body: prepareReplayMutation(null, `cancel:${accountId}:${orderId}`, { expected_revision: currentView.revision }).body })
  }

  async function changeAccountPage(nextOffset: number): Promise<void> {
    if (locked) return
    setLoading(true)
    setError(null)
    try { await loadAccounts(nextOffset) } catch (failure) { if (isCurrentSession()) setError(errorMessage(failure)) }
    finally { if (isCurrentSession()) setLoading(false) }
  }

  const pnlColor = (value: string): string => value.startsWith("-") ? downColor : upColor

  return (
    <section aria-label={`${page.title}工作区`} className="flex h-full min-h-0 flex-col overflow-auto bg-[var(--bg-primary)] text-[var(--text-primary)]">
      {/* 跨页未确认写横幅：本工作区覆盖 /trading、/positions、/orders、/assets。
          replay-* 由下方行内「重试同一请求」自己处理，用 ignore 排除，避免同一件事提示两遍。 */}
      <div className="shrink-0 px-4 pt-3"><StockPendingWritesBanner scope={pendingWriteScope} ignore={(kind) => kind.startsWith("replay-")} /></div>
      <header className="flex shrink-0 flex-wrap items-center gap-3 border-b border-[var(--border)] bg-[var(--bg-secondary)] px-4 py-3">
        <div><h1 className="text-base font-semibold">{page.title}</h1><p className="mt-0.5 text-xs text-[var(--text-muted)]">历史数据模拟 · 非实时盘 · 不连接真实资金</p></div>
        {availability?.enabled && <div className="ml-auto flex flex-wrap items-center gap-2">
          <select aria-label="选择股票回放账户" value={accountId} disabled={locked || loading} onChange={(event) => { setView(null); setCode(""); setAccountId(event.target.value); setMessage(null) }} className={cn(fieldClass, "max-w-[240px]")}>
            {!accountId && <option value="">请选择回放账户</option>}
            {accountId && !accounts.some((item) => item.id === accountId) && <option value={accountId}>{currentView?.name ?? "已记忆的回放账户"}</option>}
            {accounts.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.current_date}{item.status === "completed" ? " · 已结束" : ""}</option>)}
          </select>
          <div className="flex items-center gap-1"><Button variant="ghost" size="sm" aria-label="上一页账户" disabled={locked || loading || offset === 0} onClick={() => void changeAccountPage(Math.max(0, offset - 20))}><ChevronLeft className="h-4 w-4" /></Button><span className="text-xs text-[var(--text-muted)]">第{Math.floor(offset / 20) + 1}页</span><Button variant="ghost" size="sm" aria-label="下一页账户" disabled={locked || loading || accounts.length < 20} onClick={() => void changeAccountPage(offset + 20)}><ChevronRight className="h-4 w-4" /></Button></div>
          <Button variant="outline" size="sm" disabled={locked || !availability.datasets.length} onClick={() => setShowCreate(!showCreate)}><Plus className="mr-1 h-3.5 w-3.5" />新建账户</Button>
          <Button variant="outline" size="sm" disabled={locked || loading} onClick={() => { setError(null); if (accountId) void refreshView(accountId, code); else void initialize() }}><RefreshCw className={cn("mr-1 h-3.5 w-3.5", loading && "animate-spin")} />刷新</Button>
        </div>}
      </header>
      <nav aria-label="回放账户页面" className="flex shrink-0 flex-wrap gap-2 border-b border-[var(--border)] px-4 py-2">
        {REPLAY_PAGES.map((item) => locked ? <span key={item.value} aria-disabled="true" aria-current={tab === item.value ? "page" : undefined} className="rounded px-3 py-1.5 text-xs text-[var(--text-muted)] opacity-50">{item.label}</span> : <Link key={item.value} href={item.href} aria-current={tab === item.value ? "page" : undefined} className={cn("rounded px-3 py-1.5 text-xs", tab === item.value ? "bg-[var(--primary)] text-white" : "text-[var(--text-muted)] hover:bg-[var(--bg-tertiary)]")}>{item.label}</Link>)}
      </nav>

      {(error || message || retry) && <div role={error ? "alert" : "status"} className={cn("shrink-0 border-b border-[var(--border)] px-4 py-3 text-sm", error ? "text-[var(--accent-danger)]" : "text-[var(--text-secondary)]")}>
        {error ?? message}
        {retry && <div className="mt-2 flex flex-wrap items-center gap-3"><span className="text-xs">结果尚未确认；重试保留原幂等键和版本号，避免重复委托或推进。</span><Button variant="outline" size="sm" disabled={busy} onClick={() => void runMutation(retry)}>重试同一请求</Button></div>}
      </div>}

      {booting ? <div role="status" className="flex flex-1 items-center justify-center gap-2 p-8 text-sm text-[var(--text-muted)]"><Loader2 className="h-4 w-4 animate-spin" />正在检查回放服务与账户…</div> : !availability?.enabled ? <div className="flex-1 p-8 text-sm text-[var(--text-secondary)]"><h2 className="mb-2 font-semibold">回放服务暂不可用</h2><p>{availability ? "当前环境尚未启用A股回放，请由管理员完成迁移、授权数据导入与功能验证。" : "无法读取回放服务状态，请检查上方错误后重试。"}</p><Button className="mt-4" variant="outline" onClick={() => void initialize()}>重新检查</Button></div> : <>
        {availability.datasets.length === 0 && <div role="status" className="border-b border-[var(--border)] px-4 py-3 text-sm text-[var(--accent-warn)]">暂无可用的未复权快照。请由管理员通过授权数据导入流程准备沪深主板日线；研究用前复权行情不能用于撮合。</div>}

        {showCreate && availability.datasets.length > 0 && <form aria-label="新建回放账户" onSubmit={createAccount} className="grid shrink-0 gap-4 border-b border-[var(--border)] bg-[var(--bg-secondary)] p-4 md:grid-cols-3">
          <div className="space-y-3"><label className="block text-xs text-[var(--text-muted)]">账户名称<Input aria-label="回放账户名称" value={name} maxLength={64} disabled={locked} onChange={(event) => setName(event.target.value)} className="mt-1" /></label><label className="block text-xs text-[var(--text-muted)]">初始模拟资金（元）<Input aria-label="初始模拟资金" type="number" min="100" max="1000000000" step="0.01" required value={initialCash} disabled={locked} onChange={(event) => setInitialCash(event.target.value)} className="mt-1" /></label><p className="text-xs text-[var(--text-muted)]">100至10亿元。新建独立账户，保留已有账户资金与历史。</p></div>
          <div><p className="mb-2 text-xs text-[var(--text-muted)]">选择固定版本快照（{datasetIds.length}/20）</p><div className="max-h-36 space-y-2 overflow-auto rounded border border-[var(--border)] p-2">{availability.datasets.map((item) => <label key={item.id} className="flex cursor-pointer items-start gap-2 text-xs"><input type="checkbox" checked={datasetIds.includes(item.id)} disabled={locked || (!datasetIds.includes(item.id) && datasetIds.length >= 20)} onChange={(event) => setDatasetIds((previous) => event.target.checked ? [...previous, item.id] : previous.filter((id) => id !== item.id))} className="mt-0.5" /><span>{item.code} {item.name}<span className="mt-0.5 block text-[var(--text-muted)]">{item.start_date} 至 {item.end_date} · {item.source}<br />版本 {item.version.slice(0, 12)} · 不复权</span></span></label>)}</div></div>
          <div className="space-y-3"><label className="block text-xs text-[var(--text-muted)]">起始交易日<Input aria-label="回放起始交易日" type="date" required value={startDate} disabled={locked} onChange={(event) => setStartDate(event.target.value)} className="mt-1" /></label><p className="text-xs leading-5 text-[var(--text-muted)]">佣金万三，最低5元；滑点千分之一。税费按成交日期规则计算。起始日需在所选快照共同范围内且为有效交易日。</p><Button type="submit" disabled={locked}>{busy ? "正在创建…" : "创建独立回放账户"}</Button></div>
        </form>}

        {currentView && <div aria-label="回放账户摘要" className="grid shrink-0 grid-cols-2 gap-3 border-b border-[var(--border)] bg-[var(--bg-secondary)] px-4 py-3 md:grid-cols-4 xl:grid-cols-6">
          {[{ label: "总资产（元）", value: currentView.equity }, { label: "可用资金（元）", value: currentView.available_cash }, { label: "委托冻结（元）", value: currentView.reserved_cash }, { label: "累计盈亏（元）", value: currentView.total_pnl, pnl: true }, { label: "累计费用（元）", value: currentView.fees_paid }].map((item) => <div key={item.label}><p className="text-[11px] text-[var(--text-muted)]">{item.label}</p><p className="mt-1 font-num text-base font-semibold" style={item.pnl ? { color: pnlColor(item.value) } : undefined}>{money(item.value)}</p></div>)}
          <div><p className="text-[11px] text-[var(--text-muted)]">当前回放交易日</p><p className="mt-1 font-num text-base font-semibold">{currentView.current_date}</p><p className="text-[10px] text-[var(--text-muted)]">{currentView.status === "completed" ? "数据已到末日" : "当日收盘后"} · 版本 {currentView.revision}</p></div>
        </div>}

        {!accountId ? <div className="flex flex-1 items-center justify-center p-8 text-sm text-[var(--text-muted)]">暂无已选择的股票回放账户。请选择已有账户或新建账户。</div> : loading || !currentView ? <div role="status" className="flex flex-1 items-center justify-center gap-2 p-8 text-sm text-[var(--text-muted)]">{loading ? <><Loader2 className="h-4 w-4 animate-spin" />正在载入当前账户…</> : "账户数据未加载，请刷新或选择其他账户。"}</div> : tab !== "overview" ? <section aria-label={`${page.label}账本`} className="min-w-0 shrink-0 space-y-3 p-4">
          <h2 className="text-sm font-semibold">{currentView.name} · {page.label}</h2>
          <p className="text-xs leading-5 text-[var(--text-muted)]">截至回放交易日 {currentView.current_date} · 账户版本 {currentView.revision}。{tab === "positions" ? "次日可卖数量已扣除待卖委托，买入遵守T+1；买卖与日期推进请进入交易回放。" : tab === "orders" ? "本页查看和撤销委托；提交新委托与日期推进请进入交易回放。" : "金额按当前账户全部历史计算；日期推进请进入交易回放。"}</p>
          {tab === "orders" && <StockReplayOrderHistory accountId={currentView.id} revision={currentView.revision} currentDate={currentView.current_date} disabled={loading || locked} />}
          <div className="overflow-x-auto rounded border border-[var(--border)]">
            {tab === "orders" ? <ReplayOrders key={currentView.id} view={currentView} disabled={!canTrade} onCancel={cancelOrder} upColor={upColor} downColor={downColor} /> : tab === "positions" ? <ReplayPositions view={currentView} upColor={upColor} downColor={downColor} /> : <ReplayAssets view={currentView} upColor={upColor} downColor={downColor} />}
          </div>
        </section> : <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
          <aside aria-label="回放股票列表" className="flex shrink-0 gap-1 overflow-auto border-b border-[var(--border)] bg-[var(--bg-secondary)] p-2 lg:w-[176px] lg:flex-col lg:border-r lg:border-b-0">
            <p className="hidden px-2 py-2 text-xs text-[var(--text-muted)] lg:block">账户标的 · 固定快照</p>
            {currentView.instruments.map((item) => {
              const position = currentView.positions.find((holding) => holding.code === item.code)
              return <button key={item.code} type="button" aria-pressed={selectedCode === item.code} disabled={locked} onClick={() => { setCode(item.code); setMessage(null) }} className={cn("min-w-[140px] rounded px-3 py-3 text-left text-sm transition-colors disabled:opacity-50", selectedCode === item.code ? "bg-[var(--primary)]/10 text-[var(--primary)]" : "hover:bg-[var(--bg-tertiary)]")}><span className="block font-medium">{item.name}</span><span className="mt-1 block font-num text-xs text-[var(--text-muted)]">{item.code} · {position ? `${position.quantity}股` : "未持仓"}</span></button>
            })}
          </aside>

          <div className="flex min-w-0 flex-1 flex-col">
            <div className="min-h-[440px] flex-1 border-b border-[var(--border)]">
              {currentView.chart.code === selectedCode && currentView.chart.bars.length > 0 ? <StockKlineChart bars={currentView.chart.bars} symbolLabel={`${selectedCode} ${selectedInstrument?.name ?? ""}`} upColor={upColor} downColor={downColor} priceAdjustmentLabel="不复权（撮合价）" volumeUnit="股" changeBasis="unavailable" /> : <div role="status" className="flex min-h-[440px] items-center justify-center p-6 text-sm text-[var(--text-muted)]">当前游标前没有可展示的日线数据。</div>}
            </div>
            <div className="flex min-h-[240px] shrink-0 flex-col">
              <div className="space-y-3 p-4 text-sm"><div className="flex flex-wrap gap-x-6 gap-y-2"><span>回放区间 <b className="font-num">{currentView.start_date} 至 {currentView.end_date}</b></span><span>持仓 {currentView.positions.length} 只</span><span>展示委托 {currentView.orders.length} 笔</span><span>已实现盈亏 <b className="font-num" style={{ color: pnlColor(currentView.realized_pnl) }}>{money(currentView.realized_pnl)} 元</b></span></div><p className="text-xs leading-5 text-[var(--text-muted)]">在当前交易日收盘后提交委托，推进至下一交易日后按开盘规则撮合并以收盘价估值。限价只按开盘价判断；买入涨停、卖出跌停、T+1、资金及成交量约束可能导致拒绝，请查看每笔原因。日线无法还原盘口排队。</p><p className="text-xs text-[var(--text-muted)]">计算方法 {currentView.method_version} · 固定数据版本 {currentView.data_versions.length} 个 · 展示全部待处理委托（最多100笔）及最近200笔历史委托，资产由全部历史计算</p></div>
              <h2 className="px-4 py-2 text-sm font-semibold">当前委托与近期记录</h2>
              <div className="max-h-[380px] overflow-auto">
                <ReplayOrders key={currentView.id} view={currentView} disabled={!canTrade} onCancel={cancelOrder} upColor={upColor} downColor={downColor} />
              </div>
            </div>
          </div>

          <aside aria-label="回放下单与日期推进" className="w-full shrink-0 border-t border-[var(--border)] bg-[var(--bg-secondary)] lg:w-[280px] lg:overflow-y-auto lg:border-t-0 lg:border-l xl:w-[300px]">
            <div className="space-y-3 border-b border-[var(--border)] p-4"><div className="flex items-center justify-between"><h2 className="text-sm font-semibold">推进回放</h2><span className="rounded bg-[var(--bg-tertiary)] px-2 py-1 text-[10px] text-[var(--text-muted)]">日线 · 收盘后</span></div><p className="font-num text-xl font-semibold">{currentView.current_date}</p><p className="text-xs text-[var(--text-muted)]">{currentView.status === "completed" ? "已到达快照末日，可查看历史或新建账户。" : `下一次推进将处理 ${currentView.orders.filter((item) => item.status === "pending").length} 笔待处理委托。`}</p><Button className="w-full" disabled={!canTrade} onClick={advance}>{busy && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}推进下一交易日<ArrowRight className="ml-2 h-4 w-4" /></Button></div>
            <form onSubmit={submitOrder} className="space-y-4 p-4">
              <div><h2 className="text-sm font-semibold">股票委托</h2><p className="mt-1 text-xs text-[var(--text-muted)]">{selectedCode} {selectedInstrument?.name} · 下一交易日开盘撮合</p></div>
              <div className="grid grid-cols-2 gap-2">{(["buy", "sell"] as const).map((item) => <button key={item} type="button" aria-pressed={direction === item} disabled={locked} onClick={() => setDirection(item)} className="rounded border px-3 py-2 text-sm font-medium" style={{ color: item === "buy" ? upColor : downColor, borderColor: direction === item ? (item === "buy" ? upColor : downColor) : "var(--border)", backgroundColor: direction === item ? "var(--bg-tertiary)" : undefined }}>{item === "buy" ? "买入" : "卖出"}</button>)}</div>
              <label className="block text-xs text-[var(--text-muted)]">委托类型<select aria-label="股票委托类型" value={orderType} disabled={locked} onChange={(event) => setOrderType(event.target.value as "market" | "limit")} className={cn(fieldClass, "mt-1")}><option value="market">市价（下一开盘）</option><option value="limit">限价（开盘满足时）</option></select></label>
              {orderType === "limit" && <label className="block text-xs text-[var(--text-muted)]">限价（元/股）<Input aria-label="股票限价" type="number" min="0.01" max="1000000" step="0.01" required value={limitPrice} disabled={locked} onChange={(event) => setLimitPrice(event.target.value)} className="mt-1" /></label>}
              <label className="block text-xs text-[var(--text-muted)]">委托数量（股，100股的整数倍）<Input aria-label="股票委托股数" type="number" min="100" max="1000000" step="100" required value={quantity} disabled={locked} onChange={(event) => setQuantity(event.target.value)} className="mt-1" /></label>
              <div className="flex justify-between text-xs text-[var(--text-muted)]"><span>次日可卖数量</span><span className="font-num">{currentView.positions.find((item) => item.code === selectedCode)?.available_quantity ?? 0} 股</span></div>
              <Button type="submit" className="w-full text-white" style={{ backgroundColor: direction === "buy" ? upColor : downColor }} disabled={!canTrade}>提交{direction === "buy" ? "买入" : "卖出"}委托</Button>
              <p className="text-xs leading-5 text-[var(--text-muted)]">百股买卖 · 禁止裸卖空 · 买入T+1可卖。次日可卖数量已扣除待卖委托。图表仅展示回放日期及以前的数据，价格与成交量来自账户未复权快照。</p>
            </form>
          </aside>
        </div>}
      </>}
    </section>
  )
}

function ReplayOrders({ view, disabled, onCancel, upColor, downColor }: { view: ReplayView; disabled: boolean; onCancel: (id: string) => void; upColor: string; downColor: string }): React.JSX.Element {
  const [page, setPage] = useState(0)
  const rows = view.orders.slice(page * 20, (page + 1) * 20)
  return <>
    <p className="px-3 py-2 text-xs text-[var(--text-muted)]">全部待处理委托（最多100笔）及最近200笔历史委托 · 资产与持仓以全部历史计算</p>
    <table className="w-full whitespace-nowrap text-left text-xs">
      <thead className="bg-[var(--bg-secondary)] text-[var(--text-muted)]">
        <tr>{["提交日期", "股票", "方向", "股数", "委托价", "状态 / 原因", "成交日期", "成交价", "费用（元）", "操作"].map((item) => <th key={item} className="px-3 py-2 font-normal">{item}</th>)}</tr>
      </thead>
      <tbody>
        {rows.length ? rows.map((item) => <tr key={item.id} className="border-b border-[var(--border)]">
          <td className="px-3 py-2 font-num">{item.submitted_on}</td>
          <td className="px-3 py-2">{item.code} {view.instruments.find((instrument) => instrument.code === item.code)?.name}</td>
          <td className="px-3 py-2" style={{ color: item.direction === "buy" ? upColor : downColor }}>{item.direction === "buy" ? "买入" : "卖出"}</td>
          <td className="px-3 py-2 font-num">{item.quantity}</td>
          <td className="px-3 py-2 font-num">{item.order_type === "market" ? "市价" : item.limit_price !== null ? money(item.limit_price, 2) : "--"}</td>
          <td className="px-3 py-2"><span>{statusLabels[item.status]}</span>{item.reason && <span className="mt-1 block max-w-64 whitespace-normal text-[var(--accent-warn)]">{replayOrderReason(item.reason)}</span>}</td>
          <td className="px-3 py-2 font-num">{item.filled_on ?? "--"}</td>
          <td className="px-3 py-2 font-num">{item.filled_price === null ? "--" : money(item.filled_price, 4)}</td>
          <td className="px-3 py-2 font-num">{money(item.fees)}</td>
          <td className="px-3 py-2">{item.status === "pending" && <Button size="sm" variant="ghost" disabled={disabled} onClick={() => onCancel(item.id)}>撤单</Button>}</td>
        </tr>) : <tr><td colSpan={10} className="px-4 py-8 text-center text-[var(--text-muted)]">暂无委托；提交后在下一交易日开盘撮合。</td></tr>}
      </tbody>
    </table>
    {view.orders.length > 20 && <div className="flex items-center justify-end gap-2 px-3 py-2 text-xs">
      <Button variant="ghost" size="sm" disabled={page === 0} onClick={() => setPage(page - 1)}>上一页</Button>
      <span>{page + 1} / {Math.ceil(view.orders.length / 20)}</span>
      <Button variant="ghost" size="sm" disabled={(page + 1) * 20 >= view.orders.length} onClick={() => setPage(page + 1)}>下一页</Button>
    </div>}
  </>
}

function ReplayPositions({ view, upColor, downColor }: { view: ReplayView; upColor: string; downColor: string }): React.JSX.Element {
  return <table className="w-full whitespace-nowrap text-left text-xs"><thead className="bg-[var(--bg-secondary)] text-[var(--text-muted)]"><tr>{["股票", "持有（股）", "次日可卖（股）", "成本（元/股）", "收盘价（元）", "市值（元）", "浮动盈亏（元）"].map((item) => <th key={item} className="px-3 py-2 font-normal">{item}</th>)}</tr></thead><tbody>{view.positions.length ? view.positions.map((item) => <tr key={item.code} className="border-b border-[var(--border)]"><td className="px-3 py-3">{item.code} {item.name}</td><td className="px-3 py-3 font-num">{item.quantity}</td><td className="px-3 py-3 font-num">{item.available_quantity}</td><td className="px-3 py-3 font-num">{money(item.cost_price, 4)}</td><td className="px-3 py-3 font-num">{money(item.last_price, 4)}</td><td className="px-3 py-3 font-num">{money(item.market_value)}</td><td className="px-3 py-3 font-num" style={{ color: item.unrealized_pnl.startsWith("-") ? downColor : upColor }}>{money(item.unrealized_pnl)}</td></tr>) : <tr><td colSpan={7} className="px-4 py-8 text-center text-[var(--text-muted)]">暂无股票持仓；待买入委托推进成交后显示。</td></tr>}</tbody></table>
}

function ReplayAssets({ view, upColor, downColor }: { view: ReplayView; upColor: string; downColor: string }): React.JSX.Element {
  return <div className="p-4"><dl className="grid grid-cols-2 gap-x-6 gap-y-4 md:grid-cols-4">{[{ label: "初始模拟资金", value: view.initial_cash }, { label: "现金余额", value: view.cash }, { label: "可用资金", value: view.available_cash }, { label: "冻结资金", value: view.reserved_cash }, { label: "总资产", value: view.equity }, { label: "累计盈亏", value: view.total_pnl, pnl: true }, { label: "已实现盈亏", value: view.realized_pnl, pnl: true }, { label: "累计费用", value: view.fees_paid }].map((item) => <div key={item.label}><dt className="text-xs text-[var(--text-muted)]">{item.label}（元）</dt><dd className="mt-1 font-num text-sm font-semibold" style={item.pnl ? { color: item.value.startsWith("-") ? downColor : upColor } : undefined}>{money(item.value)}</dd></div>)}</dl><details className="mt-4 text-xs text-[var(--text-muted)]"><summary className="cursor-pointer">数据与方法追溯</summary><p className="mt-2">计算方法：{view.method_version}；币种：{view.currency}；价格：不复权；成交量：股。</p><ul className="mt-2 space-y-1 break-all">{view.data_versions.map((version) => <li key={version}>{version}</li>)}</ul></details></div>
}
