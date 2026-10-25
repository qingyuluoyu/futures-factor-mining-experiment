import assert from "node:assert/strict"
import test from "node:test"

const daily = [
  { time: "2026-10-08", open: 10, high: 12, low: 9, close: 11, volume: 100 },
  { time: "2026-10-09", open: 11, high: 13, low: 10, close: 12, volume: 200 },
  { time: "2026-10-12", open: 12, high: 14, low: 11, close: 13, volume: 300 },
]

test("weekly candles keep first open, last close, extrema and summed volume", async () => {
  const { aggregateStockBars } = await import("./stock-chart-data.ts")
  assert.deepEqual(aggregateStockBars(daily, "week"), [
    { time: "2026-10-09", open: 10, high: 13, low: 9, close: 12, volume: 300 },
    daily[2],
  ])
})

test("month grouping does not merge different years and input stays unchanged", async () => {
  const { aggregateStockBars } = await import("./stock-chart-data.ts")
  const bars = [daily[0], { ...daily[1], time: "2027-10-09" }]
  assert.equal(aggregateStockBars(bars, "month").length, 2)
  assert.equal(daily[0].time, "2026-10-08")
})

test("chart preparation sorts, deduplicates and rejects invalid OHLC or volume", async () => {
  const { prepareStockBars } = await import("./stock-chart-data.ts")
  const updated = { ...daily[0], close: 10.5 }
  const bad = [
    { ...daily[1], high: 8 },
    { ...daily[2], close: NaN },
    { ...daily[2], volume: -1 },
    { ...daily[2], time: "2026-02-30" },
  ]
  const result = prepareStockBars([daily[2], daily[0], updated, ...bad])
  assert.deepEqual(result, [updated, daily[2]])
})

test("initial view shows recent candles instead of squeezing all loaded history", async () => {
  const { stockVisibleRange } = await import("./stock-chart-data.ts")
  assert.deepEqual(stockVisibleRange(2000, 60), { from: 1940, to: 2002 })
  assert.deepEqual(stockVisibleRange(20, 60), { from: 0, to: 22 })
  assert.deepEqual(stockVisibleRange(250, "all"), { from: 0, to: 252 })
  assert.equal(stockVisibleRange(0, 60), null)
})

test("a truncated history does not present its first partial week as a complete candle", async () => {
  const { aggregateStockBars } = await import("./stock-chart-data.ts")
  assert.deepEqual(aggregateStockBars(daily, "week", true), [daily[2]])
})

function indicatorBars(closes, volumes = closes.map(() => 100)) {
  return closes.map((close, index) => ({
    time: `2026-01-${String(index + 1).padStart(2, "0")}`,
    open: close, high: close + 1, low: close - 1, close, volume: volumes[index],
  }))
}

function near(actual, expected) {
  assert.ok(Math.abs(actual - expected) < 1e-9, `expected ${expected}, received ${actual}`)
}

test("stock chart exposes real technical indicator calculations", async () => {
  const chartData = await import("./stock-chart-data.ts")
  for (const name of ["stockSMA", "stockMACD", "stockKDJ", "stockRSI", "stockBOLL", "stockVolumeRatios", "stockZoomRange", "stockRestoreRange"]) {
    assert.equal(typeof chartData[name], "function", `${name} is unavailable`)
  }
})

test("price and volume averages require a complete finite window and preserve units", async () => {
  const { stockSMA } = await import("./stock-chart-data.ts")
  const bars = indicatorBars([10, 11, 12, 11], [100, 200, 300, 400])
  assert.deepEqual(stockSMA(bars, 3), [{ time: bars[2].time, value: 11 }, { time: bars[3].time, value: 34 / 3 }])
  assert.deepEqual(stockSMA(bars, 3, "volume"), [{ time: bars[2].time, value: 200 }, { time: bars[3].time, value: 300 }])
  assert.deepEqual(stockSMA([{ ...bars[0], volume: undefined }, ...bars.slice(1)], 3, "volume"), [{ time: bars[3].time, value: 300 }])
  assert.deepEqual(stockSMA(bars, 0), [])
  assert.deepEqual(stockSMA(bars, 1.5), [])
})

test("MACD uses tick first-price EMA seeds and a doubled histogram without fabricated DIF history", async () => {
  const { stockMACD } = await import("./stock-chart-data.ts")
  const bars = indicatorBars([10, 11, 12, 11])
  const points = stockMACD(bars, 2, 3, 2)
  near(points[1].dif, 1 / 6)
  near(points[1].dea, 1 / 9)
  near(points[1].macd, 1 / 9)
  near(points[2].dif, 11 / 36)
  near(points[2].dea, 13 / 54)
  near(points[2].macd, 7 / 54)
  near(points[3].dif, 13 / 216)
  near(points[3].dea, 13 / 108)
  near(points[3].macd, -13 / 108)
  assert.deepEqual(stockMACD(bars, 3, 2, 2), [])
  assert.deepEqual(stockMACD([], 2, 3, 2), [])
})

test("KDJ uses rolling high/low and recursive K/D, leaving a zero price range unavailable", async () => {
  const { stockKDJ } = await import("./stock-chart-data.ts")
  const bars = indicatorBars([10, 11, 12, 11])
  const points = stockKDJ(bars, 3, 3, 3)
  assert.equal(points[0].time, bars[2].time)
  near(points[0].k, 75)
  near(points[0].d, 75)
  near(points[1].k, 550 / 9)
  near(points[1].d, 1900 / 27)
  near(points[1].j, 1150 / 27)
  const flat = bars.map((bar) => ({ ...bar, open: 10, high: 10, low: 10, close: 10 }))
  assert.deepEqual(stockKDJ(flat, 3, 3, 3), [])
})

test("RSI uses a full Wilder seed, handles rising/falling/flat prices and does not invent warmup values", async () => {
  const { stockRSI } = await import("./stock-chart-data.ts")
  const bars = indicatorBars([10, 12, 11, 13, 12])
  const points = stockRSI(bars, 3)
  assert.equal(points[0].time, bars[3].time)
  near(points[0].value, 80)
  near(points[1].value, 800 / 13)
  assert.equal(stockRSI(indicatorBars([10, 11, 12, 13]), 3)[0].value, 100)
  assert.equal(stockRSI(indicatorBars([13, 12, 11, 10]), 3)[0].value, 0)
  assert.equal(stockRSI(indicatorBars([10, 10, 10, 10]), 3)[0].value, 50)
  assert.deepEqual(stockRSI(bars.slice(0, 3), 3), [])
})

test("BOLL matches tick sample standard deviation instead of mixing population variance", async () => {
  const { stockBOLL } = await import("./stock-chart-data.ts")
  const bars = indicatorBars([10, 11, 12, 13])
  assert.deepEqual(stockBOLL(bars, 3, 2), [
    { time: bars[2].time, middle: 11, upper: 13, lower: 9 },
    { time: bars[3].time, middle: 12, upper: 14, lower: 10 },
  ])
  assert.deepEqual(stockBOLL(bars, 1, 2), [])
  assert.deepEqual(stockBOLL(bars.slice(0, 2), 3, 2), [])
})

test("volume comparison excludes the current bar and rejects missing, zero-denominator and invalid periods", async () => {
  const { stockVolumeRatios } = await import("./stock-chart-data.ts")
  const bars = indicatorBars([10, 11, 12, 13], [100, 300, 600, 900])
  assert.deepEqual(stockVolumeRatios(bars, 2), [
    { time: bars[2].time, value: 3 },
    { time: bars[3].time, value: 2 },
  ])
  assert.deepEqual(stockVolumeRatios(indicatorBars([10, 11], [0, 100]), 1), [])
  assert.deepEqual(stockVolumeRatios(indicatorBars([10, 11, 12], [100, undefined, 300]), 2), [])
  assert.deepEqual(stockVolumeRatios(bars, 21), [])
  assert.deepEqual(stockVolumeRatios(bars, 0), [])
})

test("all technical indicators keep prefix results unchanged when future candles are appended", async () => {
  const chartData = await import("./stock-chart-data.ts")
  const bars = indicatorBars(Array.from({ length: 30 }, (_, index) => 10 + index % 5))
  for (const name of ["stockSMA", "stockMACD", "stockKDJ", "stockRSI", "stockBOLL", "stockVolumeRatios"]) {
    const prefix = chartData[name](bars.slice(0, 25))
    assert.deepEqual(chartData[name](bars).filter((point) => point.time <= bars[24].time), prefix, name)
  }
})

test("chart zoom anchors the viewport and does not exceed loaded data", async () => {
  const { stockZoomRange } = await import("./stock-chart-data.ts")
  assert.deepEqual(stockZoomRange({ from: 40, to: 80 }, 0.5, 200), { from: 50, to: 70 })
  assert.deepEqual(stockZoomRange({ from: 0, to: 40 }, 2, 50), { from: 0, to: 52 })
  assert.equal(stockZoomRange({ from: 0, to: 40 }, 0.5, 0), null)
})

test("the default 60-candle view does not suppress volume labels because of its empty right offset", async () => {
  const { stockVisibleBarCount, stockVisibleRange } = await import("./stock-chart-data.ts")
  assert.equal(typeof stockVisibleBarCount, "function", "visible volume bars must exclude empty chart space")
  assert.equal(stockVisibleBarCount(stockVisibleRange(200, 60), 200), 60)
  assert.equal(stockVisibleBarCount(stockVisibleRange(200, 120), 200), 120)
  assert.equal(stockVisibleBarCount({ from: -20, to: 20 }, 10), 10)
  assert.equal(stockVisibleBarCount(null, 200), 0)
})

test("rebuilding chart panes preserves historical viewport and follows the end only when already at the end", async () => {
  const { stockRestoreRange } = await import("./stock-chart-data.ts")
  assert.deepEqual(stockRestoreRange({ from: 20, to: 80 }, 200, 205, 60), { from: 20, to: 80 })
  assert.deepEqual(stockRestoreRange({ from: 140, to: 202 }, 200, 205, 60), { from: 145, to: 207 })
  assert.deepEqual(stockRestoreRange(null, 0, 205, 60), { from: 145, to: 207 })
  assert.equal(stockRestoreRange(null, 0, 0, 60), null)
})
