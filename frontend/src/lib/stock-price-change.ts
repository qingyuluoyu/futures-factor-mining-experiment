import type { StockBar } from "./stock-api"

export type StockChangeBasis = "vendor_ex_rights_pre_close" | "adjacent_loaded_adjusted_close" | "unavailable"
export type StockReferenceBar = Pick<StockBar, "close"> & { pre_close?: number | null }
export const STOCK_PRICE_CHANGE_METHOD_VERSION = "stock-price-change-2026-10-21.v1"

export function stockPriceChange(bar: StockReferenceBar | undefined, previous: Pick<StockBar, "close"> | undefined, basis: StockChangeBasis): { reference: number; change: number; changePct: number } | null {
  if (!bar || basis === "unavailable") return null
  const reference = basis === "vendor_ex_rights_pre_close" ? bar.pre_close : previous?.close
  if (typeof reference !== "number" || !Number.isFinite(reference) || reference <= 0 || !Number.isFinite(bar.close) || bar.close <= 0) return null
  const change = bar.close - reference
  const changePct = change / reference * 100
  return Number.isFinite(change) && Number.isFinite(changePct) ? { reference, change, changePct } : null
}

export function stockChangeBasisLabel(basis: StockChangeBasis): string {
  return basis === "vendor_ex_rights_pre_close" ? "供应商除权昨收参考；缺参考不计算，不用相邻原价代替"
    : basis === "adjacent_loaded_adjusted_close" ? "相邻已载入前复权K线变化，非官方当日涨跌幅"
    : "未提供可比参考价，涨跌幅不计算"
}

export function formatStockVolume(value: number): string {
  if (!Number.isFinite(value) || value < 0) return "—"
  if (value >= 100000000) return `${(value / 100000000).toFixed(2)}亿`
  if (value >= 10000) return `${(value / 10000).toFixed(2)}万`
  if (value > 0 && value < 0.000001) return "<0.000001"
  return value.toFixed(6).replace(/\.?0+$/, "") || "0"
}
