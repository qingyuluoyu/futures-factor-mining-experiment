import assert from "node:assert/strict"
import test from "node:test"
import { readFileSync } from "node:fs"
import { createRequire } from "node:module"
import vm from "node:vm"
import ts from "typescript"
import * as priceChange from "@/lib/stock-price-change"
import * as chartData from "./stock-chart-data.ts"
import * as tickAdapter from "./tick/adapter.ts"
import * as chartWindow from "./stock-chart-window.ts"
import * as indicatorCatalog from "./stock-chart-indicators.ts"
import { loadTsx } from "../dashboard/dashboard-frame-harness.mjs"
import { renderToStaticMarkup } from "react-dom/server"

const source = readFileSync(new URL("./stock-kline-chart.tsx", import.meta.url), "utf8")
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText
const requireModule = createRequire(import.meta.url)
function text(tree) { return Array.isArray(tree) ? tree.map(text).join("") : tree && typeof tree === "object" ? text(tree.props?.children) : tree == null || typeof tree === "boolean" ? "" : String(tree) }
function elements(tree) { return Array.isArray(tree) ? tree.flatMap(elements) : tree && typeof tree === "object" ? [tree, ...elements(tree.props?.children)] : [] }
function render(period = "day", changeBasis = "vendor_ex_rights_pre_close", harness = {}) {
  const states = harness.states ??= []
  const memos = harness.memos ??= []
  const refs = harness.refs ??= [], effects = harness.effectSlots ??= []
  let stateCursor = 0, memoCursor = 0, refCursor = 0, effectCursor = 0
  const preferences = harness.preferences ??= { period, visible: 60, showMA: true, showVolume: true, showMACD: false, showRSI: false, showKDJ: false, showBOLL: false, showCrosshair: true, volumeCompareEnabled: true, volumeCompareDays: 1 }
  const inputBars = harness.bars ??= [
    { time: "2026-10-15", open: 100, high: 100, low: 100, close: 100, volume: 0.01 },
    { time: "2026-10-16", open: 10, high: 11, low: 9, close: 11, volume: 0.01, pre_close: 10 },
  ]
  const dependencies = {
    "next/dynamic": { default: (loader, options) => { harness.loaders?.push({ loader, options }); return function TickCanvas() { return null } } },
    "./tick/adapter": tickAdapter,
    "./stock-chart-boundary": loadTsx(readFileSync(new URL("./stock-chart-boundary.tsx", import.meta.url), "utf8")),
    "./stock-chart-window": chartWindow,
    "./stock-chart-indicators": indicatorCatalog,
    "lightweight-charts": {},
    react: { useEffect(callback, deps) { const i = effectCursor++; if (!effects[i] || deps.some((value, j) => !Object.is(value, effects[i].deps[j]))) { effects[i]?.cleanup?.(); effects[i] = { deps }; (harness.effects ??= []).push(() => { effects[i].cleanup = callback() }) } }, useMemo(callback, deps) { const i = memoCursor++; if (!memos[i] || deps.some((value, j) => !Object.is(value, memos[i].deps[j]))) memos[i] = { deps, value: callback() }; return memos[i].value }, useRef(value) { const i = refCursor++; return refs[i] ??= { current: value } }, useState(value) { const i = stateCursor++; if (!(i in states)) states[i] = typeof value === "function" ? value() : value; return [states[i], (next) => { states[i] = typeof next === "function" ? next(states[i]) : next }] } },
    "@/lib/chart-theme": { useChartTheme: () => ({}) },
    "@/lib/utils": { cn: (...values) => values.filter(Boolean).join(" ") },
    "@/stores/stock-market": { useStockMarketStore: (selector) => selector({ chartPreferences: preferences, setChartPreferences(patch) { (harness.preferencePatches ??= []).push(patch); Object.assign(preferences, patch) } }) },
    "@/lib/stock-price-change": priceChange,
    "./stock-chart-data": chartData,
  }
  const module = { exports: {} }
  vm.runInNewContext(compiled, { module, exports: module.exports, window: harness.window, ResizeObserver: harness.ResizeObserver, getComputedStyle: harness.getComputedStyle, require: (name) => dependencies[name] ?? requireModule(name) })
  return module.exports.StockKlineChart({ bars: inputBars, symbolLabel: "605058", upColor: "red", downColor: "green", priceAdjustmentLabel: harness.priceAdjustmentLabel ?? "不复权", changeBasis, volumeUnit: "手" })
}

test("chart hover uses the same raw vendor reference and never rounds fractional lot to zero", () => {
  const tree = render()
  assert.match(text(tree), /\+10\.00%/)
  assert.doesNotMatch(text(tree), /-89\.00%/)
  assert.match(text(tree), /量 0\.01 手/)
  assert.ok(elements(tree).some((item) => item.props?.title === "0.01 手"))
})

test("raw week and month do not infer comparable multi-day returns across corporate actions", () => {
  for (const period of ["week", "month"]) {
    const tree = render(period)
    assert.match(text(tree), /跨除权不可直接比较/)
    assert.doesNotMatch(text(tree), /-89\.00%|\+10\.00%/)
  }
})

test("replay chart remains unavailable without reference and caller explicitly passes safe basis", () => {
  assert.match(text(render("day", "unavailable")), /未提供可比参考价/)
  const replay = readFileSync(new URL("../trading/stock-replay-workspace.tsx", import.meta.url), "utf8")
  assert.match(replay, /volumeUnit="股" changeBasis="unavailable"/)
})

test("public stock chart mounts the ported tick workspace with verified data and explicit viewport controls", () => {
  const tree = render()
  const canvas = elements(tree).find((item) => item.type?.name === "TickCanvas")
  assert.ok(canvas, "stock entry still uses its separately rewritten chart")
  assert.equal(canvas.props.data.at(-1).change_pct, 10)
  assert.equal(canvas.props.data.at(-1).volume, 0.01)
  assert.deepEqual(Array.from(canvas.props.activeIndicators), ["vol"])
  assert.equal(canvas.props.visibleBars, 60)
  assert.ok(canvas.props.height >= 320 && canvas.props.height <= 600)
  assert.equal(typeof canvas.props.onHoverDate, "function")
})

test("chart keeps natural content height and discloses actual loaded rows under the selected window", () => {
  const tree = render()
  assert.doesNotMatch(tree.props.className, /(?:^|\s)h-full(?:\s|$)/)
  assert.match(tree.props.className, /shrink-0/)
  const surface = elements(tree).find((item) => item.props?.["data-chart-engine"] === "tick-echarts")
  assert.match(surface.props.className, /shrink-0/)
  assert.doesNotMatch(surface.props.className, /flex-1/)
  assert.match(text(tree), /已载入 2 根/)
})

test("failed chart only reconstructs its loader after manual retry and retains quote and indicator inputs", () => {
  const harness = { loaders: [] }
  const first = render("day", "vendor_ex_rights_pre_close", harness)
  const before = elements(first).find((item) => item.type?.name === "TickCanvas")
  assert.equal(harness.loaders.length, 1)
  const failure = harness.loaders[0].options.loading({ error: new Error("private-chunk-location") })
  assert.match(renderToStaticMarkup(failure), /重试图表/)
  assert.doesNotMatch(renderToStaticMarkup(failure), /正在加载|private-chunk-location/)
  const boundary = elements(first).find((item) => item.type?.name === "StockChartBoundary")
  assert.ok(boundary, "renderer errors must stay in a stock-chart-only boundary")
  assert.equal(harness.loaders.length, 1)
  elements(failure).find((item) => item.type === "button").props.onClick()
  const second = render("day", "vendor_ex_rights_pre_close", harness)
  const after = elements(second).find((item) => item.type?.name === "TickCanvas")
  assert.equal(harness.loaders.length, 2)
  assert.notEqual(before.type, after.type)
  assert.deepEqual(JSON.parse(JSON.stringify(before.props.data)), JSON.parse(JSON.stringify(after.props.data)))
  assert.equal(after.props.visibleBars, before.props.visibleBars)
  assert.deepEqual(after.props.activeIndicators, before.props.activeIndicators)
})

const canvasIn = (tree) => elements(tree).find((item) => item.type?.name === "TickCanvas")
const dateInput = (tree, label) => elements(tree).find((item) => item.props?.["aria-label"] === label)
const rangeBars = () => Array.from({ length: 70 }, (_, i) => ({ time: new Date(Date.UTC(2026, 0, i + 1)).toISOString().slice(0, 10), open: 10 + i, high: 12 + i, low: 9 + i, close: 11 + i, volume: 0.01 }))

test("loaded date controls only set a viewport and retain full indicator prefixes and cursor-bound data", () => {
  const harness = { bars: rangeBars() }
  let tree = render("day", "adjacent_loaded_adjusted_close", harness)
  assert.ok(dateInput(tree, "K线开始日期"))
  dateInput(tree, "K线开始日期").props.onChange({ target: { value: harness.bars[65].time } })
  tree = render("day", "adjacent_loaded_adjusted_close", harness)
  dateInput(tree, "K线结束日期").props.onChange({ target: { value: harness.bars[67].time } })
  tree = render("day", "adjacent_loaded_adjusted_close", harness)
  const canvas = canvasIn(tree)
  assert.equal(canvas.props.data.length, 70, "a viewport cannot remove indicator history")
  assert.equal(canvas.props.data[67].ma60, chartData.stockSMA(harness.bars, 60).find((point) => point.time === harness.bars[67].time).value)
  assert.deepEqual(JSON.parse(JSON.stringify(canvas.props.visibleDateRange)), { start: harness.bars[65].time, end: harness.bars[67].time })
  assert.match(text(tree), /截至所指周期.*68\s*根/)
  assert.ok(elements(tree).some((item) => item.props?.["data-indicator-line"] === "ma60" && /可用/.test(text(item))))
  assert.equal(dateInput(tree, "K线结束日期").props.max, harness.bars.at(-1).time)
})

test("invalid and empty local windows replace only the chart with explicit recovery and no fabricated bars", () => {
  const harness = {}
  let tree = render("day", "unavailable", harness)
  const changeDate = (label, value) => { dateInput(tree, label).props.onChange({ target: { value } }); tree = render("day", "unavailable", harness) }
  assert.ok(dateInput(tree, "K线开始日期"))
  changeDate("K线开始日期", "2026-10-16"); changeDate("K线结束日期", "2026-10-15")
  assert.match(text(tree), /开始日期不能晚于结束日期/)
  assert.equal(canvasIn(tree), undefined)
  changeDate("K线结束日期", ""); changeDate("K线开始日期", "2026-10-17")
  assert.match(text(tree), /所选日期范围没有已载入/)
  assert.equal(canvasIn(tree), undefined)
  elements(tree).find((item) => item.type === "button" && text(item) === "显示全部已载入日期").props.onClick()
  tree = render("day", "unavailable", harness)
  assert.equal(canvasIn(tree).props.data.length, 2)
  assert.equal(canvasIn(tree).props.visibleBars, "all")
  assert.equal(dateInput(tree, "K线开始日期").props.value, "")
})

test("latest sixty reset also clears an explicit date viewport", () => {
  const harness = {}
  let tree = render("day", "unavailable", harness)
  dateInput(tree, "K线开始日期").props.onChange({ target: { value: "2026-10-16" } })
  tree = render("day", "unavailable", harness)
  assert.ok(canvasIn(tree).props.visibleDateRange)
  elements(tree).find((item) => item.props?.["aria-label"] === "复位K线").props.onClick()
  tree = render("day", "unavailable", harness)
  assert.equal(canvasIn(tree).props.visibleDateRange, undefined)
  assert.equal(canvasIn(tree).props.visibleBars, 60)
})

test("same-symbol replacement dataset cannot inherit a date window from its previous source", () => {
  const harness = {}
  let tree = render("day", "unavailable", harness)
  assert.ok(dateInput(tree, "K线开始日期"))
  dateInput(tree, "K线开始日期").props.onChange({ target: { value: "2026-10-16" } })
  harness.bars = [{ time: "2025-01-03", open: 5, high: 6, low: 4, close: 5.5, volume: 0.01 }]
  tree = render("day", "unavailable", harness)
  assert.equal(dateInput(tree, "K线开始日期").props.value, "")
  assert.equal(canvasIn(tree).props.visibleDateRange, undefined)
  assert.equal(canvasIn(tree).props.data[0].date, "2025-01-03")
})

test("all six indicators and compact preset only change the existing indicator preference fields", () => {
  const harness = {}
  let tree = render("day", "unavailable", harness)
  const all = elements(tree).find((item) => item.type === "button" && text(item) === "全部六指标")
  assert.ok(all); all.props.onClick()
  tree = render("day", "unavailable", harness)
  for (const key of ["showMA", "showVolume", "showMACD", "showRSI", "showKDJ", "showBOLL"]) assert.equal(harness.preferences[key], true)
  assert.equal(canvasIn(tree).props.activeIndicators.length, 5)
  assert.equal(elements(tree).filter((item) => item.props?.["data-indicator-line"]).length, indicatorCatalog.STOCK_CHART_INDICATORS.reduce((sum, item) => sum + item.lines.length, 0))
  elements(tree).find((item) => item.type === "button" && text(item) === "价格+成交量").props.onClick()
  tree = render("day", "unavailable", harness)
  assert.equal(harness.preferences.showVolume, true)
  for (const key of ["showMA", "showMACD", "showRSI", "showKDJ", "showBOLL"]) assert.equal(harness.preferences[key], false)
  assert.equal(harness.preferences.visible, 60)
  assert.equal(harness.preferences.showCrosshair, true)
  assert.equal(harness.preferences.volumeCompareEnabled, true)
})

test("resize observation measures its actual viewport and controls rather than window height", () => {
  let observerCallback, disconnected = false
  const observed = [], listeners = new Map()
  const harness = { window: { addEventListener: (name, callback) => listeners.set(name, callback), removeEventListener: (name) => listeners.delete(name) }, getComputedStyle: () => ({ overflowY: "auto" }), ResizeObserver: class { constructor(callback) { observerCallback = callback } observe(node) { observed.push(node) } disconnect() { disconnected = true } } }
  let tree = render("day", "unavailable", harness)
  assert.ok(tree.props.ref, "the stock workspace must observe a DOM viewport")
  const viewport = { clientHeight: 700, parentElement: null, getBoundingClientRect: () => ({ top: 0 }) }
  tree.props.ref.current = { parentElement: viewport, getBoundingClientRect: () => ({ top: 50 }) }
  const controls = elements(tree).find((item) => item.props?.["data-chart-controls"])
  assert.ok(controls?.props.ref)
  controls.props.ref.current = { offsetHeight: 140 }
  harness.effects.splice(0).forEach((effect) => effect())
  tree = render("day", "unavailable", harness)
  assert.equal(canvasIn(tree).props.height, 478)
  assert.ok(observed.includes(viewport))
  viewport.clientHeight = 400; observerCallback()
  assert.equal(canvasIn(render("day", "unavailable", harness)).props.height, 320)
  harness.effectSlots.forEach((slot) => slot?.cleanup?.())
  assert.equal(disconnected, true)
  assert.equal(listeners.size, 0)
})
