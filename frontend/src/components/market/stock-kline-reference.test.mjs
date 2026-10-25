import assert from "node:assert/strict"
import test from "node:test"
import { readFileSync } from "node:fs"
import { createRequire } from "node:module"
import vm from "node:vm"
import ts from "typescript"
import * as priceChange from "@/lib/stock-price-change"
import * as chartData from "./stock-chart-data.ts"
import * as tickAdapter from "./tick/adapter.ts"
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
  let stateCursor = 0, memoCursor = 0
  const preferences = { period, visible: 60, showMA: true, showVolume: true, showMACD: false, showRSI: false, showKDJ: false, showBOLL: false, showCrosshair: true, volumeCompareEnabled: true, volumeCompareDays: 1 }
  const dependencies = {
    "next/dynamic": { default: (loader, options) => { harness.loaders?.push({ loader, options }); return function TickCanvas() { return null } } },
    "./tick/adapter": tickAdapter,
    "./stock-chart-boundary": loadTsx(readFileSync(new URL("./stock-chart-boundary.tsx", import.meta.url), "utf8")),
    "lightweight-charts": {},
    react: { useEffect() {}, useMemo(callback, deps) { const i = memoCursor++; if (!memos[i] || deps.some((value, j) => !Object.is(value, memos[i].deps[j]))) memos[i] = { deps, value: callback() }; return memos[i].value }, useRef: (value) => ({ current: value }), useState(value) { const i = stateCursor++; if (!(i in states)) states[i] = value; return [states[i], (next) => { states[i] = typeof next === "function" ? next(states[i]) : next }] } },
    "@/lib/chart-theme": { useChartTheme: () => ({}) },
    "@/lib/utils": { cn: (...values) => values.filter(Boolean).join(" ") },
    "@/stores/stock-market": { useStockMarketStore: (selector) => selector({ chartPreferences: preferences, setChartPreferences() {} }) },
    "@/lib/stock-price-change": priceChange,
    "./stock-chart-data": chartData,
  }
  const module = { exports: {} }
  vm.runInNewContext(compiled, { module, exports: module.exports, require: (name) => dependencies[name] ?? requireModule(name) })
  return module.exports.StockKlineChart({ bars: [
    { time: "2026-10-15", open: 100, high: 100, low: 100, close: 100, volume: 0.01 },
    { time: "2026-10-16", open: 10, high: 11, low: 9, close: 11, volume: 0.01, pre_close: 10 },
  ], upColor: "red", downColor: "green", priceAdjustmentLabel: "不复权", changeBasis, volumeUnit: "手" })
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
