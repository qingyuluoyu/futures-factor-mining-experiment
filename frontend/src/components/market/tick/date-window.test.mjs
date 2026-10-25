import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { createRequire } from "node:module"
import test from "node:test"
import vm from "node:vm"
import ts from "typescript"
import { createElement } from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { loadTsx } from "../../dashboard/dashboard-frame-harness.mjs"
import { toTickChartData } from "./adapter.ts"

const requireModule = createRequire(import.meta.url)
const theme = { chartTheme: () => ({ text: "#555", grid: "#eee", border: "#aaa", crosshair: "#888", crosshairLabelBg: "#eee", infoBarBg: "#fff" }), getTheme: () => "light", useTheme: () => "light", stockCandleColors: () => ({ bull: "red", bear: "green", bullAlpha: "red", bearAlpha: "green" }) }
const renderer = loadTsx(readFileSync(new URL("./echarts-candlestick.tsx", import.meta.url), "utf8"), { "./adapter": await import("./adapter.ts"), "./theme": theme })
const bars = Array.from({ length: 80 }, (_, index) => ({ time: new Date(Date.UTC(2026, 0, index + 1)).toISOString().slice(0, 10), open: 10 + index, close: 10.5 + index, low: 9 + index, high: 11 + index, volume: (index + 1) / 100 }))
const data = toTickChartData(bars, "unavailable")
const dates = data.map((row) => row.date)
const range = { start: dates[60], end: dates[64] }
const optionFor = (visibleDateRange, infoIndex = -1) => renderer.buildTickOption(data, dates, new Map(dates.map((date, index) => [date, index])), undefined, undefined, undefined, true, false, ["vol", "macd", "rsi", "kdj", "boll"], 800, infoIndex, undefined, { enabled: true, days: 2 }, undefined, visibleDateRange)

test("stock date viewport resolves exact loaded categories without padding or losing earlier observations", () => {
  const window = renderer.resolveTickDateRange(dates, range)
  assert.equal(window.startValue, 60)
  assert.equal(window.endValue, 64)
  assert.equal(window.start, 60 / 79 * 100)
  assert.equal(window.end, 64 / 79 * 100)
  assert.equal(renderer.resolveTickDateRange(dates), null)
  const sparse = ["2026-10-16", "2026-10-19", "2026-10-23"]
  assert.deepEqual(JSON.parse(JSON.stringify(renderer.resolveTickDateRange(sparse, { start: "2026-10-17", end: "2026-10-22" }))), { startValue: 1, endValue: 1, start: 50, end: 50 })
  for (const invalid of [{ start: "2026-02-30", end: "2026-03-01" }, { start: dates[4], end: dates[3] }, { start: "2027-01-01", end: "2027-01-02" }]) {
    assert.throws(() => renderer.resolveTickDateRange(dates, invalid))
  }
  assert.throws(() => renderer.resolveTickDateRange([], range))
})

test("date zoom retains full MA60, VOL10, RSI and ratio history while all axes use the same requested interval", () => {
  const original = optionFor()
  const ranged = optionFor(range)
  // Tooltip/label closures are rebuilt for each option; compare every serialized data field.
  assert.deepEqual(JSON.parse(JSON.stringify(ranged.series)), JSON.parse(JSON.stringify(original.series)), "date selection must not recalculate or slice any indicator inputs")
  assert.deepEqual(JSON.parse(JSON.stringify(ranged.xAxis)), JSON.parse(JSON.stringify(original.xAxis)))
  assert.equal(ranged.dataZoom[0].startValue, 60)
  assert.equal(ranged.dataZoom[0].endValue, 64)
  assert.deepEqual(Array.from(ranged.dataZoom[0].xAxisIndex), [0, 1, 2, 3, 4])
  assert.ok(ranged.series.find((series) => series.name === "MA60").data[60] > 0)
  const volume = ranged.series.find((series) => series.name === "成交量")
  assert.equal(volume.data[60].volumeRatioLabel, (0.61 / ((0.60 + 0.59) / 2)).toFixed(1) + "x")
  const text = JSON.stringify(ranged.graphic)
  assert.match(text, /0\.65/)
  assert.doesNotMatch(text, /0\.80/)
})

test("native ECharts date zoom shows requested endpoints and keeps indicator values through manual zoom", () => {
  const echarts = requireModule("echarts")
  const chart = echarts.init(null, undefined, { renderer: "svg", ssr: true, width: 1100, height: 800 })
  try {
    chart.setOption(optionFor(range))
    const zoom = chart.getOption().dataZoom[0]
    assert.equal(zoom.startValue, 60)
    assert.equal(zoom.endValue, 64)
    const ma60 = chart.getModel().getSeries().find((series) => series.name === "MA60")
    assert.equal(ma60.getRawData().count(), 80)
    assert.equal(ma60.getData().get("y", 0), data[60].ma60)
    assert.doesNotMatch(chart.renderToSVGString(), /NaN|undefined|Infinity/)
    chart.dispatchAction({ type: "dataZoom", start: 0, end: 100 })
    assert.equal(chart.getOption().dataZoom[0].startValue, 0)
    assert.equal(chart.getOption().dataZoom[0].endValue, 79)
    assert.equal(chart.getModel().getSeries().find((series) => series.name === "MA60").getRawData().count(), 80)
  } finally { chart.dispose() }
})

test("initial price information defaults to the selected end date rather than a later loaded bar", () => {
  const html = renderToStaticMarkup(createElement(renderer.EChartsCandlestick, { data, visibleDateRange: range, activeIndicators: ["vol"], showInfoBar: true }))
  assert.match(html, new RegExp(dates[64]))
  assert.doesNotMatch(html, new RegExp(dates[79]))
  assert.match(html, /MA60/)
})

function mountNativeRenderer() {
  let refCursor = 0, memoCursor = 0, effectCursor = 0
  const refs = [], memos = [], effects = [], pending = [], charts = [], dispatches = []
  const changed = (old, deps) => !old || deps.some((value, index) => !Object.is(value, old.deps[index]))
  const hooks = {
    useRef(value) { const index = refCursor++; return refs[index] ??= { current: value } },
    useMemo(callback, deps) { const index = memoCursor++; if (changed(memos[index], deps)) memos[index] = { deps, value: callback() }; return memos[index].value },
    useCallback(callback, deps) { return hooks.useMemo(() => callback, deps) },
    useEffect(callback, deps) { const index = effectCursor++; if (changed(effects[index], deps)) pending.push(() => { effects[index]?.cleanup?.(); effects[index] = { deps, cleanup: callback() } }) },
  }
  const native = requireModule("echarts")
  const core = { use() {}, init() {
    const chart = native.init(null, undefined, { renderer: "svg", ssr: true, width: 1100, height: 800 })
    const dispatch = chart.dispatchAction.bind(chart)
    chart.dispatchAction = (action) => { dispatches.push(action); return dispatch(action) }
    charts.push(chart); return chart
  } }
  const dependencies = { react: hooks, "./adapter": requireModule("./adapter.ts"), "./theme": theme, "echarts/core": core, "echarts/charts": {}, "echarts/components": {}, "echarts/renderers": {} }
  const module = { exports: {} }
  const source = readFileSync(new URL("./echarts-candlestick.tsx", import.meta.url), "utf8")
  const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText
  vm.runInNewContext(output, { module, exports: module.exports, require: (name) => dependencies[name] ?? requireModule(name), ResizeObserver: class { observe() {} disconnect() {} } })
  const mountRefs = (node) => {
    if (Array.isArray(node)) return node.forEach(mountRefs)
    if (!node || typeof node !== "object") return
    if (node.props?.ref && !node.props.ref.current) node.props.ref.current = { addEventListener() {}, removeEventListener() {}, innerHTML: "" }
    mountRefs(node.props?.children)
  }
  return {
    charts, dispatches,
    render(props) {
      refCursor = memoCursor = effectCursor = 0
      const node = module.exports.EChartsCandlestick({ data, activeIndicators: ["vol"], ...props })
      mountRefs(node)
      while (pending.length) pending.shift()()
    },
    dispose() { for (const effect of effects) effect?.cleanup?.() },
  }
}

test("date remount does not replay an old zoom command and new range replaces a previous manual pan", () => {
  const harness = mountNativeRenderer()
  try {
    harness.render({ visibleDateRange: range, zoomRevision: 7, zoomScale: 0.8 })
    assert.equal(harness.dispatches.length, 0, "a previous page command must not override a newly selected date range")
    const chart = harness.charts[0]
    assert.equal(chart.getOption().dataZoom[0].endValue, 64)
    harness.render({ visibleDateRange: range, zoomRevision: 8, zoomScale: 1.25 })
    assert.equal(harness.dispatches.length, 1, "only a new command executes")
    chart.dispatchAction({ type: "dataZoom", start: 0, end: 100 })
    harness.render({ visibleDateRange: { start: dates[68], end: dates[71] }, zoomRevision: 8, zoomScale: 1.25 })
    assert.equal(chart.getOption().dataZoom[0].startValue, 68)
    assert.equal(chart.getOption().dataZoom[0].endValue, 71)
    assert.equal(chart.getModel().getSeries().find((series) => series.name === "MA60").getRawData().count(), 80)
  } finally { harness.dispose() }
})

test("manual pan and a valid hover outside the initial range retain the same day across indicator changes", () => {
  const harness = mountNativeRenderer()
  const hovered = []
  const onHoverDate = (day) => hovered.push(day)
  try {
    harness.render({ visibleDateRange: range, onHoverDate })
    const chart = harness.charts[0]
    chart.dispatchAction({ type: "dataZoom", start: 0, end: 100 })
    chart.trigger("updateaxispointer", { axesInfo: [{ value: 20 }] })
    assert.equal(hovered.at(-1), dates[20])
    harness.render({ visibleDateRange: range, onHoverDate, activeIndicators: ["vol", "macd"] })
    const activeChart = harness.charts.at(-1)
    const graphic = activeChart.getOption().graphic
    const text = JSON.stringify(graphic)
    assert.match(text, /0\.21/)
    assert.doesNotMatch(text, /0\.65/)
    assert.equal(activeChart.getOption().dataZoom[0].startValue, 0)
    assert.equal(activeChart.getOption().dataZoom[0].endValue, 79)
    assert.match(JSON.stringify(optionFor(range, 70).graphic), /0\.71/)
  } finally { harness.dispose() }
})
