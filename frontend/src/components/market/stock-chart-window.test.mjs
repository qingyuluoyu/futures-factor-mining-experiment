import assert from "node:assert/strict"
import test from "node:test"

const bars = ["2026-10-16", "2026-10-19", "2026-10-20", "2026-10-23"].map((time) => ({ time }))

test("local chart window resolves loaded date indices without trimming changing or inventing input", async () => {
  const { resolveStockChartWindow } = await import("./stock-chart-window.ts")
  assert.deepEqual(resolveStockChartWindow(bars, "", ""), { status: "ready", from: 0, to: 3, visibleDateRange: undefined })
  assert.deepEqual(resolveStockChartWindow(bars, "2026-10-18", "2026-10-21"), { status: "ready", from: 1, to: 2, visibleDateRange: { start: "2026-10-19", end: "2026-10-20" } })
  assert.deepEqual(resolveStockChartWindow(bars, "2026-10-19", ""), { status: "ready", from: 1, to: 3, visibleDateRange: { start: "2026-10-19", end: "2026-10-23" } })
  assert.equal(bars.length, 4)
})

test("date range rejects reversed or malformed calendar days and never manufactures future replay candles", async () => {
  const { resolveStockChartWindow } = await import("./stock-chart-window.ts")
  for (const [start, end] of [["2026-10-20", "2026-10-19"], ["2026-02-30", ""], ["", "bad-date"]]) assert.equal(resolveStockChartWindow(bars, start, end).status, "invalid")
  assert.equal(resolveStockChartWindow(bars, "2026-10-24", "2026-11-01").status, "empty")
  assert.deepEqual(resolveStockChartWindow(bars, "", "2026-11-01").visibleDateRange, { start: "2026-10-16", end: "2026-10-23" })
  assert.equal(resolveStockChartWindow([], "", "").status, "empty")
  assert.equal(resolveStockChartWindow(bars, "2026-10-17", "2026-10-18").status, "empty")
})

test("measured container and control heights bound the canvas while keeping a usable small viewport", async () => {
  const { stockChartCanvasHeight } = await import("./stock-chart-window.ts")
  assert.equal(stockChartCanvasHeight(1000, 140, 50), 680)
  assert.equal(stockChartCanvasHeight(700, 140, 50), 478)
  assert.equal(stockChartCanvasHeight(150, 140, 20), 320)
  assert.equal(stockChartCanvasHeight(NaN, 100), 420)
})
