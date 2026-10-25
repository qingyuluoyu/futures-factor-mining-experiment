"use client"

import { create } from "zustand"

export interface StockChartPreferences {
  period: "day" | "week" | "month"
  visible: 60 | 120 | 250 | "all"
  showMA: boolean
  showVolume: boolean
  showMACD: boolean
  showRSI: boolean
  showKDJ: boolean
  showBOLL: boolean
  showCrosshair: boolean
  volumeCompareEnabled: boolean
  volumeCompareDays: number
}

const DEFAULT_CHART: StockChartPreferences = {
  period: "day", visible: 60, showMA: true, showVolume: true, showMACD: false,
  showRSI: false, showKDJ: false, showBOLL: false, showCrosshair: true,
  volumeCompareEnabled: true, volumeCompareDays: 1,
}

function normalizeChartPreferences(patch: Partial<StockChartPreferences>, previous = DEFAULT_CHART): StockChartPreferences {
  const result = { ...previous }
  if (patch.period === "day" || patch.period === "week" || patch.period === "month") result.period = patch.period
  if (patch.visible === 60 || patch.visible === 120 || patch.visible === 250 || patch.visible === "all") result.visible = patch.visible
  for (const name of ["showMA", "showVolume", "showMACD", "showRSI", "showKDJ", "showBOLL", "showCrosshair", "volumeCompareEnabled"] as const) {
    if (typeof patch[name] === "boolean") result[name] = patch[name]
  }
  if (typeof patch.volumeCompareDays === "number" && Number.isFinite(patch.volumeCompareDays)) {
    result.volumeCompareDays = Math.max(1, Math.min(20, Math.round(patch.volumeCompareDays)))
  }
  return result
}

function readChartPreferences(key: string): StockChartPreferences {
  const result = { ...DEFAULT_CHART }
  try {
    const parsed = JSON.parse(localStorage.getItem(`${key}:chart`) ?? "null") as Partial<StockChartPreferences> | null
    if (!parsed || typeof parsed !== "object") return result
    return normalizeChartPreferences(parsed)
  } catch {}
  return result
}

interface StockMarketState {
  activeStockCode: string
  contextKey: string | null
  restoreContext: (userId: string, tradingMode: string) => void
  selectStock: (code: string) => void
  selectStockInContext: (userId: string, tradingMode: string, code: string) => void
  chartPreferences: StockChartPreferences
  setChartPreferences: (patch: Partial<StockChartPreferences>) => void
}

function validStockCode(code: string): boolean {
  return /^\d{6}$/.test(code)
}

export const useStockMarketStore = create<StockMarketState>((set, get) => ({
  activeStockCode: "600519",
  contextKey: null,
  chartPreferences: { ...DEFAULT_CHART },
  restoreContext: (userId, tradingMode) => {
    const key = `qihuo_stock_selection:${userId}:${tradingMode}`
    if (get().contextKey === key) return
    let code = "600519"
    try {
      const saved = typeof window === "undefined" ? null : localStorage.getItem(key)
      if (saved && validStockCode(saved)) code = saved
    } catch {}
    set({ activeStockCode: code, contextKey: key, chartPreferences: readChartPreferences(key) })
  },
  selectStock: (input) => {
    const code = input.trim()
    if (!validStockCode(code)) return
    const key = get().contextKey
    if (key && typeof window !== "undefined") {
      try {
        localStorage.setItem(key, code)
      } catch {}
    }
    set({ activeStockCode: code })
  },
  selectStockInContext: (userId, tradingMode, code) => {
    get().restoreContext(userId, tradingMode)
    get().selectStock(code)
  },
  setChartPreferences: (patch) => {
    const next = normalizeChartPreferences(patch, get().chartPreferences)
    const key = get().contextKey
    if (key && typeof window !== "undefined") {
      try {
        localStorage.setItem(`${key}:chart`, JSON.stringify(next))
      } catch {}
    }
    set({ chartPreferences: next })
  },
}))
