import type { StockBar } from "@/lib/stock-api"
import { STOCK_CHART_METHOD_VERSION, stockVolumeRatios } from "./stock-chart-data.ts"

export type StockIndicatorKey = "showMA" | "showBOLL" | "showVolume" | "showMACD" | "showRSI" | "showKDJ"
export type StockIndicatorId = "ma" | "boll" | "vol" | "macd" | "rsi" | "kdj"

export interface StockIndicatorLine {
  readonly id: string
  readonly label: string
  readonly requiredBars: number
  readonly matureBars: number
}
export interface StockIndicatorDefinition {
  readonly key: StockIndicatorKey
  readonly id: StockIndicatorId
  readonly label: string
  readonly title: string
  readonly lines: readonly StockIndicatorLine[]
}

function definition(key: StockIndicatorKey, id: StockIndicatorId, label: string, title: string, requirements: readonly (readonly [string, string, number, number?])[]): StockIndicatorDefinition {
  return Object.freeze({ key, id, label, title, lines: Object.freeze(requirements.map(([lineId, lineLabel, requiredBars, matureBars]) => Object.freeze({ id: lineId, label: lineLabel, requiredBars, matureBars: matureBars ?? requiredBars }))) })
}

// Same calculations and methods as the adapter. Count sufficiency is not data/PIT qualification.
export const STOCK_INDICATOR_METHOD_VERSION = STOCK_CHART_METHOD_VERSION
export const STOCK_CHART_INDICATORS: readonly StockIndicatorDefinition[] = Object.freeze([
  definition("showMA", "ma", "MA", "MA5/10/20/60：包含本根的收盘价简单均线，周期按已聚合K线计数。", [["ma5", "MA5", 5], ["ma10", "MA10", 10], ["ma20", "MA20", 20], ["ma60", "MA60", 60]]),
  definition("showBOLL", "boll", "BOLL", "BOLL(20,2)：20根简单均值与样本标准差，上中下轨；中轨复用MA20。", [["boll_upper", "BOLL上", 20], ["ma20", "BOLL中", 20], ["boll_lower", "BOLL下", 20]]),
  definition("showVolume", "vol", "VOL", "VOL柱为本根成交量；VOL5/10为包含本根的同周期简单均量，保留输入单位。", [["volume", "VOL", 1], ["vol5", "VOL5", 5], ["vol10", "VOL10", 10]]),
  definition("showMACD", "macd", "MACD", "MACD(12,26,9)：首价初始化EMA，1根可算；34根仅为26+9−1预热参考，不保证EMA收敛；柱为2×(DIF−DEA)。", [["macd_dif", "DIF", 1, 34], ["macd_dea", "DEA", 1, 34], ["macd_hist", "MACD", 1, 34]]),
  definition("showRSI", "rsi", "RSI", "RSI(6,14,24)：Wilder平滑，需6/14/24次价差即7/15/25根收盘价；平价取50。", [["rsi_6", "RSI6", 7], ["rsi_14", "RSI14", 15], ["rsi_24", "RSI24", 25]]),
  definition("showKDJ", "kdj", "KDJ", "KDJ(9,3,3)：9根高低区间，首个有效RSV初始化；高低价差为0时不可算，数量足够不保证有值。", [["kdj_k", "K", 9], ["kdj_d", "D", 9], ["kdj_j", "J", 9]]),
])

export function stockIndicatorWarmup(availableBars: number) {
  if (!Number.isSafeInteger(availableBars) || availableBars < 0) throw new RangeError("指标可用根数必须为非负安全整数")
  return STOCK_CHART_INDICATORS.map((item) => ({ ...item, availableBars,
    lines: item.lines.map((line) => ({ ...line, available: availableBars >= line.requiredBars,
      missingBars: Math.max(0, line.requiredBars - availableBars), mature: availableBars >= line.matureBars,
      missingMatureBars: Math.max(0, line.matureBars - availableBars) })),
  }))
}

export type StockVolumeRatioReason = "available" | "insufficient_bars" | "invalid_volume" | "zero_reference_volume"
export interface StockVolumeRatioAvailability {
  requiredBars: number
  availableBars: number
  missingBars: number
  available: boolean
  value: number | null
  reason: StockVolumeRatioReason
}

/** Input is the cleaned, aggregated chart series. Check its latest point without changing formulas. */
export function stockVolumeRatioAvailability(bars: readonly StockBar[], days: number): StockVolumeRatioAvailability {
  if (!Number.isInteger(days) || days < 1 || days > 20) throw new RangeError("量比周期必须为1至20的整数")
  const base = { requiredBars: days + 1, availableBars: bars.length, missingBars: Math.max(0, days + 1 - bars.length) }
  const unavailable = (reason: StockVolumeRatioReason): StockVolumeRatioAvailability => ({ ...base, available: false, value: null, reason })
  if (base.missingBars) return unavailable("insufficient_bars")
  const window = bars.slice(-(days + 1))
  if (!window.every((bar) => Number.isFinite(bar.volume) && bar.volume >= 0)) return unavailable("invalid_volume")
  if (window.slice(0, -1).every((bar) => bar.volume === 0)) return unavailable("zero_reference_volume")
  const latest = stockVolumeRatios(window, days).at(-1)
  if (!latest || !Number.isFinite(latest.value)) return unavailable("invalid_volume")
  return { ...base, available: true, value: latest.value, reason: "available" }
}
