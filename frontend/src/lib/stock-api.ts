/** A 股行情 API —— 双市场之 stock 侧（数据来自本地 Tushare 导入库） */

import { request } from "./api.ts"
import type { User } from "@/types"

export interface StockInstrument {
  code: string
  name: string
  exchange: string
  industry: string
  list_date: string | null
}

export interface StockIndustry {
  industry: string
  count: number
}

export interface StockBar {
  time: string
  open: number
  high: number
  low: number
  close: number
  volume: number
}

export interface StockKlineResponse {
  code: string
  name: string
  industry: string
  bars: StockBar[]
  has_more: boolean
  price_adjustment?: "none" | "qfq"
  source?: "teajoin_eod" | "local_import"
  volume_unit?: "lot_100_shares"
}

export interface StockValuationPoint {
  trade_date: string
  pe_ttm: number | null
  pb: number | null
  total_mv: number | null
  circ_mv: number | null
  turnover_rate: number | null
}

export interface StockDataStatus {
  instruments: number
  valuation_rows: number
  financial_rows: number
  kline_rows: number
  latest_trade_date: string | null
}

/** 股票池（可按行业过滤/关键词搜索） */
export async function getStockUniverseApi(
  industry?: string,
  keyword?: string,
): Promise<StockInstrument[]> {
  const params = new URLSearchParams()
  if (industry) params.set("industry", industry)
  if (keyword) params.set("keyword", keyword)
  params.set("limit", "500")
  const instruments: StockInstrument[] = []
  for (let offset = 0; offset < 10000; offset += 500) {
    params.set("offset", String(offset))
    const page = await request<StockInstrument[]>(`/api/stock/universe?${params.toString()}`)
    instruments.push(...page)
    if (page.length < 500) return instruments
  }
  throw new Error("股票池超过10000条，请缩小行业或搜索范围")
}

/** 行业列表（含股票数） */
export async function getStockIndustriesApi(): Promise<StockIndustry[]> {
  return request<StockIndustry[]>("/api/stock/industries")
}

/** A 股日线，复权与来源口径随响应返回 */
export async function getStockKlineApi(
  code: string,
  limit = 250,
  signal?: AbortSignal,
  source?: "legacy_auto" | "history_qfq" | "eod_raw",
): Promise<StockKlineResponse> {
  const timeout = AbortSignal.timeout(20000)
  return request(`/api/stock/kline?code=${encodeURIComponent(code)}&limit=${limit}${source ? `&source=${encodeURIComponent(source)}` : ""}`, { signal: signal ? AbortSignal.any([signal, timeout]) : timeout })
}

/** 每日估值（PE/PB/市值/换手率） */
export async function getStockValuationApi(
  code: string,
  days = 2,
): Promise<StockValuationPoint[]> {
  return request<StockValuationPoint[]>(
    `/api/stock/valuation?code=${encodeURIComponent(code)}`,
  ).then((rows) => rows.slice(-days))
}

/** A 股数据覆盖情况 */
export async function getStockDataStatusApi(): Promise<StockDataStatus> {
  return request<StockDataStatus>("/api/stock/data-status")
}

/** 切换市场偏好 */
export async function switchMarketTypeApi(
  market: "futures" | "stock",
  currentUser: User | null,
): Promise<User | null> {
  await request<{ market_type: string }>("/api/users/me/market", {
    method: "PUT",
    body: JSON.stringify({ market_type: market }),
  })
  return currentUser ? { ...currentUser, market_type: market } : currentUser
}

// ---------------------------------------------------------------- 因子研究

export interface StockFactorSpec {
  id: string
  label: string
  group: string
  formula: string
  direction: string
  source: string
  description: string
  method_version?: string
  policy_version?: string
  eligibility_rule?: string
  missing_value_policy?: string
  exclusion_rule?: string
}

export interface PreprocessOptions {
  winsorize: string
  standardize: boolean
  fillna: boolean
  industry_neutral: boolean
  mktcap_neutral: boolean
}

export interface FactorAnalysisResult {
  error?: string
  method_version?: string
  validation_status?: string
  validation_limits?: string[]
  factor_id: string
  label: string
  group: string
  direction: string
  ic?: {
    ic_mean: number
    ic_std: number
    ic_ir: number | null
    rank_ic_mean: number
    rank_ic_ir: number | null
    ic_positive_ratio: number
    periods: number
  }
  decay: { lag: number; rank_ic: number | null }[]
  quantile_returns: { group: number; avg_return: number }[]
  long_short_nav: { date: string; nav: number }[]
}

export interface BacktestResult {
  error?: string
  method_version?: string
  validation_status?: string
  validation_limits?: string[]
  metrics?: {
    total_return_pct: number
    annualized_return_pct: number
    annualized_vol_pct: number
    sharpe: number
    sortino: number
    calmar: number | null
    max_drawdown_pct: number
    win_rate: number
    turnover_rate: number
    periods: number
  }
  nav: { date: string; nav: number }[]
  trades: {
    date: string
    code: string
    name: string
    weight: number
    action: string
    factor_value: number | null
  }[]
  rebalance_count: number
  params: Record<string, unknown>
}

const DEFAULT_PREPROCESS: PreprocessOptions = {
  winsorize: "mad",
  standardize: true,
  fillna: true,
  industry_neutral: false,
  mktcap_neutral: false,
}

/** 因子目录 */
export async function getStockFactorsApi(): Promise<StockFactorSpec[]> {
  return request<StockFactorSpec[]>("/api/stock/factors")
}

/** 单因子检验：IC/IR、衰减、分组收益、多空净值 */
export async function analyzeStockFactorApi(
  factorId: string,
  options: {
    start_date?: string
    end_date?: string
    horizon?: number
    groups?: number
    preprocess?: Partial<PreprocessOptions>
  } = {},
): Promise<FactorAnalysisResult> {
  return request<FactorAnalysisResult>("/api/stock/factors/analysis", {
    method: "POST",
    body: JSON.stringify({
      factor_id: factorId,
      start_date: options.start_date ?? "20180101",
      end_date: options.end_date ?? "",
      horizon: options.horizon ?? 20,
      groups: options.groups ?? 5,
      preprocess: { ...DEFAULT_PREPROCESS, ...options.preprocess },
    }),
  })
}

/** 组合回测 */
export async function runStockBacktestApi(
  body: {
    factor_ids: string[]
    start_date?: string
    end_date?: string
    rebalance?: string
    top_n?: number
    weight?: string
    commission_rate?: number
    slippage_rate?: number
    preprocess?: Partial<PreprocessOptions>
  },
): Promise<BacktestResult> {
  return request<BacktestResult>("/api/stock/backtest", {
    method: "POST",
    body: JSON.stringify({
      ...body,
      preprocess: { ...DEFAULT_PREPROCESS, ...body.preprocess },
    }),
  })
}
