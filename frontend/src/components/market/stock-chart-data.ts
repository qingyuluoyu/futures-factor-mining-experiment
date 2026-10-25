import type { StockBar } from "@/lib/stock-api"

export type StockChartPeriod = "day" | "week" | "month"

export function prepareStockBars(bars: StockBar[]): StockBar[] {
  const dates = new Map<string, StockBar>()
  for (const bar of bars) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(bar.time)) continue
    const parsed = new Date(`${bar.time}T00:00:00Z`)
    if (!Number.isFinite(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== bar.time) continue
    if (![bar.open, bar.high, bar.low, bar.close].every((value) => Number.isFinite(value) && value > 0)) continue
    if (!Number.isFinite(bar.volume) || bar.volume < 0) continue
    if (bar.high < Math.max(bar.open, bar.close) || bar.low > Math.min(bar.open, bar.close) || bar.high < bar.low) continue
    dates.set(bar.time, { ...bar })
  }
  return [...dates.values()].sort((first, second) => first.time.localeCompare(second.time))
}

export function aggregateStockBars(bars: StockBar[], period: StockChartPeriod, truncatedStart = false): StockBar[] {
  const cleaned = prepareStockBars(bars)
  if (period === "day") return cleaned
  const groups = new Map<string, StockBar>()
  for (const bar of cleaned) {
    const date = new Date(`${bar.time}T00:00:00Z`)
    if (period === "week") date.setUTCDate(date.getUTCDate() - (date.getUTCDay() + 6) % 7)
    const key = period === "month" ? bar.time.slice(0, 7) : date.toISOString().slice(0, 10)
    const previous = groups.get(key)
    groups.set(key, previous ? {
      time: bar.time,
      open: previous.open,
      close: bar.close,
      high: Math.max(previous.high, bar.high),
      low: Math.min(previous.low, bar.low),
      volume: previous.volume + bar.volume,
    } : { ...bar })
  }
  return [...groups.values()].slice(truncatedStart ? 1 : 0)
}

export function stockVisibleRange(count: number, visible: number | "all"): { from: number; to: number } | null {
  if (count <= 0) return null
  return { from: visible === "all" ? 0 : Math.max(0, count - visible), to: count + 2 }
}

export const STOCK_CHART_METHOD_VERSION = "stock-chart-1.0.0"
export const STOCK_CHART_METHOD_DESCRIPTION = "MACD(12,26,9)：EMA以首个收盘价初始化、柱值为2×(DIF−DEA)；KDJ(9,3,3)：首个有效RSV初始化、零价差不计算；RSI(6,14,24)：Wilder平滑、首段简单均值、无涨跌取50；BOLL(20,2)：样本标准差；均线包含本根，量能对比不含本根。指标仅使用已载入K线。"

export interface StockIndicatorPoint {
  time: string
  value: number
}

export interface StockMACDPoint {
  time: string
  dif: number
  dea: number
  macd: number
}

export interface StockKDJPoint {
  time: string
  k: number
  d: number
  j: number
}

export interface StockBOLLPoint {
  time: string
  middle: number
  upper: number
  lower: number
}

function validPeriod(period: number): boolean {
  return Number.isInteger(period) && period > 0
}

export function stockSMA(bars: StockBar[], period = 5, field: "close" | "volume" = "close"): StockIndicatorPoint[] {
  if (!validPeriod(period)) return []
  const result: StockIndicatorPoint[] = []
  for (let index = period - 1; index < bars.length; index++) {
    const values = bars.slice(index - period + 1, index + 1).map((bar) => bar[field])
    if (!values.every((value) => Number.isFinite(value) && (field === "volume" ? value >= 0 : value > 0))) continue
    result.push({ time: bars[index].time, value: values.reduce((sum, value) => sum + value, 0) / period })
  }
  return result
}

export function stockMACD(bars: StockBar[], fastPeriod = 12, slowPeriod = 26, signalPeriod = 9): StockMACDPoint[] {
  if (![fastPeriod, slowPeriod, signalPeriod].every(validPeriod) || fastPeriod >= slowPeriod) return []
  const result: StockMACDPoint[] = []
  let fast: number | null = null
  let slow: number | null = null
  let signal: number | null = null
  for (const bar of bars) {
    if (!Number.isFinite(bar.close) || bar.close <= 0) {
      fast = slow = signal = null
      continue
    }
    fast = fast === null ? bar.close : fast + (bar.close - fast) * 2 / (fastPeriod + 1)
    slow = slow === null ? bar.close : slow + (bar.close - slow) * 2 / (slowPeriod + 1)
    const difference: number = fast - slow
    signal = signal === null ? difference : signal + (difference - signal) * 2 / (signalPeriod + 1)
    result.push({ time: bar.time, dif: difference, dea: signal, macd: 2 * (difference - signal) })
  }
  return result
}

export function stockKDJ(bars: StockBar[], period = 9, kPeriod = 3, dPeriod = 3): StockKDJPoint[] {
  if (![period, kPeriod, dPeriod].every(validPeriod)) return []
  const result: StockKDJPoint[] = []
  let previousK: number | null = null
  let previousD: number | null = null
  for (let index = period - 1; index < bars.length; index++) {
    const window = bars.slice(index - period + 1, index + 1)
    if (!window.every((bar) => [bar.high, bar.low, bar.close].every((value) => Number.isFinite(value) && value > 0))) continue
    const high = Math.max(...window.map((bar) => bar.high))
    const low = Math.min(...window.map((bar) => bar.low))
    if (high <= low) continue
    const relative = (bars[index].close - low) / (high - low) * 100
    const currentK: number = previousK === null ? relative : previousK + (relative - previousK) / kPeriod
    const currentD: number = previousD === null ? currentK : previousD + (currentK - previousD) / dPeriod
    previousK = currentK
    previousD = currentD
    result.push({ time: bars[index].time, k: currentK, d: currentD, j: 3 * currentK - 2 * currentD })
  }
  return result
}

export function stockRSI(bars: StockBar[], period = 14): StockIndicatorPoint[] {
  if (!validPeriod(period) || bars.length <= period) return []
  const result: StockIndicatorPoint[] = []
  let gain = 0
  let loss = 0
  let changes = 0
  for (let index = 1; index < bars.length; index++) {
    const previous = bars[index - 1].close
    const current = bars[index].close
    if (![previous, current].every((value) => Number.isFinite(value) && value > 0)) {
      gain = loss = changes = 0
      continue
    }
    const difference = current - previous
    if (changes < period) {
      gain += Math.max(difference, 0) / period
      loss += Math.max(-difference, 0) / period
      changes++
      if (changes < period) continue
    } else {
      gain = (gain * (period - 1) + Math.max(difference, 0)) / period
      loss = (loss * (period - 1) + Math.max(-difference, 0)) / period
    }
    const value = gain + loss === 0 ? 50 : gain / (gain + loss) * 100
    result.push({ time: bars[index].time, value })
  }
  return result
}

export function stockBOLL(bars: StockBar[], period = 20, multiplier = 2): StockBOLLPoint[] {
  if (!validPeriod(period) || period < 2 || !Number.isFinite(multiplier) || multiplier <= 0) return []
  const result: StockBOLLPoint[] = []
  for (let index = period - 1; index < bars.length; index++) {
    const values = bars.slice(index - period + 1, index + 1).map((bar) => bar.close)
    if (!values.every((value) => Number.isFinite(value) && value > 0)) continue
    const middle = values.reduce((sum, value) => sum + value, 0) / period
    const deviation = Math.sqrt(values.reduce((sum, value) => sum + (value - middle) ** 2, 0) / (period - 1))
    result.push({ time: bars[index].time, middle, upper: middle + multiplier * deviation, lower: middle - multiplier * deviation })
  }
  return result
}

export function stockVolumeRatios(bars: StockBar[], period = 1): StockIndicatorPoint[] {
  if (!validPeriod(period) || period > 20) return []
  const result: StockIndicatorPoint[] = []
  for (let index = period; index < bars.length; index++) {
    const values = bars.slice(index - period, index).map((bar) => bar.volume)
    const volume = bars[index].volume
    if (!Number.isFinite(volume) || volume < 0 || !values.every((value) => Number.isFinite(value) && value >= 0)) continue
    const average = values.reduce((sum, value) => sum + value, 0) / period
    if (average > 0) result.push({ time: bars[index].time, value: volume / average })
  }
  return result
}

type StockLogicalRange = { from: number; to: number }

export function stockVisibleBarCount(range: StockLogicalRange | null, count: number): number {
  if (!range || count <= 0 || ![range.from, range.to].every(Number.isFinite)) return 0
  return Math.max(0, Math.min(count - 1, Math.ceil(range.to)) - Math.max(0, Math.floor(range.from)) + 1)
}

function boundStockRange(range: StockLogicalRange, count: number): StockLogicalRange {
  const width = Math.min(count + 2, Math.max(8, range.to - range.from))
  const from = Math.min(Math.max(0, range.from), count + 2 - width)
  return { from, to: from + width }
}

export function stockZoomRange(range: StockLogicalRange, factor: number, count: number): StockLogicalRange | null {
  if (count <= 0 || !Number.isFinite(factor) || factor <= 0) return null
  const middle = (range.from + range.to) / 2
  const width = (range.to - range.from) * factor
  return boundStockRange({ from: middle - width / 2, to: middle + width / 2 }, count)
}

export function stockRestoreRange(previous: StockLogicalRange | null, previousCount: number, count: number, visible: number | "all"): StockLogicalRange | null {
  if (!previous || ![previous.from, previous.to].every(Number.isFinite)) return stockVisibleRange(count, visible)
  if (count <= 0) return null
  const shift = previous.to >= previousCount - 1 ? count - previousCount : 0
  return boundStockRange({ from: previous.from + shift, to: previous.to + shift }, count)
}
