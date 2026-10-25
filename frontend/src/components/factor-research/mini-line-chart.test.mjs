import assert from "node:assert/strict"
import test from "node:test"
import { readFileSync } from "node:fs"
import vm from "node:vm"
import { createRequire } from "node:module"
import ts from "typescript"

const source = readFileSync(new URL("./mini-line-chart.tsx", import.meta.url), "utf8")
const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText
const ninePoints = Array.from({ length: 9 }, (_, index) => ({ x: `2026-09-${String(index + 1).padStart(2, "0")}`, y: 1 + index / 100 }))

function mount(points = ninePoints) {
  const slots = [], pendingEffects = [], charts = [], observers = [], dateInputs = []
  const container = { clientWidth: 760 }
  const format = (value) => value.toFixed(3)
  let cursor = 0, theme = "dark"
  const dependencies = {
    react: {
      useRef(initial) { const position = cursor++; if (!(position in slots)) slots[position] = { current: initial }; return slots[position] },
      useEffect(effect, values) { const position = cursor++; const previous = slots[position]; if (!previous || values.some((value, index) => !Object.is(value, previous.values[index]))) { pendingEffects.push(() => { previous?.cleanup?.(); const cleanup = effect(); slots[position] = { values, cleanup } }) } },
    },
    "@/stores/display": { useDisplayStore: (selector) => selector({ theme }) },
    "@/components/market/kline/utils": { makeChartOpts: () => ({ layout: { background: { color: theme } }, timeScale: { rightOffset: 40, rightOffsetPixels: 200, timeVisible: true, barSpacing: 8 } }) },
    "lightweight-charts": {
      LineSeries: "line",
      createChart(_element, options) {
        const chart = { options, data: [], fitCalls: 0, removed: false, range: null, applyOptions(next) { this.options = { ...this.options, ...next, timeScale: { ...this.options.timeScale, ...next.timeScale } }; if (next.timeScale) this.range = { from: Math.max(0, this.data.length - 2), to: this.data.length - 1 + Number(this.options.timeScale.rightOffset ?? 0) } }, addSeries() { return { applyOptions() {}, setData(data) { chart.data = data } } }, timeScale() { return { fitContent() { chart.fitCalls++; const offset = Number(chart.options.timeScale.rightOffset ?? 0) + Number(chart.options.timeScale.rightOffsetPixels ?? 0) / 8; chart.range = { from: offset ? Math.max(0, chart.data.length - 2) : 0, to: chart.data.length - 1 + offset } } } }, remove() { this.removed = true } }
        charts.push(chart)
        return chart
      },
    },
  }
  const module = { exports: {} }, requireModule = createRequire(import.meta.url)
  vm.runInNewContext(output, { module, exports: module.exports, require: (name) => dependencies[name] ?? requireModule(name), Date: class extends Date { constructor(...args) { if (typeof args[0] === "string") dateInputs.push(args[0]); super(...args) } static parse(value) { dateInputs.push(value); return Date.parse(value) } }, Number, RangeError, ResizeObserver: class { constructor(callback) { this.callback = callback; this.disconnected = false; observers.push(this) } observe() {} disconnect() { this.disconnected = true } } })
  function render() { cursor = 0; const tree = module.exports.MiniLineChart({ points, valueFormat: format }); tree.props.ref.current = container; pendingEffects.splice(0).forEach((effect) => effect()); return tree }
  return { render, charts, observers, dateInputs, setTheme(value) { theme = value }, resize(width) { container.clientWidth = width; observers.at(-1).callback() }, unmount() { slots.forEach((slot) => slot?.cleanup?.()) } }
}

function assertCompleteSeries(chart) {
  assert.equal(chart.options.timeScale.rightOffset, 0)
  assert.equal(chart.options.timeScale.rightOffsetPixels, 0)
  assert.equal(chart.options.timeScale.timeVisible, true)
  assert.equal(chart.data.length, 9)
  assert.equal(chart.range.from, 0)
  assert.equal(chart.range.to, 8)
  assert.equal(chart.data[0].value, 1)
  assert.equal(chart.data[8].value, 1.08)
}

test("initial creation and theme effect show all nine NAV points with no futures candle offset", () => {
  const page = mount(); page.render()
  assert.equal(page.charts.length, 1)
  assertCompleteSeries(page.charts[0])
  assert.equal(page.charts[0].fitCalls, 2)
})

test("theme change and resize preserve complete series, refresh viewport and release lifecycle resources", () => {
  const page = mount(); page.render()
  page.setTheme("light"); page.render()
  assert.equal(page.charts.length, 1)
  assert.equal(page.charts[0].options.layout.background.color, "light")
  assertCompleteSeries(page.charts[0])
  const previousFits = page.charts[0].fitCalls
  page.resize(320)
  assert.equal(page.charts[0].options.width, 320)
  assert.ok(page.charts[0].fitCalls > previousFits)
  assertCompleteSeries(page.charts[0])
  page.unmount()
  assert.equal(page.charts[0].removed, true)
  assert.equal(page.observers[0].disconnected, true)
})

test("daily dates use explicit UTC midnight and existing full timestamp formats remain compatible", () => {
  const points = [{ x: "2026-09-01", y: 1 }, { x: "2026-09-02 09:30:00", y: 1.01 }, { x: "2026-09-03T09:30:00Z", y: 1.02 }, { x: "2026-09-04T09:30:00+08:00", y: 1.03 }]
  const page = mount(points); page.render()
  assert.ok(page.dateInputs.includes("2026-09-01T00:00:00Z"))
  assert.ok(!page.dateInputs.some((value) => /^\d{4}-\d{2}-\d{2}Z$/.test(value)))
  assert.deepEqual(Array.from(page.charts[0].data, (row) => row.time), points.map((point) => Date.parse(point.x.length === 10 ? `${point.x}T00:00:00Z` : point.x.includes(" ") ? point.x.replace(" ", "T") + "Z" : point.x) / 1000))
})

test("invalid dates and nonfinite chart inputs are rejected before chart allocation rather than fabricated as zero", () => {
  for (const point of [{ x: "invalid", y: 1 }, { x: "2026-02-30", y: 1 }, { x: NaN, y: 1 }, { x: "2026-09-01", y: Infinity }]) {
    const page = mount([point])
    assert.throws(() => page.render(), /研究图表/)
    assert.equal(page.charts.length, 0)
  }
})
