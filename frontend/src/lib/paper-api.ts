/** 模拟交易 / 虚拟资金 API 客户端 */

/** 模拟账户概览 */
export interface PaperAccountSummary {
  valuation?: {
    method_version: "paper-cache-mark-v1"
    status: "cache_marked" | "no_positions" | "unavailable"
    trading_mode: "live" | "virtual"
    currency: string
    book_equity: string
    equity: string | null
    unrealized_pnl: string | null
    today_pnl: string | null
    risk_rate: string | null
    calculated_at: number
    earliest_quote_received_at: number | null
    issues: string[]
    note: string
  }
  account_id: string
  currency: string
  balance: number
  available_margin: number
  frozen_margin: number
  position_margin: number
  total_equity: number
  equity_basis?: "book_balance_plus_frozen_excludes_floating"
  legacy_pnl_fields_available?: false
  realized_pnl: number
  unrealized_pnl: number
  today_pnl: number
  total_claimed: number
  total_deposit: number
  total_withdraw: number
  risk_rate: number
  last_claim_month: string | null
  current_claim_month: string
  can_claim: boolean
  claim_amount: number
  updated_at: string | null
  funding_policy?: "custom"
  custom_deposit_enabled?: boolean
  simulation_only?: boolean
  funding_method_version?: string
}

export interface PaperDepositRequest {
  amount: string
  idempotency_key: string
}

export interface PendingPaperDeposit {
  sessionToken: string
  body: PaperDepositRequest
}

export class PaperRequestError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

export function isDefinitivePaperDepositError(error: unknown): boolean {
  return error instanceof PaperRequestError && [400, 401, 403, 404, 405, 409, 413, 415, 422].includes(error.status)
}

export function normalizePaperDepositAmount(value: string): string {
  if (!/^(0|[1-9][0-9]{0,15})(\.[0-9]{1,2})?$/.test(value) || !/[1-9]/.test(value)) {
    throw new Error("请输入大于零的金额，最多两位小数，且不超过金额存储范围")
  }
  const [whole, fraction = ""] = value.split(".")
  return `${whole}.${fraction.padEnd(2, "0")}`
}

export function preparePaperDeposit(
  pending: PendingPaperDeposit | null,
  amount: string,
  sessionToken: string
): PendingPaperDeposit {
  const normalized = normalizePaperDepositAmount(amount)
  if (pending?.sessionToken === sessionToken) {
    if (pending.body.amount !== normalized) throw new Error("上一笔充值待确认，请先重试原金额")
    return pending
  }
  return { sessionToken, body: { amount: normalized, idempotency_key: crypto.randomUUID() } }
}

/** 资金流水项 */
export interface PaperLedgerItem {
  id: string
  time: string
  type: string
  amount: number
  balance: number
  description: string
  ref_type: string | null
  ref_id: string | null
}

/** 模拟委托 */
export interface PaperOrderItem {
  id: string
  symbol: string
  symbol_name: string
  direction: "buy" | "sell" | string
  offset: "open" | "close" | string
  order_type: "limit" | "market" | string
  price: number
  quantity: number
  filled_qty: number
  status: string
  /** 委托来源：manual/ai/quant */
  source: "manual" | "ai" | "quant" | string
  frozen_margin: number
  fee: number
  realized_pnl: number
  created_at: string
  updated_at: string
  filled_at: string | null
}

/** 模拟持仓 */
export interface PaperPositionItem {
  id: string
  symbol: string
  symbol_name: string
  direction: "long" | "short" | string
  source: "manual" | "ai" | "quant" | string
  quantity: number
  available_quantity: number
  avg_price: number
  margin: number
  realized_pnl: number
  multiplier: number
  margin_rate: number
  updated_at: string
}

/** 下单请求 */
export interface PlacePaperOrderRequest {
  symbol: string
  direction: "buy" | "sell"
  offset: "open" | "close"
  source?: "manual" | "ai" | "quant"
  order_type: "limit" | "market"
  price: number
  quantity: number
  symbol_name: string
  multiplier: number | null
  margin_rate: number | null
}

const API_BASE = (process.env.NEXT_PUBLIC_API_URL ?? "").replace(/\/$/, "")

/** 带鉴权的 JSON 请求 */
async function paperRequest<T>(path: string, options: RequestInit = {}, sessionToken?: string): Promise<T> {
  const token =
    typeof window !== "undefined" ? localStorage.getItem("access_token") : null
  const verifySession = () => {
    if (!token || (sessionToken !== undefined && sessionToken !== token) || localStorage.getItem("access_token") !== token) {
      throw new Error("登录会话已变更，请重新加载账户")
    }
  }
  verifySession()
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...((options.headers as Record<string, string>) ?? {}),
  }
  if (token) {
    headers["Authorization"] = `Bearer ${token}`
  }

  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers,
    signal: options.signal
      ? AbortSignal.any([options.signal, AbortSignal.timeout(20_000)])
      : AbortSignal.timeout(20_000),
  })
  verifySession()

  if (response.status === 401 && typeof window !== "undefined") {
    window.location.href = "/login"
    throw new Error("认证过期")
  }

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: "请求失败" }))
    verifySession()
    let detailText = `请求失败: ${response.status}`
    if (typeof error?.detail === "string") {
      detailText = error.detail
    } else if (Array.isArray(error?.detail)) {
      detailText = error.detail
        .map((item: { msg?: string }) => item?.msg ?? JSON.stringify(item))
        .join("; ")
    }
    throw new PaperRequestError(detailText, response.status)
  }

  const result = await response.json() as T
  verifySession()
  return result
}

/** 获取模拟账户 */
export async function getPaperAccount(sessionToken?: string): Promise<PaperAccountSummary> {
  return paperRequest<PaperAccountSummary>("/api/paper/account", {}, sessionToken)
}

export async function depositPaperFunds(body: PaperDepositRequest, sessionToken: string): Promise<PaperAccountSummary> {
  return paperRequest<PaperAccountSummary>("/api/paper/account/deposit", {
    method: "POST",
    body: JSON.stringify(body),
  }, sessionToken)
}

/** 领取本月虚拟练习金 */
export async function claimPaperFunds(): Promise<PaperAccountSummary> {
  return paperRequest<PaperAccountSummary>("/api/paper/account/claim", {
    method: "POST",
  })
}

/** 资金流水 */
export async function getPaperLedgers(
  limit: number,
  offset: number,
  sessionToken?: string
): Promise<{ total: number; items: PaperLedgerItem[] }> {
  return paperRequest<{ total: number; items: PaperLedgerItem[] }>(
    `/api/paper/ledgers?limit=${limit}&offset=${offset}`, {}, sessionToken
  )
}

/** 委托列表 */
export async function getPaperOrders(
  status: string | null,
  limit: number,
  offset: number,
  sessionToken?: string
): Promise<{ total: number; items: PaperOrderItem[] }> {
  const qs = new URLSearchParams()
  qs.set("limit", String(limit))
  qs.set("offset", String(offset))
  if (status) {
    qs.set("status", status)
  }
  return paperRequest<{ total: number; items: PaperOrderItem[] }>(
    `/api/paper/orders?${qs.toString()}`, {}, sessionToken
  )
}

/** 模拟下单 */
export async function placePaperOrder(
  body: PlacePaperOrderRequest,
  sessionToken?: string
): Promise<PaperOrderItem> {
  return paperRequest<PaperOrderItem>("/api/paper/orders", {
    method: "POST",
    body: JSON.stringify(body),
  }, sessionToken)
}

/** 撤单 */
export async function cancelPaperOrder(orderId: string, sessionToken?: string): Promise<PaperOrderItem> {
  return paperRequest<PaperOrderItem>(`/api/paper/orders/${orderId}`, {
    method: "DELETE",
  }, sessionToken)
}

/** 持仓列表 */
export async function getPaperPositions(sessionToken?: string): Promise<{
  total: number
  total_margin: number
  items: PaperPositionItem[]
}> {
  return paperRequest("/api/paper/positions", {}, sessionToken)
}

/** 用户交易参数 */
export interface PaperTradeSettings {
  margin_scale: number
  fee_scale: number
  margin_rate_override: number | null
  fee_mode_override: string | null
  open_fee_override: number | null
  close_fee_override: number | null
  updated_at: string | null
}

/** 品种默认规格 */
export interface ProductSpecItem {
  code: string
  name: string
  multiplier: number
  margin_rate: number
  fee_mode: string
  open_fee: number
  close_fee: number
  description: string
  /** 最小变动价位（rb=1, cu=10, au=0.02） */
  tick_size: number
  /** 价格显示小数位（tick>=1 为 0，au=0.02 为 2） */
  decimal_places: number
}

/** 读取用户交易参数 */
export async function getPaperSettings(): Promise<PaperTradeSettings> {
  return paperRequest<PaperTradeSettings>("/api/paper/settings")
}

/** 更新用户交易参数 */
export async function updatePaperSettings(body: {
  margin_scale?: number | null
  fee_scale?: number | null
  margin_rate_override?: number | null
  fee_mode_override?: string | null
  open_fee_override?: number | null
  close_fee_override?: number | null
  clear_overrides?: boolean
}): Promise<PaperTradeSettings> {
  return paperRequest<PaperTradeSettings>("/api/paper/settings", {
    method: "PUT",
    body: JSON.stringify(body),
  })
}

/** 品种默认规格列表 */
export async function getProductSpecs(): Promise<{ items: ProductSpecItem[] }> {
  return paperRequest<{ items: ProductSpecItem[] }>("/api/paper/product-specs")
}

/** 预估保证金/手续费 */
export async function estimatePaperOrder(body: {
  symbol: string
  price: number
  quantity: number
  offset: "open" | "close"
}, sessionToken?: string): Promise<{
  margin: number
  fee: number
  total_needed: number
  multiplier: number
  margin_rate: number
  fee_mode: string
  fee_value: number
  notional: number
}> {
  return paperRequest("/api/paper/estimate", {
    method: "POST",
    body: JSON.stringify(body),
  }, sessionToken)
}
