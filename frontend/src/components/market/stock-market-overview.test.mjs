import assert from "node:assert/strict"
import test from "node:test"
import { existsSync, readFileSync } from "node:fs"
import { createRequire } from "node:module"
import vm from "node:vm"
import ts from "typescript"

const filename = new URL("./stock-market-overview.tsx", import.meta.url)
const requireModule = createRequire(import.meta.url)
const EodStatus = () => null
const boards = {
  as_of: "2026-10-16", previous_date: "2026-10-15", source: "teajoin_eod", price_adjustment: "none", has_more: false,
  coverage: { instruments: 5558, quoted: 5548, comparable: 5548 }, total: 1,
  boards: [{ industry: "银行", count: 100, quoted_count: 20, comparable_count: 15, advancing: 10, declining: 3, flat: 2, change_pct: 1.23, leader: null }],
}
function elements(tree) { return Array.isArray(tree) ? tree.flatMap(elements) : tree && typeof tree === "object" ? [tree, ...elements(tree.props?.children)] : [] }
function text(tree) { return Array.isArray(tree) ? tree.map(text).join("") : tree && typeof tree === "object" ? text(tree.props?.children) : tree == null || typeof tree === "boolean" ? "" : String(tree) }

function mount({ response = boards, error = null, deferred = null, collapsible = false, ...initialProps } = {}) {
  assert.ok(existsSync(filename), "stock market overview component must exist")
  const output = ts.transpileModule(readFileSync(filename, "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText
  const slots = [], effects = [], calls = [], published = []
  const controls = { response, error, deferred }
  const props = { ...initialProps }
  let cursor = 0
  const dependencies = {
    react: {
      useState(initial) { const index = cursor++; if (!(index in slots)) slots[index] = typeof initial === "function" ? initial() : initial; return [slots[index], (value) => { slots[index] = typeof value === "function" ? value(slots[index]) : value }] },
      useEffect(callback, deps) { const index = cursor++; if (!(index in slots) || deps.some((value, position) => value !== slots[index].deps[position])) { slots[index]?.cleanup?.(); slots[index] = { deps }; effects.push(() => { slots[index].cleanup = callback() }) } },
    },
    "@/lib/stock-market-api": { getStockBoardsApi: async (keyword, signal) => { calls.push({ keyword, signal }); if (controls.error) throw new Error(controls.error); return controls.deferred ?? controls.response } },
    "@/components/market/stock-eod-status": { StockEodStatus: EodStatus },
  }
  const module = { exports: {} }
  vm.runInNewContext(output, { module, exports: module.exports, AbortController, Error, require: (name) => dependencies[name] ?? requireModule(name) })
  const render = () => {
    cursor = 0
    published.length = 0
    return module.exports.StockMarketOverview({ collapsible, ...props, children: (overview) => { published.push(overview); return { type: "article", props: { "data-child": true } } } })
  }
  return {
    calls, published, controls, props,
    render,
    async settle() { while (effects.length) effects.shift()(); await new Promise(setImmediate) },
    unmount() { slots.forEach((slot) => slot?.cleanup?.()) },
  }
}

test("the shared overview reads the board directory once and shares one revision with the EOD status", async () => {
  const page = mount(); page.render(); await page.settle()
  assert.equal(page.calls.length, 1)
  assert.equal(page.calls[0].keyword, "")
  assert.equal(elements(page.render()).find((item) => item.type === EodStatus)?.props.refreshKey, 0)
  assert.equal(elements(page.render()).filter((item) => item.type === EodStatus).length, 1)
  page.published.at(-1).refresh()
  assert.equal(elements(page.render()).find((item) => item.type === EodStatus)?.props.refreshKey, 1)
  await page.settle()
  assert.equal(page.calls.length, 2)
  assert.equal(page.published.at(-1).revision, 1)
})

test("the overview keeps every provenance field in one collapsed audit disclosure", async () => {
  const page = mount({ response: { ...boards, source: "teajoin daily 盘后快照", source_row_count: 5567, missing_codes: Array.from({ length: 10 }, (_, index) => String(index + 1).padStart(6, "0")), unknown_codes: Array.from({ length: 19 }, (_, index) => String(index + 999000)), data_version: "abcdef012345".repeat(5), collected_at: "2026-10-18T04:00:00+00:00", reference_price: "vendor_ex_rights_pre_close", license_scope: "operator_configured_account_not_redistribution_certified" } })
  page.render(); await page.settle()
  const tree = page.render()
  assert.match(text(tree), /teajoin daily 盘后快照/)
  assert.match(text(tree), /5548 \/ 5558/)
  assert.match(text(tree), /可比日线样本/)
  assert.match(text(tree), /来源行数\s*5567/)
  assert.match(text(tree), /缺行情\s*10/)
  assert.match(text(tree), /目录未匹配\s*19/)
  assert.match(text(tree), /非实时/)
  const audit = elements(tree).find((element) => element.props?.["aria-label"] === "盘后行情来源与缺口")
  assert.ok(audit)
  const details = elements(audit).find((element) => element.type === "details")
  assert.ok(details)
  assert.notEqual(details.props.open, true)
  assert.match(text(details), /000001/)
  assert.match(text(details), /999000/)
  assert.match(text(details), /abcdef012345/)
  assert.match(text(details), /2026-10-18T04:00:00\+00:00/)
  assert.match(text(details), /供应商除权参考前收盘价/)
  assert.match(text(details), /operator_configured_account_not_redistribution_certified/)
})

test("legacy board responses never invent EOD counts or snapshot versions", async () => {
  const page = mount({ response: { ...boards, source: "本地授权导入库", price_adjustment: "qfq" } }); page.render(); await page.settle()
  assert.match(text(page.render()), /本地授权导入库/)
  assert.match(text(page.render()), /前复权/)
  assert.doesNotMatch(text(page.render()), /来源行数|目录未匹配\s*0|缺行情\s*0|采集时间|版本/)
})

test("operational status stays collapsed only when the page asks for it", async () => {
  const collapsed = mount({ collapsible: true }); collapsed.render(); await collapsed.settle()
  const disclosure = elements(collapsed.render()).find((item) => item.type === "details" && text(item).includes("盘后同步与影子归档状态"))
  assert.ok(disclosure)
  assert.notEqual(disclosure.props.open, true)
  assert.ok(elements(disclosure).some((item) => item.type === EodStatus))
  assert.ok(elements(disclosure).some((item) => item.type === "summary"))
  const expanded = mount(); expanded.render(); await expanded.settle()
  assert.ok(elements(expanded.render()).some((item) => item.type === EodStatus))
  assert.equal(elements(expanded.render()).filter((item) => item.type === "details" && text(item).includes("盘后同步与影子归档状态")).length, 0)
})

test("industry selection is owned by the overview and reported to the hosting page", async () => {
  const page = mount(); page.render(); await page.settle()
  assert.equal(page.published.at(-1).industry, "")
  page.published.at(-1).onIndustryChange("银行")
  page.render()
  assert.equal(page.published.at(-1).industry, "银行")
  const controlled = mount(); controlled.render(); await controlled.settle()
  assert.equal(controlled.published.at(-1).industry, "")
})

test("a failed read is retryable, aborted on unmount and never writes a late response", async () => {
  const failed = mount({ error: "板块与行情覆盖读取失败" }); failed.render(); await failed.settle()
  assert.match(text(failed.render()), /板块与行情覆盖读取失败/)
  assert.ok(elements(failed.render()).find((item) => item.type === "button" && text(item) === "重试"))
  failed.controls.error = null
  elements(failed.render()).find((item) => item.type === "button" && text(item) === "重试").props.onClick()
  failed.render(); await failed.settle()
  assert.equal(failed.calls.length, 2)
  assert.doesNotMatch(text(failed.render()), /板块与行情覆盖读取失败/)
  const aborted = mount(); aborted.render(); await aborted.settle(); aborted.unmount()
  assert.equal(aborted.calls[0].signal.aborted, true)
  let finish
  const late = mount({ deferred: new Promise((resolve) => { finish = resolve }) })
  late.render(); await late.settle(); late.unmount()
  finish(boards); await new Promise(setImmediate)
  assert.doesNotMatch(text(late.render()), /2026-10-16/)
})

test("the overview never issues its own stock member or detail reads", () => {
  const source = readFileSync(filename, "utf8")
  assert.doesNotMatch(source, /getStockMembersApi|getStockDetailsApi|getStockEodStatusApi/)
  assert.equal((source.match(/getStockBoardsApi\(/g) ?? []).length, 1)
  assert.match(source, /getStockBoardsApi\("", controller\.signal\)/)
})

test("compact context keeps real summary and all coverage source and operational detail in one collapsed area", async () => {
  const page = mount({ presentation: "compact", response: { ...boards, source_row_count: 5567, missing_codes: ["600001"], unknown_codes: ["999999"], data_version: "abcdef012345", collected_at: "2026-10-18T04:00:00+00:00" } })
  page.render(); await page.settle()
  const tree = page.render()
  const summary = elements(tree).find((item) => item.props?.["aria-label"] === "行情简要上下文")
  assert.ok(summary)
  assert.match(text(summary), /2026-10-16/)
  assert.match(text(summary), /5548\s*\/\s*5558/)
  assert.match(text(summary), /Teajoin.*不复权/)
  const disclosure = elements(tree).find((item) => item.props?.["aria-label"] === "行情数据与同步详情")
  assert.equal(disclosure.type, "details")
  assert.notEqual(disclosure.props.open, true)
  assert.equal(elements(disclosure).filter((item) => item.props?.["aria-label"] === "市场覆盖").length, 1)
  assert.equal(elements(disclosure).filter((item) => item.props?.["aria-label"] === "盘后行情来源与缺口").length, 1)
  assert.equal(elements(disclosure).filter((item) => item.type === EodStatus).length, 1)
  assert.match(text(disclosure), /5567/)
  assert.match(text(disclosure), /600001/)
  assert.match(text(disclosure), /abcdef012345/)
  assert.equal(page.calls.length, 1)
})

test("context suppression hides and inerts retained metadata without rereading or losing the bounded detail area", async () => {
  const page = mount({ presentation: "compact" }); page.render(); await page.settle()
  page.props.suppressContext = true
  const tree = page.render(); await page.settle()
  const context = elements(tree).find((item) => item.props?.["aria-label"] === "行情数据上下文")
  assert.equal(context.props.hidden, true)
  assert.equal(context.props.inert, true)
  assert.equal(elements(context).filter((item) => item.type === EodStatus).length, 1)
  assert.match(text(context), /2026-10-16/)
  const content = elements(tree).find((item) => item.props?.["data-market-content"])
  assert.match(content.props.className, /min-h-0/)
  assert.match(content.props.className, /flex-1/)
  assert.ok(elements(content).some((item) => item.props?.["data-child"]))
  assert.equal(page.calls.length, 1)
  page.props.suppressContext = false
  const restored = elements(page.render()).find((item) => item.props?.["aria-label"] === "行情数据上下文")
  assert.equal(restored.props.hidden, false)
  assert.equal(page.calls.length, 1)
})

test("overview alone owns bounded vertical scrolling while ordinary children keep natural height", async () => {
  const page = mount(); page.render(); await page.settle()
  const tree = page.render()
  assert.match(tree.props.className, /h-full/)
  assert.match(tree.props.className, /min-h-0/)
  assert.match(tree.props.className, /overflow-y-auto/)
  const content = elements(tree).find((item) => item.props?.["data-market-content"])
  assert.match(content.props.className, /shrink-0/)
  assert.doesNotMatch(content.props.className, /h-full|overflow|flex-1/)
})

test("compact failures remain explicit and retry shares the same directory read and status revision", async () => {
  const page = mount({ presentation: "compact", error: "本地覆盖读取失败" })
  page.render(); await page.settle()
  let tree = page.render()
  assert.match(text(tree), /本地覆盖读取失败/)
  assert.equal(elements(tree).some((item) => item.props?.["aria-label"] === "行情简要上下文"), false)
  assert.equal(elements(tree).filter((item) => item.type === EodStatus).length, 1)
  page.controls.error = null
  elements(tree).find((item) => item.type === "button" && text(item) === "重试").props.onClick()
  page.render(); await page.settle()
  tree = page.render()
  assert.equal(page.calls.length, 2)
  assert.equal(elements(tree).find((item) => item.type === EodStatus).props.refreshKey, 1)
  assert.match(text(tree), /2026-10-16/)
  assert.doesNotMatch(text(tree), /本地覆盖读取失败/)
})

test("hiding and restoring full context preserves the old operational presentation and original data instance", async () => {
  const page = mount({ collapsible: true })
  page.render(); await page.settle()
  page.render()
  const original = page.published.at(-1).boards
  page.props.suppressContext = true
  const hidden = page.render()
  assert.equal(elements(hidden).filter((item) => item.type === EodStatus).length, 1)
  assert.ok(elements(hidden).find((item) => item.type === "details" && text(item).includes("盘后同步与影子归档状态")))
  page.props.suppressContext = false
  page.render(); await page.settle()
  assert.equal(page.published.at(-1).boards, original)
  assert.equal(page.calls.length, 1)
})
