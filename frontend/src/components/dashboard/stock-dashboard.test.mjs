import assert from "node:assert/strict"
import test from "node:test"
import { existsSync, readFileSync } from "node:fs"
import { createRequire } from "node:module"
import vm from "node:vm"
import ts from "typescript"

const filename = new URL("./stock-dashboard.tsx", import.meta.url)
const requireModule = createRequire(import.meta.url)
const EodStatus = () => null
const boards = {
  as_of: "2026-10-16", previous_date: "2026-10-15", source: "teajoin_eod", price_adjustment: "none", has_more: false,
  coverage: { instruments: 5000, quoted: 200, comparable: 150 }, total: 1,
  boards: [{ industry: "银行", count: 100, quoted_count: 20, comparable_count: 15, advancing: 10, declining: 3, flat: 2, change_pct: 1.23, leader: null }],
}
function elements(tree) { return Array.isArray(tree) ? tree.flatMap(elements) : tree && typeof tree === "object" ? [tree, ...elements(tree.props?.children)] : [] }
function text(tree) { return Array.isArray(tree) ? tree.map(text).join("") : tree && typeof tree === "object" ? text(tree.props?.children) : tree == null || typeof tree === "boolean" ? "" : String(tree) }
function mount({ response = boards, error = null, deferred = null } = {}) {
  assert.ok(existsSync(filename), "stock dashboard component must exist")
  const output = ts.transpileModule(readFileSync(filename, "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText
  const slots = [], effects = [], calls = []
  const controls = { response, error, deferred }
  let cursor = 0
  const Members = () => null, Detail = () => null
  const dependencies = {
    react: {
      useState(initial) { const index = cursor++; if (!(index in slots)) slots[index] = typeof initial === "function" ? initial() : initial; return [slots[index], (value) => { slots[index] = typeof value === "function" ? value(slots[index]) : value }] },
      useEffect(callback, deps) { const index = cursor++; if (!(index in slots) || deps.some((value, position) => value !== slots[index].deps[position])) { slots[index]?.cleanup?.(); slots[index] = { deps }; effects.push(() => { slots[index].cleanup = callback() }) } },
    },
    "@/lib/stock-market-api": { getStockBoardsApi: async (keyword, signal) => { calls.push({ keyword, signal }); if (controls.error) throw new Error(controls.error); return controls.deferred ?? controls.response } },
    "@/components/market/stock-board-browser": { StockMembers: Members },
    "@/components/market/stock-quote-detail": { StockQuoteDetail: Detail },
    "@/components/market/stock-eod-status": { StockEodStatus: EodStatus },
    "next/link": { default: "a" },
    "@/lib/utils": { cn: (...values) => values.filter(Boolean).join(" ") },
  }
  const module = { exports: {} }
  vm.runInNewContext(output, { module, exports: module.exports, AbortController, Error, require: (name) => dependencies[name] ?? requireModule(name) })
  const render = () => { cursor = 0; return module.exports.StockDashboard() }
  return { render, calls, controls, Members, Detail, async settle() { while (effects.length) effects.shift()(); await new Promise(setImmediate) }, unmount() { slots.forEach((slot) => slot?.cleanup?.()) } }
}

test("dashboard exposes independent EOD status and shares the existing refresh revision", async () => {
  const page = mount()
  assert.equal(elements(page.render()).find((item) => item.type === EodStatus)?.props.refreshKey, 0)
  await page.settle()
  elements(page.render()).find((item) => item.type === "button" && text(item) === "刷新看板").props.onClick()
  assert.equal(elements(page.render()).find((item) => item.type === EodStatus)?.props.refreshKey, 1)
})

test("stock dashboard replaces the replay form and preserves the futures dashboard branch", () => {
  const page = readFileSync(new URL("../../app/(main)/dashboard/page.tsx", import.meta.url), "utf8")
  assert.match(page, /market === "stock" \? <StockDashboard/)
  assert.doesNotMatch(page, /StockReplayWorkspace/)
  for (const component of ["ActiveChannelBadge", "AssetSummaryBar", "WatchlistTable", "PositionOverview", "RecentTrades", "AiTradingOverview"]) assert.match(page, new RegExp(`<${component} \/>`))
})

test("dashboard displays actual coverage and sample breadth with actionable industry and chart entry", async () => {
  const page = mount(); assert.match(text(page.render()), /加载/); await page.settle()
  let tree = page.render()
  assert.match(text(tree), /5000/); assert.match(text(tree), /200/); assert.match(text(tree), /2026-10-16/)
  assert.match(text(tree), /可比样本/); assert.match(text(tree), /不复权/)
  assert.match(text(tree), /涨\s*10/); assert.match(text(tree), /跌\s*3/); assert.match(text(tree), /平\s*2/)
  elements(tree).find((item) => item.type === "button" && text(item).includes("银行")).props.onClick()
  tree = page.render()
  const members = elements(tree).find((item) => item.type === page.Members)
  assert.equal(members.props.industry, "银行")
  members.props.onSelect("600001")
  tree = page.render()
  const detail = elements(tree).find((item) => item.type === page.Detail)
  assert.equal(detail.props.code, "600001")
  detail.props.onBack()
  assert.ok(elements(page.render()).find((item) => item.type === page.Members))
  assert.equal(page.calls.length, 1)
})

test("dashboard shows explicit empty, partial and failure states and aborts unmounted reads", async () => {
  const empty = mount({ response: { ...boards, boards: [], coverage: { instruments: 0, quoted: 0, comparable: 0 } } }); empty.render(); await empty.settle()
  assert.match(text(empty.render()), /暂无.*(行情|板块|数据)/)
  const partial = mount({ response: { ...boards, has_more: true } }); partial.render(); await partial.settle()
  assert.match(text(partial.render()), /未完整|部分板块|展示板块/)
  const failed = mount({ error: "行情源不可用" }); failed.render(); await failed.settle()
  assert.match(text(failed.render()), /行情源不可用/)
  assert.ok(elements(failed.render()).find((item) => item.type === "button" && /重试|刷新/.test(text(item))))
  failed.unmount(); assert.equal(failed.calls[0].signal.aborted, true)
})

test("dashboard retry recovers real statistics and ignores late data after cleanup", async () => {
  const page = mount({ error: "看板连接失败" }); page.render(); await page.settle()
  elements(page.render()).find((item) => item.type === "button" && text(item) === "重试").props.onClick()
  page.controls.error = null
  page.render(); await page.settle()
  assert.equal(page.calls.length, 2)
  assert.match(text(page.render()), /2026-10-16/)
  let finish
  const late = mount({ deferred: new Promise((resolve) => { finish = resolve }) })
  late.render(); await late.settle(); late.unmount()
  finish(boards); await new Promise(setImmediate)
  assert.doesNotMatch(text(late.render()), /2026-10-16/)
})

test("dashboard shows raw source and explicit EOD gaps with expandable audit metadata", async () => {
  const page = mount({ response: { ...boards, source: "teajoin daily 盘后快照", coverage: { instruments: 5558, quoted: 5548, comparable: 5548 }, source_row_count: 5567, missing_codes: Array.from({ length: 10 }, (_, index) => String(index + 1).padStart(6, "0")), unknown_codes: Array.from({ length: 19 }, (_, index) => String(index + 999000)), data_version: "123456abcdef".repeat(5), collected_at: "2026-10-18T04:00:00+00:00" } })
  page.render(); await page.settle()
  const tree = page.render()
  assert.match(text(tree), /teajoin daily 盘后快照/)
  assert.match(text(tree), /5548 \/ 5558/)
  assert.match(text(tree), /来源行数\s*5567/)
  assert.match(text(tree), /缺行情\s*10/)
  assert.match(text(tree), /目录未匹配\s*19/)
  const details = elements(tree).find((element) => element.type === "details")
  assert.ok(details)
  assert.match(text(details), /000001/)
  assert.match(text(details), /999000/)
  assert.match(text(details), /123456abcdef/)
  assert.match(text(details), /2026-10-18T04:00:00\+00:00/)
  assert.doesNotMatch(text(tree), /本地已导入日线/)
})

test("legacy qfq dashboard does not fabricate missing provenance counts", async () => {
  const page = mount({ response: { ...boards, source: "本地授权导入库", price_adjustment: "qfq" } }); page.render(); await page.settle()
  assert.match(text(page.render()), /本地授权导入库/)
  assert.match(text(page.render()), /前复权/)
  assert.doesNotMatch(text(page.render()), /来源行数|目录未匹配\s*0|缺行情\s*0|采集时间|版本/)
})

test("opening a stock keeps the original query workspace mounted but hidden and inert", async () => {
  const page = mount(); page.render(); await page.settle()
  const before = elements(page.render()).find((item) => item.type === page.Members)
  before.props.onSelect("000001")
  const tree = page.render()
  const retained = elements(tree).find((item) => item.type === page.Members)
  assert.ok(retained)
  assert.equal(retained.key, before.key)
  assert.ok(elements(tree).find((item) => item.props?.hidden === true && item.props?.inert === true))
  elements(tree).find((item) => item.type === page.Detail).props.onBack()
  assert.equal(elements(page.render()).find((item) => item.type === page.Members).key, before.key)
})

test("refresh updates member data without resetting its query component identity", async () => {
  const page = mount(); page.render(); await page.settle()
  const before = elements(page.render()).find((item) => item.type === page.Members)
  elements(page.render()).find((item) => item.type === "button" && text(item) === "刷新看板").props.onClick()
  const after = elements(page.render()).find((item) => item.type === page.Members)
  assert.equal(after.key, before.key)
  assert.equal(after.props.refreshKey, 1)
})
