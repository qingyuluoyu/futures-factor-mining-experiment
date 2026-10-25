import assert from "node:assert/strict"
import test from "node:test"
import { aggregateStockBars, STOCK_CHART_METHOD_VERSION } from "./stock-chart-data.ts"

const bars = (count, volume = 0.01) => Array.from({ length: count }, (_, index) => ({ time: new Date(Date.UTC(2026, 0, index + 1)).toISOString().slice(0, 10), open: 10 + index, high: 11 + index, low: 9 + index, close: 10 + index, volume }))
const load = () => import("./stock-chart-indicators.ts")

test("six indicator directory matches existing chart toggles and methods without promising other frequencies", async () => {
  const { STOCK_CHART_INDICATORS, STOCK_INDICATOR_METHOD_VERSION } = await load()
  assert.deepEqual(STOCK_CHART_INDICATORS.map(({ key, id }) => [key, id]), [["showMA", "ma"], ["showBOLL", "boll"], ["showVolume", "vol"], ["showMACD", "macd"], ["showRSI", "rsi"], ["showKDJ", "kdj"]])
  assert.equal(STOCK_INDICATOR_METHOD_VERSION, STOCK_CHART_METHOD_VERSION)
  assert.ok(STOCK_CHART_INDICATORS.every((item) => item.title && item.lines.length > 0))
  assert.ok(Object.isFrozen(STOCK_CHART_INDICATORS))
})

test("per-line count requirements distinguish price averages bands volume and RSI increments", async () => {
  const { stockIndicatorWarmup } = await load()
  const state = stockIndicatorWarmup(5)
  const indexed = Object.fromEntries(state.map((item) => [item.id, item]))
  assert.deepEqual(indexed.ma.lines.map(({ requiredBars, available, missingBars }) => [requiredBars, available, missingBars]), [[5, true, 0], [10, false, 5], [20, false, 15], [60, false, 55]])
  assert.deepEqual(indexed.boll.lines.map(({ requiredBars }) => requiredBars), [20, 20, 20])
  assert.deepEqual(indexed.vol.lines.map(({ requiredBars }) => requiredBars), [1, 5, 10])
  assert.deepEqual(indexed.rsi.lines.map(({ requiredBars }) => requiredBars), [7, 15, 25])
  assert.deepEqual(indexed.kdj.lines.map(({ requiredBars }) => requiredBars), [9, 9, 9])
  assert.ok(stockIndicatorWarmup(0).every((item) => item.lines.every((line) => !line.available && line.missingBars === line.requiredBars)))
  assert.ok(stockIndicatorWarmup(60).every((item) => item.lines.every((line) => line.available && line.missingBars === 0)))
})

test("MACD first-price seed is computable from one point while its warmup reference is explicit", async () => {
  const { stockIndicatorWarmup } = await load()
  const macd = stockIndicatorWarmup(1).find((item) => item.id === "macd")
  assert.ok(macd.lines.every((line) => line.available && line.requiredBars === 1 && line.matureBars === 34 && !line.mature && line.missingMatureBars === 33))
  assert.match(macd.title, /首价|首个/)
  assert.match(macd.title, /不.*收敛|非.*收敛/)
  assert.ok(stockIndicatorWarmup(34).find((item) => item.id === "macd").lines.every((line) => line.mature))
})

test("warmup is counted after period aggregation and rejects impossible lengths", async () => {
  const { stockIndicatorWarmup } = await load()
  const weeks = aggregateStockBars(bars(30), "week")
  const weekly = stockIndicatorWarmup(weeks.length).find((item) => item.id === "ma")
  assert.equal(weekly.availableBars, weeks.length)
  assert.equal(weekly.lines[1].missingBars, 10 - weeks.length)
  for (const count of [-1, 0.5, NaN, Infinity, null, undefined]) assert.throws(() => stockIndicatorWarmup(count), /根数/)
})

test("volume ratio needs N previous bars plus current and retains fractional units without current inclusion", async () => {
  const { stockVolumeRatioAvailability } = await load()
  const input = bars(3); input[0].volume = 0.01; input[1].volume = 0.03; input[2].volume = 0.06
  const result = stockVolumeRatioAvailability(input, 2)
  assert.deepEqual(result, { requiredBars: 3, availableBars: 3, missingBars: 0, available: true, value: 3, reason: "available" })
  assert.equal(stockVolumeRatioAvailability(input.slice(0, 2), 2).missingBars, 1)
  assert.equal(stockVolumeRatioAvailability(input.slice(0, 2), 2).value, null)
  assert.equal(stockVolumeRatioAvailability(bars(21), 20).available, true)
  for (const days of [0, 21, -1, 1.5, NaN]) assert.throws(() => stockVolumeRatioAvailability(input, days), /1至20/)
})

test("volume ratio keeps zero current volume valid but missing or zero reference volume unavailable", async () => {
  const { stockVolumeRatioAvailability } = await load()
  const zero = bars(2, 0)
  assert.equal(stockVolumeRatioAvailability(zero, 1).reason, "zero_reference_volume")
  assert.equal(stockVolumeRatioAvailability(zero, 1).value, null)
  zero[0].volume = 0.01
  assert.equal(stockVolumeRatioAvailability(zero, 1).value, 0)
  for (const invalid of [null, undefined, NaN, Infinity, -1]) {
    const input = bars(2); input[0].volume = invalid
    assert.equal(stockVolumeRatioAvailability(input, 1).reason, "invalid_volume")
    assert.equal(stockVolumeRatioAvailability(input, 1).available, false)
  }
})
