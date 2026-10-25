import type { StockBar } from "@/lib/stock-api"
import { formatStockVolume, stockPriceChange, type StockChangeBasis } from "@/lib/stock-price-change"
import { prepareStockBars, stockSMA, stockMACD, stockRSI, stockKDJ, stockBOLL } from "@/components/market/stock-chart-data"
import type { OHLC } from "./echarts-candlestick"

export function tickVolume(value: number | null | undefined): string {
  return value == null ? "—" : formatStockVolume(value)
}
export function tickPercent(value: number | null | undefined): string {
  return value == null || !Number.isFinite(value) ? "—" : `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`
}
export function toTickChartData(bars: StockBar[], basis: StockChangeBasis): OHLC[] {
  const data = prepareStockBars(bars)
  const numericMap = (rows: { time: string; value: number }[]) => new Map(rows.map((row) => [row.time, row.value]))
  const ma = [5, 10, 20, 60].map((period) => numericMap(stockSMA(data, period)))
  const rsi = [6, 14, 24].map((period) => numericMap(stockRSI(data, period)))
  const macd = new Map(stockMACD(data).map((row) => [row.time, row]))
  const kdj = new Map(stockKDJ(data).map((row) => [row.time, row]))
  const boll = new Map(stockBOLL(data).map((row) => [row.time, row]))
  return data.map((row, index) => ({
    date: row.time, open: row.open, high: row.high, low: row.low, close: row.close, volume: row.volume,
    change_pct: stockPriceChange(row, data[index - 1], basis)?.changePct ?? null,
    ma5: ma[0].get(row.time) ?? null, ma10: ma[1].get(row.time) ?? null,
    ma20: ma[2].get(row.time) ?? null, ma60: ma[3].get(row.time) ?? null,
    macd_dif: macd.get(row.time)?.dif ?? null, macd_dea: macd.get(row.time)?.dea ?? null, macd_hist: macd.get(row.time)?.macd ?? null,
    rsi_6: rsi[0].get(row.time) ?? null, rsi_14: rsi[1].get(row.time) ?? null, rsi_24: rsi[2].get(row.time) ?? null,
    kdj_k: kdj.get(row.time)?.k ?? null, kdj_d: kdj.get(row.time)?.d ?? null, kdj_j: kdj.get(row.time)?.j ?? null,
    boll_upper: boll.get(row.time)?.upper ?? null, boll_lower: boll.get(row.time)?.lower ?? null,
  }))
}
