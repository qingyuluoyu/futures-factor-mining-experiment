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

async function indicatorOption(input) {
  const colors = { text: "#555", grid: "#eee", border: "#aaa", crosshair: "#888", crosshairLabelBg: "#eee", tooltipBg: "#fff", tooltipText: "#333", infoBarBg: "#fff" }
  const renderer = loadTsx(readFileSync(new URL("./echarts-candlestick.tsx", import.meta.url), "utf8"), {
    "./adapter": await adapter(),
    "./theme": { chartTheme: () => colors, getTheme: () => "light", useTheme: () => "light", stockCandleColors: () => ({ bull: "#C74040", bear: "#2D9B65", bullAlpha: "#C74040", bearAlpha: "#2D9B65" }) },
  })
  const dates = input.map((row) => row.date)
  return { renderer, option: renderer.buildTickOption(input, dates, new Map(dates.map((day, index) => [day, index])), undefined, undefined, undefined,
    true, false, ["vol", "macd", "rsi", "kdj", "boll"], 800, input.length - 1, undefined, { enabled: true, days: 2 }, undefined) }
}
const fixedBars = (count) => Array.from({ length: count }, (_, index) => ({ time: new Date(Date.UTC(2026, 0, index + 1)).toISOString().slice(0, 10), open: 10 + index, high: 11 + index, low: 9 + index, close: 10 + index, volume: (index + 1) / 100 }))
const near = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-9, `${actual} != ${expected}`)

test("indicator directory names exactly the existing tick overlays and subcharts", async () => {
  const { STOCK_CHART_INDICATORS } = await import("../stock-chart-indicators.ts")
  const { renderer } = await indicatorOption([])
  assert.deepEqual(Array.from(renderer.SUB_CHARTS, (item) => item.key), ["vol", "macd", "rsi", "kdj"])
  assert.deepEqual(Array.from(renderer.OVERLAY_INDICATORS, (item) => item.key), ["boll"])
  assert.deepEqual(STOCK_CHART_INDICATORS.map((item) => item.id).sort(), ["ma", ...Array.from(renderer.SUB_CHARTS, (item) => item.key), ...Array.from(renderer.OVERLAY_INDICATORS, (item) => item.key)].sort())
})

test("fixed vector exposes every current adapter indicator field and keeps first-price MACD zero distinct from null", async () => {
  const { toTickChartData } = await adapter()
  const input = fixedBars(70)
  const data = toTickChartData(input, "adjacent_loaded_adjusted_close")
  const last = data.at(-1)
  for (const [field, expected] of Object.entries({ ma5: 77, ma10: 74.5, ma20: 69.5, ma60: 49.5, rsi_6: 100, rsi_14: 100, rsi_24: 100, kdj_k: 90, kdj_d: 90, kdj_j: 90,
    boll_upper: 69.5 + 2 * Math.sqrt(35), boll_lower: 69.5 - 2 * Math.sqrt(35), volume: 0.7 })) near(last[field], expected)
  near(data[1].macd_dif, 28 / 351); near(data[1].macd_dea, 28 / 1755); near(data[1].macd_hist, 224 / 1755)
  assert.deepEqual([data[0].macd_dif, data[0].macd_dea, data[0].macd_hist], [0, 0, 0])
  for (const [field, required] of Object.entries({ ma5: 5, ma10: 10, ma20: 20, ma60: 60, rsi_6: 7, rsi_14: 15, rsi_24: 25, kdj_k: 9, kdj_d: 9, kdj_j: 9, boll_upper: 20, boll_lower: 20 })) {
    assert.ok(data.slice(0, required - 1).every((row) => row[field] === null), field)
    assert.equal(typeof data[required - 1][field], "number", field)
  }
  assert.deepEqual(toTickChartData(input.slice(0, 30), "adjacent_loaded_adjusted_close"), data.slice(0, 30), "future bars cannot rewrite a prefix")
  assert.equal(toTickChartData([{ ...input[0], volume: null }], "unavailable").length, 0)
})

test("all existing MA BOLL VOL MACD RSI KDJ series reach real ECharts SVG with exact field values", async () => {
  const { toTickChartData } = await adapter()
  const data = toTickChartData(fixedBars(70), "unavailable")
  const { option } = await indicatorOption(data)
  const byName = new Map(option.series.map((item) => [item.name, item]))
  const fields = { MA5: "ma5", MA10: "ma10", MA20: "ma20", MA60: "ma60", "BOLL上": "boll_upper", "BOLL下": "boll_lower", DIF: "macd_dif", DEA: "macd_dea", MACD: "macd_hist", RSI6: "rsi_6", RSI14: "rsi_14", RSI24: "rsi_24", K: "kdj_k", D: "kdj_d", J: "kdj_j" }
  for (const [name, field] of Object.entries(fields)) {
    const series = option.series.find((item) => item.name === name && item.type !== "candlestick")
    assert.ok(series, name)
    assert.deepEqual(Array.from(series.data, (item) => typeof item === "object" && item !== null ? item.value : item), data.map((row) => row[field] ?? "-"), name)
  }
  near(byName.get("VOL5").data.at(-1), 0.68)
  near(byName.get("VOL10").data.at(-1), 0.655)
  assert.deepEqual(Array.from(byName.get("成交量").data, (item) => item.value), data.map((row) => row.volume))
  const chart = requireModule("echarts").init(null, undefined, { renderer: "svg", ssr: true, width: 1200, height: 800 })
  try {
    chart.setOption({ ...option, animation: false, dataZoom: [] })
    assert.equal(chart.getModel().getSeries().length, 19)
    assert.ok(chart.getModel().getSeries().every((series) => series.getData().count() === 70))
    const svg = chart.renderToSVGString()
    assert.match(svg, /^<svg/); assert.match(svg, /<path/)
    assert.doesNotMatch(svg, /NaN|undefined|Infinity/)
  } finally { chart.dispose() }
})

test("sparse flat-price and zero-volume vectors render gaps without manufacturing unavailable curves or ratios", async () => {
  const { toTickChartData } = await adapter()
  const flat = fixedBars(25).map((row) => ({ ...row, open: 10, high: 10, low: 10, close: 10, volume: 0 }))
  const data = toTickChartData(flat, "unavailable")
  assert.ok(data.every((row) => row.kdj_k === null && row.kdj_d === null && row.kdj_j === null))
  assert.equal(data.at(-1).rsi_24, 50)
  assert.equal(data.at(-1).boll_upper, 10); assert.equal(data.at(-1).boll_lower, 10)
  for (const input of [data.slice(0, 1), data]) {
    const { option } = await indicatorOption(input)
    const volume = option.series.find((item) => item.name === "成交量")
    assert.ok(volume.data.every((item) => item.value === 0 && item.volumeRatioLabel === ""))
    for (const name of ["K", "D", "J"]) assert.ok(option.series.find((item) => item.name === name && item.type === "line").data.every((item) => item === "-"))
    if (input.length === 1) assert.ok(option.series.find((item) => item.name === "RSI6").data.every((item) => item === "-"))
    const chart = requireModule("echarts").init(null, undefined, { renderer: "svg", ssr: true, width: 1000, height: 800 })
    try {
      chart.setOption({ ...option, animation: false })
      assert.match(chart.renderToSVGString(), /^<svg/)
      assert.doesNotMatch(chart.renderToSVGString(), /NaN|undefined|Infinity/)
    } finally { chart.dispose() }
  }
})
