import assert from "node:assert/strict"
import test from "node:test"
import { readFileSync } from "node:fs"
import vm from "node:vm"
import { createRequire } from "node:module"
import ts from "typescript"
import { validateStockQuoteBars } from "./stock-quote-data.ts"
import * as priceChange from "@/lib/stock-price-change"
import { parseStockKlineResponse, parseStockKlineSources, latestStockKlineSource } from "@/lib/stock-kline-contract"

const output = ts.transpileModule(readFileSync(new URL("./stock-quote-detail.tsx", import.meta.url), "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText
const requireModule = createRequire(import.meta.url)
function elements(tree) { return Array.isArray(tree) ? tree.flatMap(elements) : tree && typeof tree === "object" ? [tree, ...elements(tree.props?.children)] : [] }
function text(tree) { return Array.isArray(tree) ? tree.map(text).join("") : tree && typeof tree === "object" ? text(tree.props?.children) : tree == null || typeof tree === "boolean" ? "" : String(tree) }
async function mount(metadata = {}, controls = {}) {
  const slots = [], effects = [], calls = [], detailCalls = []
  let cursor = 0
  const Chart = () => null
  const History = () => null
  const dependencies = {
    react: { useState(initial) { const index = cursor++; if (!(index in slots)) slots[index] = initial; return [slots[index], (value) => { slots[index] = typeof value === "function" ? value(slots[index]) : value }] }, useEffect(callback, deps) { const index = cursor++; if (!(index in slots) || deps.some((value, position) => value !== slots[index].deps[position])) { slots[index]?.cleanup?.(); slots[index] = { deps }; effects.push(() => { slots[index].cleanup = callback() }) } } },
    "./stock-kline-chart": { StockKlineChart: Chart },
    "./stock-fundamentals-history": { StockFundamentalsHistory: History },
    "./stock-quote-data": { validateStockQuoteBars },
    "@/lib/stock-api": { getStockKlineApi: async (code, limit, signal, source) => { calls.push({ signal, code, source }); if (controls.chartError) throw new Error(controls.chartError); return controls.chartBySource?.(source, code) ?? controls.chartPending ?? { code, name: "证券", industry: "银行", bars: [{ time: "2026-10-16", open: 10, high: 11, low: 9, close: 11, volume: 100 }], has_more: false, ...metadata } } },
    "@/lib/stock-kline-contract": {
      latestStockKlineSource,
      getStockKlineSources: async (code, signal) => { if (controls.sourceError) throw controls.sourceError; if (controls.sources || controls.sourcePending) return parseStockKlineSources(await (controls.sourcePending ?? { ...controls.sources, code }), code); throw Object.assign(new Error("目录不存在"), { status: 404 }) },
      getValidatedStockKlineApi: async (code, limit, signal, source) => parseStockKlineResponse(await dependencies["@/lib/stock-api"].getStockKlineApi(code, limit, signal, source), code),
    },
    "@/lib/stock-price-change": priceChange,
    "@/lib/stock-market-api": { getStockDetailsApi: async (_code, signal) => { detailCalls.push({ signal }); if (controls.detailError) throw new Error(controls.detailError); return controls.detailPending ?? { instrument: { code: "600001", name: "证券", instrument_id: "600001.SSE", industry: "银行" }, as_of: "2026-10-16", coverage: { daily_rows: 0, first_date: null, last_date: null }, financials: [], valuation: null, limitations: [] } } },
    "@/stores/display": { useDisplayStore: (selector) => selector({ candleUp: "red", candleDown: "green" }) },
    "@/lib/utils": { cn: (...values) => values.filter(Boolean).join(" ") },
  }
  const module = { exports: {} }
  vm.runInNewContext(output, { module, exports: module.exports, AbortController, Error, require: (name) => dependencies[name] ?? requireModule(name) })
  const render = (code = "600001") => { cursor = 0; return module.exports.StockQuoteDetail({ code, onBack: () => {} }) }
  const settle = async () => { while (effects.length) effects.shift()(); await new Promise(setImmediate) }
  render(); await settle()
  return { render, Chart, History, calls, detailCalls, settle, unmount() { slots.forEach((slot) => slot?.cleanup?.()) } }
}

test("raw EOD bars are explicitly unadjusted in header and reused chart", async () => {
  const page = await mount({ price_adjustment: "none", source: "teajoin_eod", volume_unit: "lot_100_shares" })
  let tree = page.render()
  assert.match(text(tree), /不复权/)
  assert.doesNotMatch(text(tree), /历史前复权/)
  const chart = elements(tree).find((item) => item.type === page.Chart)
  assert.equal(chart.props.priceAdjustmentLabel, "不复权")
  assert.equal(chart.props.volumeUnit, "手")
  assert.match(text(tree), /盘后/)
  assert.doesNotMatch(text(tree), /0\.00%/)
  elements(tree).find((item) => item.type === "button" && text(item) === "数据覆盖与口径").props.onClick()
  tree = page.render()
  assert.match(text(tree), /已载入.*1/)
  assert.match(text(tree), /不复权/)
  page.unmount(); assert.equal(page.calls[0].signal.aborted, true)
})

test("quote tabs share one vertical scroll owner and chart content cannot shrink under its header", async () => {
  const page = await mount()
  const tree = page.render()
  assert.match(tree.props.className, /overflow-y-auto/)
  assert.match(tree.props.className, /\[&>\*\]:shrink-0/)
  const chartPane = elements(tree).find((item) => item.props?.children?.type === page.Chart)
  assert.ok(chartPane)
  assert.doesNotMatch(chartPane.props.className, /overflow-auto|flex-1/)
  for (const label of ["估值与财务", "数据覆盖与口径"]) {
    elements(page.render()).find((item) => item.type === "button" && text(item) === label).props.onClick()
    const tab = page.render()
    const scrollOwners = elements(tab).filter((item) => /overflow-(auto|y-auto)/.test(item.props?.className ?? "") && item.type === "div")
    assert.ok(scrollOwners.includes(tab), "the root remains the vertical scroll owner")
    assert.ok(scrollOwners.filter((item) => item !== tab).every((item) => [item.props?.children].flat().some((child) => child?.type === "table")), "only financial tables may have independent horizontal scrolling")
  }
})

test("legacy kline response without metadata preserves the original forward-adjusted contract", async () => {
  const page = await mount()
  assert.equal(elements(page.render()).find((item) => item.type === page.Chart).props.priceAdjustmentLabel, "前复权")
  assert.doesNotMatch(text(page.render()), /本地所选源共|已读完当前本地来源/)
})

test("chart discloses local source coverage without claiming vendor history is exhausted", async () => {
  const page = await mount({ price_adjustment: "none", source: "teajoin_eod", volume_unit: "lot_100_shares",
    coverage: { scope: "selected_source_local_store", available_bars: 5, first_date: "2026-10-12", last_date: "2026-10-16", requested_limit: 1500, effective_limit: 250, window_status: "local_history_exhausted", history_completeness: "unverified" },
    bars: [12, 13, 14, 15, 16].map((day) => ({ time: `2026-10-${day}`, open: 10, high: 11, low: 9, close: 11, volume: 100 })),
  })
  const tree = page.render()
  assert.match(text(tree), /本地所选源共\s*5\s*根/)
  assert.match(text(tree), /2026-10-12\s*至\s*2026-10-16/)
  assert.match(text(tree), /历史完整性未核验/)
  assert.match(text(tree), /不代表供应商没有更早历史/)
  assert.doesNotMatch(text(tree), /供应商历史已完整|全部历史已完整/)
})

test("raw single day header and reused chart both carry vendor ex-rights basis", async () => {
  const page = await mount({ price_adjustment: "none", source: "teajoin_eod", volume_unit: "lot_100_shares",
    bars: [{ time: "2026-10-16", open: 10, high: 11, low: 9, close: 11, volume: 0.01, pre_close: 5 }] })
  const tree = page.render()
  assert.match(text(tree), /120\.00%/)
  assert.match(text(tree), /除权昨收/)
  assert.equal(elements(tree).find((item) => item.type === page.Chart).props.changeBasis, "vendor_ex_rights_pre_close")
})

test("raw ex-rights date does not compare with prior unadjusted close", async () => {
  const page = await mount({ price_adjustment: "none", source: "teajoin_eod", volume_unit: "lot_100_shares", bars: [
    { time: "2026-10-15", open: 100, high: 100, low: 100, close: 100, volume: 100 },
    { time: "2026-10-16", open: 10, high: 11, low: 9, close: 11, volume: 0.01, pre_close: 10 },
  ] })
  assert.match(text(page.render()), /10\.00%/)
  assert.doesNotMatch(text(page.render()), /-89\.00%/)
})

test("mismatched response metadata fails without retaining a quote or chart", async () => {
  const page = await mount({ price_adjustment: "none", source: "local_import" })
  const tree = page.render()
  assert.match(text(tree), /日线响应/)
  assert.equal(elements(tree).some((item) => item.type === page.Chart), false)
  assert.doesNotMatch(text(tree), /11\.00/)
})

test("stock financial tab mounts paginated history without changing chart or old summaries", async () => {
  const page = await mount()
  elements(page.render()).find((item) => item.type === "button" && text(item) === "估值与财务").props.onClick()
  const tree = page.render()
  const history = elements(tree).find((item) => item.type === page.History)
  assert.ok(history)
  assert.equal(history.props.code, "600001")
  assert.match(text(tree), /已披露财务/)
})

test("slow fundamentals never block a validated chart", async () => {
  const page = await mount({}, { detailPending: new Promise(() => {}) })
  assert.ok(elements(page.render()).find((item) => item.type === page.Chart))
  assert.match(text(page.render()), /11\.00/)
  page.unmount()
  assert.equal(page.detailCalls[0].signal.aborted, true)
})

test("slow chart never blocks a successful financial view", async () => {
  const page = await mount({}, { chartPending: new Promise(() => {}) })
  elements(page.render()).find((item) => item.type === "button" && text(item) === "估值与财务").props.onClick()
  assert.ok(elements(page.render()).find((item) => item.type === page.History))
  assert.match(text(page.render()), /该证券暂无估值覆盖/)
  page.unmount()
})

test("retrying failed fundamentals does not fetch the chart again", async () => {
  const controls = { detailError: "财务读取失败" }
  const page = await mount({}, controls)
  assert.ok(elements(page.render()).find((item) => item.type === page.Chart))
  const retry = elements(page.render()).find((item) => item.type === "button" && text(item) === "重试估值与财务")
  assert.ok(retry)
  controls.detailError = null
  retry.props.onClick(); page.render(); await page.settle()
  assert.equal(page.calls.length, 1)
  assert.equal(page.detailCalls.length, 2)
  assert.doesNotMatch(text(page.render()), /财务读取失败/)
})

test("chart error stays distinct from empty data and retries only the chart", async () => {
  const controls = { chartError: "K线连接失败" }
  const page = await mount({}, controls)
  assert.match(text(page.render()), /K线连接失败/)
  assert.doesNotMatch(text(page.render()), /证券已收录，尚未导入日线/)
  const retry = elements(page.render()).find((item) => item.type === "button" && text(item) === "重试K线")
  assert.ok(retry)
  controls.chartError = null
  retry.props.onClick(); page.render(); await page.settle()
  assert.equal(page.calls.length, 2)
  assert.equal(page.detailCalls.length, 1)
  assert.ok(elements(page.render()).find((item) => item.type === page.Chart))
})

test("unmounted chart and fundamentals discard late success and failure", async () => {
  let resolveChart, rejectDetail
  const controls = { chartPending: new Promise((resolve) => { resolveChart = resolve }), detailPending: new Promise((_resolve, reject) => { rejectDetail = reject }) }
  const page = await mount({}, controls)
  page.unmount()
  resolveChart({ code: "600001", name: "迟到证券", industry: "银行", bars: [{ time: "2026-10-16", open: 10, high: 11, low: 9, close: 11, volume: 100 }], has_more: false })
  rejectDetail(new Error("旧财务错误"))
  await new Promise(setImmediate)
  assert.equal(elements(page.render()).some((item) => item.type === page.Chart), false)
  assert.doesNotMatch(text(page.render()), /旧财务错误|11\.00/)
})

const sources = { catalog_version: "stock-kline-sources-v1", code: "600001", default_mode: "legacy_auto", sources: [
  { id: "history_qfq", source: "local_import", price_adjustment: "qfq", available: true, reason: "available", available_bars: 1, first_date: "2026-10-15", last_date: "2026-10-15", window_cap: 3000, history_completeness: "unverified" },
  { id: "eod_raw", source: "teajoin_eod", price_adjustment: "none", available: true, reason: "available", available_bars: 1, first_date: "2026-10-16", last_date: "2026-10-16", window_cap: 250, history_completeness: "unverified" },
] }
const quoteFor = (source, code = "600001", close = 11) => ({ code, name: "证券", industry: "银行", bars: [{ time: source === "eod_raw" ? "2026-10-16" : "2026-10-15", open: close, high: close, low: close, close, volume: 100 }], has_more: false,
  selected_source: source, price_adjustment: source === "eod_raw" ? "none" : "qfq", source: source === "eod_raw" ? "teajoin_eod" : "local_import", volume_unit: "lot_100_shares" })

test("source selector chooses latest actual source then fetches only the explicit requested source", async () => {
  const page = await mount({}, { sources, chartBySource: quoteFor })
  assert.equal(page.calls[0].source, "eod_raw")
  assert.match(text(page.render()), /2026-10-16/)
  const selector = elements(page.render()).find((item) => item.props?.["aria-label"] === "日线数据来源")
  assert.equal(selector.props.value, "eod_raw")
  selector.props.onChange({ target: { value: "history_qfq" } })
  assert.equal(elements(page.render()).some((item) => item.type === page.Chart), false)
  await page.settle()
  assert.equal(page.calls[0].signal.aborted, true)
  assert.equal(page.calls.at(-1).source, "history_qfq")
  assert.match(text(page.render()), /2026-10-15/)
  assert.equal(elements(page.render()).find((item) => item.type === page.Chart).props.priceAdjustmentLabel, "前复权")
})
test("missing old source directory is visibly legacy mode while other directory failures never fetch kline", async () => {
  const old = await mount()
  assert.match(text(old.render()), /旧模式/)
  assert.ok(elements(old.render()).find((item) => item.type === "button" && text(item) === "重试来源目录"))
  const blocked = await mount({}, { sourceError: Object.assign(new Error("来源目录服务失败"), { status: 503 }) })
  assert.equal(blocked.calls.length, 0)
  assert.match(text(blocked.render()), /来源目录服务失败/)
  assert.equal(elements(blocked.render()).some((item) => item.type === blocked.Chart), false)
})
test("source switches discard late prior source success and failed explicit source never falls back", async () => {
  let resolveRaw
  const controls = { sources, chartBySource: (source, code) => source === "eod_raw" ? new Promise((resolve) => { resolveRaw = resolve }) : quoteFor(source, code, 99) }
  const page = await mount({}, controls)
  elements(page.render()).find((item) => item.props?.["aria-label"] === "日线数据来源").props.onChange({ target: { value: "history_qfq" } })
  page.render(); await page.settle()
  resolveRaw(quoteFor("eod_raw", "600001", 11)); await new Promise(setImmediate)
  assert.match(text(page.render()), /99\.00/)
  assert.doesNotMatch(text(page.render()), /11\.00/)
  controls.chartError = "所选来源不可用"
  elements(page.render()).find((item) => item.props?.["aria-label"] === "日线数据来源").props.onChange({ target: { value: "eod_raw" } })
  page.render(); await page.settle()
  assert.match(text(page.render()), /所选来源不可用/)
  assert.equal(elements(page.render()).some((item) => item.type === page.Chart), false)
  assert.equal(page.calls.at(-1).source, "eod_raw")
})

test("late directory from an earlier symbol is cancelled and cannot fetch that symbol's bars", async () => {
  let resolveDirectory
  const controls = { sourcePending: new Promise((resolve) => { resolveDirectory = resolve }), chartBySource: quoteFor }
  const page = await mount({}, controls)
  controls.sourcePending = null; controls.sources = sources
  page.render("600002"); await page.settle()
  resolveDirectory(sources); await new Promise(setImmediate)
  assert.equal(page.calls.length, 1)
  assert.equal(page.calls[0].code, "600002")
})

test("unavailable catalog is distinct from missing directory and never triggers automatic legacy fetch", async () => {
  const unavailable = { ...sources, sources: sources.sources.map((item) => ({ ...item, available: false, reason: "no_local_history", available_bars: 0, first_date: null, last_date: null })) }
  const page = await mount({}, { sources: unavailable })
  assert.equal(page.calls.length, 0)
  assert.match(text(page.render()), /没有开放且含本地日线的来源/)
  assert.doesNotMatch(text(page.render()), /旧模式/)
})

test("directory disappearing after explicit selection requires deliberate legacy choice", async () => {
  const controls = { sources, chartBySource: quoteFor }
  const page = await mount({}, controls)
  elements(page.render()).find((item) => item.props?.["aria-label"] === "日线数据来源").props.onChange({ target: { value: "history_qfq" } })
  page.render(); await page.settle()
  controls.sourceError = Object.assign(new Error("Not Found"), { status: 404 })
  elements(page.render()).find((item) => item.type === "button" && text(item) === "重试K线")?.props.onClick()
  elements(page.render()).find((item) => item.type === "button" && text(item) === "重新加载").props.onClick()
  page.render(); await page.settle()
  assert.equal(page.calls.length, 2)
  assert.match(text(page.render()), /无法确认所选来源/)
  assert.ok(elements(page.render()).find((item) => item.type === "button" && text(item) === "使用旧模式"))
})
