import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { createRequire } from "node:module"
import test from "node:test"
import * as calculations from "../stock-chart-data.ts"
import { loadTsx } from "../../dashboard/dashboard-frame-harness.mjs"
import { renderToStaticMarkup } from "react-dom/server"
import { createElement } from "react"

const requireModule = createRequire(import.meta.url)
const adapter = () => import("./adapter.ts")
const bars = Array.from({ length: 70 }, (_, index) => ({ time: new Date(Date.UTC(2026, 0, index + 1)).toISOString().slice(0, 10), open: 10 + index / 10, close: 10.2 + index / 10, low: 9.9 + index / 10, high: 10.3 + index / 10, volume: index === 69 ? 0.01 : index + 1 }))

test("tick adapter retains decimal volume and the current stock indicator method", async () => {
  const { toTickChartData } = await adapter()
  const data = toTickChartData(bars, "adjacent_loaded_adjusted_close")
  assert.equal(data.length, bars.length)
  assert.equal(data.at(-1).volume, 0.01)
  assert.equal(data.at(-1).ma20, calculations.stockSMA(bars, 20).at(-1).value)
  assert.equal(data.at(-1).macd_hist, calculations.stockMACD(bars).at(-1).macd)
  assert.equal(data.at(-1).rsi_14, calculations.stockRSI(bars, 14).at(-1).value)
  assert.equal(data.at(-1).kdj_j, calculations.stockKDJ(bars).at(-1).j)
  assert.equal(data.at(-1).boll_upper, calculations.stockBOLL(bars).at(-1).upper)
  assert.equal(data[0].ma60, null)
  assert.equal(data[0].change_pct, null)
})

test("raw vendor reference cannot become an adjacent raw or zero change", async () => {
  const { toTickChartData, tickVolume, tickPercent } = await adapter()
  const input = [{ time: "2026-10-15", open: 100, close: 100, high: 100, low: 100, volume: 1 },
    { time: "2026-10-16", open: 10, close: 11, high: 11, low: 9, volume: 0.01, pre_close: 10 }]
  assert.equal(toTickChartData(input, "vendor_ex_rights_pre_close")[1].change_pct, 10)
  assert.equal(toTickChartData(input, "unavailable")[1].change_pct, null)
  assert.equal(tickVolume(0.01), "0.01")
  assert.equal(tickVolume(undefined), "—")
  assert.equal(tickPercent(null), "—")
  assert.equal(tickPercent(0), "+0.00%")
})

test("ported tick engine renders actual SVG with main and all four subcharts", async () => {
  const { toTickChartData } = await adapter()
  const data = toTickChartData(bars, "adjacent_loaded_adjusted_close")
  const colors = { text: "#555", grid: "#eee", border: "#aaa", crosshair: "#888", crosshairLabelBg: "#eee", tooltipBg: "#fff", tooltipText: "#333", infoBarBg: "#fff" }
  const module = loadTsx(readFileSync(new URL("./echarts-candlestick.tsx", import.meta.url), "utf8"), {
    "./adapter": await adapter(),
    "./theme": { chartTheme: () => colors, getTheme: () => "light", useTheme: () => "light", stockCandleColors: () => ({ bull: "#C74040", bear: "#2D9B65", bullAlpha: "#C74040", bearAlpha: "#2D9B65" }) },
  })
  const dates = data.map((row) => row.date)
  const option = module.buildTickOption(data, dates, new Map(dates.map((day, index) => [day, index])), undefined, undefined, undefined,
    true, false, ["vol", "macd", "rsi", "kdj", "boll"], 680, data.length - 1, undefined, { enabled: true, days: 1 }, undefined)
  assert.equal(option.grid.length, 5)
  assert.ok(option.series.some((series) => series.type === "candlestick"))
  assert.ok(option.series.every((series) => ["candlestick", "line", "bar", "scatter", "custom"].includes(series.type)))
  const implementation = readFileSync(new URL("./echarts-candlestick.tsx", import.meta.url), "utf8")
  assert.match(implementation, /from ['"]echarts\/core['"]/)
  assert.doesNotMatch(implementation, /LinesChart|import \* as echarts from ['"]echarts['"]/)
  const echarts = requireModule("echarts")
  const chart = echarts.init(null, undefined, { renderer: "svg", ssr: true, width: 1024, height: 680 })
  try {
    chart.setOption({ ...option, animation: false })
    const svg = chart.renderToSVGString()
    assert.match(svg, /^<svg/)
    assert.match(svg, /MACD|DIF/)
    assert.doesNotMatch(svg, /NaN|undefined/)
  } finally { chart.dispose() }
})

test("sparse daily bars have bounded candle widths and all indicator grids stay inside their canvas", async () => {
  const { toTickChartData } = await adapter()
  const data = toTickChartData(bars.slice(0, 5), "adjacent_loaded_adjusted_close")
  const colors = { text: "#555", grid: "#eee", border: "#aaa", crosshair: "#888", crosshairLabelBg: "#eee", tooltipBg: "#fff", tooltipText: "#333", infoBarBg: "#fff" }
  const module = loadTsx(readFileSync(new URL("./echarts-candlestick.tsx", import.meta.url), "utf8"), {
    "./adapter": await adapter(),
    "./theme": { chartTheme: () => colors, getTheme: () => "light", useTheme: () => "light", stockCandleColors: () => ({ bull: "red", bear: "green" }) },
  })
  const activeIndicators = ["vol", "macd", "rsi", "kdj"]
  const markup = renderToStaticMarkup(createElement(module.EChartsCandlestick, { data, height: 320, showInfoBar: false, activeIndicators }))
  const canvasHeight = Number(markup.match(/height:(\d+)px/)?.[1])
  const dates = data.map((row) => row.date)
  const option = module.buildTickOption(data, dates, new Map(dates.map((day, index) => [day, index])), undefined, undefined, undefined, true, false, activeIndicators, canvasHeight, 4, undefined, { enabled: false, days: 1 }, undefined)
  assert.ok(option.grid[0].height >= 240, "enabled indicators must grow the canvas without squeezing the price pane")
  assert.ok(option.grid.every((grid) => grid.top + grid.height <= canvasHeight))
  assert.equal(option.series.find((series) => series.type === "candlestick").barMaxWidth, 12)
  assert.ok(option.series.filter((series) => series.type === "bar").every((series) => series.barMaxWidth <= 12))
  const chart = requireModule("echarts").init(null, undefined, { renderer: "svg", ssr: true, width: 1000, height: canvasHeight })
  try {
    chart.setOption({ ...option, animation: false })
    const layout = chart.getModel().getSeriesByIndex(0).getData().getItemLayout(0)
    assert.ok(Math.abs(layout.ends[1][0] - layout.ends[0][0]) <= 12)
    assert.doesNotMatch(chart.renderToSVGString(), /NaN|undefined/)
  } finally { chart.dispose() }
})
