import assert from "node:assert/strict"
import { existsSync, readFileSync } from "node:fs"
import { createRequire } from "node:module"
import test from "node:test"
import vm from "node:vm"
import ts from "typescript"

const filename = new URL("./stock-screener-workspace.tsx", import.meta.url)
const requireModule = createRequire(import.meta.url)
const Members = () => null
const EodStatus = () => null
const Advanced = () => null
const Availability = () => null
const Detail = () => null
const boards = { as_of: "2026-10-16", source: "teajoin_eod", price_adjustment: "none", coverage: { instruments: 5558, quoted: 5548 }, has_more: false, boards: [{ industry: "银行" }, { industry: "半导体" }] }
function elements(tree) { return Array.isArray(tree) ? tree.flatMap(elements) : tree && typeof tree === "object" ? [tree, ...elements(tree.props?.children)] : [] }
function text(tree) { return Array.isArray(tree) ? tree.map(text).join("") : tree && typeof tree === "object" ? text(tree.props?.children) : tree == null || typeof tree === "boolean" ? "" : String(tree) }

function mount({ failure = null, response = boards } = {}) {
  assert.ok(existsSync(filename), "stock screener must be implemented")
  const output = ts.transpileModule(readFileSync(filename, "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText
  const slots = [], effects = [], calls = [], navigations = []
  const controls = { failure, response }
  let cursor = 0, overviewProps
  const dependencies = {
    react: {
      useState(initial) { const index = cursor++; if (!(index in slots)) slots[index] = initial; return [slots[index], (value) => { slots[index] = typeof value === "function" ? value(slots[index]) : value }] },
      useEffect(callback, deps) { const index = cursor++; if (!(index in slots) || deps.some((value, position) => value !== slots[index].deps[position])) { slots[index]?.cleanup?.(); slots[index] = { deps }; effects.push(() => { slots[index].cleanup = callback() }) } },
    },
    "next/navigation": { useRouter: () => ({ push: (url) => navigations.push(url) }) },
    "next/link": { default: "a" },
    "@/lib/stock-market-api": { getStockBoardsApi: async (keyword, signal) => { calls.push(signal); if (controls.failure) throw new Error(controls.failure); return controls.response } },
    "@/components/market/stock-board-browser": { StockMembers: Members },
    "@/components/market/stock-eod-status": { StockEodStatus: EodStatus },
    "@/components/market/stock-screen-results": { StockScreenResults: Advanced },
    "@/components/market/stock-data-availability": { StockDataAvailability: Availability },
    "@/components/market/stock-quote-detail": { StockQuoteDetail: Detail },
  }
  const module = { exports: {} }
  // The shared market overview runs in the same context against the same stubbed reads, so a
  // page-level assertion about "one board read and one EOD read" observes the real component.
  const overviewModule = { exports: {} }
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL("./stock-market-overview.tsx", import.meta.url), "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText, { module: overviewModule, exports: overviewModule.exports, Error, AbortController, require: (name) => dependencies[name] ?? requireModule(name) })
  const Overview = overviewModule.exports.StockMarketOverview
  dependencies["@/components/market/stock-market-overview"] = overviewModule.exports
  vm.runInNewContext(output, { module, exports: module.exports, Error, AbortController, require: (name) => dependencies[name] ?? requireModule(name) })
  function draw(node) {
    if (Array.isArray(node)) return node.flatMap(draw)
    if (!node || typeof node !== "object") return node
    if (node.type === Overview) { overviewProps = node.props; return draw(node.type(node.props)) }
    return node
  }
  return { controls, calls, navigations, Overview, get overviewProps() { return overviewProps }, render() { cursor = 0; return draw(module.exports.StockScreenerWorkspace()) }, async settle() { while (effects.length) effects.shift()(); await new Promise(setImmediate) }, unmount() { slots.forEach((slot) => slot?.cleanup?.()) } }
}

test("the stock screener reads the board directory and the EOD status exactly once", async () => {
  const page = mount(); page.render(); await page.settle()
  assert.equal(page.calls.length, 1)
  assert.equal(elements(page.render()).filter((item) => item.type === EodStatus).length, 1)
  assert.doesNotMatch(readFileSync(filename, "utf8"), /getStockBoardsApi|getStockEodStatusApi|getStockMembersApi/)
})

test("stock screener defaults to real multi-condition results and retains industry and detail context", async () => {
  const page = mount(); page.render(); await page.settle()
  const tree = page.render()
  assert.match(text(tree), /5558/); assert.match(text(tree), /5548/); assert.match(text(tree), /2026-10-16/)
  assert.match(text(tree), /非实时/); assert.match(text(tree), /缺价/)
  const audit = elements(tree).find((item) => item.type === "details")
  assert.ok(audit, "lengthy archive audit must be collapsed so filtering remains above the fold")
  assert.notEqual(audit.props.open, true)
  const selector = elements(tree).find((item) => item.props?.["aria-label"] === "筛选股票行业")
  assert.ok(selector); assert.match(text(selector), /银行/); assert.match(text(selector), /半导体/)
  selector.props.onChange({ target: { value: "银行" } })
  const members = elements(page.render()).find((item) => item.type === Advanced)
  assert.ok(members, "the screener must open its multi-condition workflow by default")
  assert.equal(elements(page.render()).some((item) => item.type === Members), false, "unused basic directory must not mount or query")
  assert.equal(members.props.industry, "银行")
  members.props.onSelect("600001")
  const opened = elements(page.render()).find((item) => item.type === Detail)
  assert.equal(opened?.props.code, "600001")
  const retained = elements(page.render()).find((item) => item.type === Advanced)
  assert.equal(retained.key, members.key)
  const workspace = elements(page.render()).find((item) => item.type === "main")
  assert.equal(workspace.props.hidden, true)
  assert.equal(workspace.props.inert, true)
  opened.props.onBack()
  assert.equal(elements(page.render()).some((item) => item.type === Detail), false)
  assert.equal(elements(page.render()).find((item) => item.type === Advanced).props.industry, "银行")
  assert.equal(page.navigations.length, 0)
  assert.equal(page.calls.length, 1)
})

test("industry failure is visible and retry refreshes industry and the current query without a fake empty success", async () => {
  const page = mount({ failure: "行业读取失败" }); page.render(); await page.settle()
  assert.match(text(page.render()), /行业读取失败/)
  const member = elements(page.render()).find((item) => item.type === Advanced)
  assert.ok(member)
  page.controls.failure = null
  elements(page.render()).find((item) => item.type === "button" && text(item).includes("刷新筛选数据")).props.onClick()
  page.render(); await page.settle()
  assert.doesNotMatch(text(page.render()), /行业读取失败/)
  assert.equal(elements(page.render()).find((item) => item.type === Advanced).props.refreshKey, 1)
  assert.equal(elements(page.render()).find((item) => item.type === EodStatus).props.refreshKey, 1)
})

test("screener aborts old industry reads and discloses capped classification", async () => {
  const page = mount({ response: { ...boards, has_more: true } }); page.render(); await page.settle()
  assert.match(text(page.render()), /200/)
  page.unmount()
  assert.equal(page.calls[0].aborted, true)
})

test("stock-only page branches keep futures components separate and authentication pending", () => {
  const screener = readFileSync(new URL("../../app/(main)/market/screener/page.tsx", import.meta.url), "utf8")
  const backtest = readFileSync(new URL("../../app/(main)/backtest/page.tsx", import.meta.url), "utf8")
  const lab = readFileSync(new URL("../../app/(main)/factor-lab/page.tsx", import.meta.url), "utf8")
  assert.match(screener, /StockScreenerWorkspace/)
  assert.match(screener, /market_type === "stock"/)
  assert.match(screener, /ScreenerWorkspace/)
  assert.match(backtest, /StockResearchEntry initialTab="backtest"/)
  assert.match(backtest, /FuturesBacktestPage/)
  assert.match(lab, /StockResearchEntry initialTab="analysis"/)
  assert.match(lab, /FactorLabPage/)
})

test("mode switches retain both mounted result instances and only first basic opt-in mounts its directory", async () => {
  const page = mount(); page.render(); await page.settle()
  elements(page.render()).find((item) => item.props?.["aria-label"] === "筛选股票行业").props.onChange({ target: { value: "银行" } })
  const advanced = elements(page.render()).find((item) => item.type === Advanced)
  assert.ok(advanced)
  assert.equal(advanced.props.industry, "银行")
  assert.equal(elements(page.render()).some((item) => item.type === Members), false)
  const basicButton = () => elements(page.render()).find((item) => item.type === "button" && text(item) === "基础股票目录")
  assert.ok(basicButton()); basicButton().props.onClick()
  let tree = page.render()
  const basic = elements(tree).find((item) => item.type === Members)
  assert.ok(basic)
  assert.equal(basic.props.industry, "银行")
  assert.equal(elements(tree).find((item) => item.type === Advanced).key, advanced.key)
  const modePanel = (tree, label) => elements(tree).find((item) => item.props?.["aria-label"] === label)
  assert.equal(modePanel(tree, "多条件筛选结果").props.hidden, true)
  assert.equal(modePanel(tree, "多条件筛选结果").props.inert, true)
  elements(tree).find((item) => item.type === "button" && text(item) === "多条件筛选").props.onClick()
  tree = page.render()
  assert.equal(elements(tree).find((item) => item.type === Members).key, basic.key)
  assert.equal(modePanel(tree, "基础股票目录结果").props.hidden, true)
  assert.equal(modePanel(tree, "基础股票目录结果").props.inert, true)
  assert.equal(modePanel(tree, "多条件筛选结果").props.hidden, false)
  advanced.props.onSelect("600001")
  const detail = elements(page.render()).find((item) => item.type === Detail)
  assert.equal(detail?.props.code, "600001")
  const retained = elements(page.render()).find((item) => item.type === Advanced)
  assert.equal(retained.key, advanced.key)
  assert.equal(retained.props.industry, "银行")
  assert.equal(retained.props.refreshKey, advanced.props.refreshKey)
  detail.props.onBack()
  assert.ok(elements(page.render()).find((item) => item.type === Advanced))
  assert.equal(elements(page.render()).find((item) => item.type === "main").props.inert, false)
  assert.equal(elements(page.render()).find((item) => item.type === Members).key, basic.key)
  assert.equal(page.calls.length, 1)
})

test("returning from stock detail preserves selected coverage disclosure and does not refresh the list", async () => {
  const page = mount(); page.render(); await page.settle()
  elements(page.render()).find((item) => item.type === "button" && text(item) === "查看数据覆盖资格").props.onClick()
  const before = elements(page.render()).find((item) => item.type === Advanced)
  before.props.onSelect("000001")
  const detail = elements(page.render()).find((item) => item.type === Detail)
  assert.match(detail.props.backLabel, /返回.*筛选/)
  detail.props.onBack()
  const tree = page.render()
  assert.equal(elements(tree).find((item) => item.type === Advanced).key, before.key)
  assert.equal(elements(tree).find((item) => item.type === Advanced).props.refreshKey, before.props.refreshKey)
  assert.ok(elements(tree).find((item) => item.type === Availability))
  assert.equal(elements(tree).find((item) => item.type === "main").props.hidden, false)
  assert.equal(page.calls.length, 1)
})

test("availability panel is an explicit stock-only opt-in and shares the parent refresh", async () => {
  const page = mount(); page.render(); await page.settle()
  assert.equal(elements(page.render()).some((item) => item.type === Availability), false)
  elements(page.render()).find((item) => item.type === "button" && text(item) === "查看数据覆盖资格").props.onClick()
  assert.equal(elements(page.render()).find((item) => item.type === Availability).props.refreshKey, 0)
  elements(page.render()).find((item) => item.type === "button" && text(item) === "刷新筛选数据").props.onClick()
  assert.equal(elements(page.render()).find((item) => item.type === Availability).props.refreshKey, 1)
})

test("basic detail returns to the same visible directory and retains the hidden advanced result", async () => {
  const page = mount(); page.render(); await page.settle()
  elements(page.render()).find((item) => item.type === "button" && text(item) === "基础股票目录").props.onClick()
  const before = [Members, Advanced].map((type) => elements(page.render()).find((item) => item.type === type))
  before[0].props.onSelect("000001")
  let tree = page.render()
  assert.equal(page.overviewProps.suppressContext, true)
  assert.equal(elements(tree).find((item) => item.type === "main").props.inert, true)
  for (const [index, type] of [Members, Advanced].entries()) assert.equal(elements(tree).find((item) => item.type === type).key, before[index].key)
  elements(tree).find((item) => item.type === Detail).props.onBack()
  tree = page.render()
  assert.equal(elements(tree).find((item) => item.props?.["aria-label"] === "基础股票目录结果").props.hidden, false)
  assert.equal(elements(tree).find((item) => item.props?.["aria-label"] === "多条件筛选结果").props.hidden, true)
  for (const [index, type] of [Members, Advanced].entries()) assert.equal(elements(tree).find((item) => item.type === type).props.refreshKey, before[index].props.refreshKey)
  assert.equal(page.calls.length, 1)
})

test("screen delegates compact context and vertical scrolling to overview and suppresses context for detail", async () => {
  const page = mount(); page.render(); await page.settle()
  let tree = page.render()
  assert.equal(page.overviewProps.presentation, "compact")
  assert.equal(page.overviewProps.suppressContext, false)
  const workspace = elements(tree).find((item) => item.type === "main")
  assert.doesNotMatch(workspace.props.className, /(?:^|\s)h-full(?:\s|$)|overflow-(auto|y-auto|hidden)/)
  elements(tree).find((item) => item.type === Advanced).props.onSelect("000001")
  tree = page.render()
  assert.equal(page.overviewProps.suppressContext, true)
  assert.ok(elements(tree).find((item) => item.type === Detail))
  elements(tree).find((item) => item.type === Detail).props.onBack(); page.render()
  assert.equal(page.overviewProps.suppressContext, false)
  assert.equal(page.calls.length, 1)
})

test("both mode identities survive refresh while only industry changes reset the original result keys", async () => {
  const page = mount(); page.render(); await page.settle()
  elements(page.render()).find((item) => item.type === "button" && text(item) === "基础股票目录").props.onClick()
  let tree = page.render()
  const before = [Advanced, Members].map((type) => elements(tree).find((item) => item.type === type))
  elements(tree).find((item) => item.type === "button" && text(item) === "刷新筛选数据").props.onClick()
  tree = page.render()
  for (const [index, type] of [Advanced, Members].entries()) {
    const after = elements(tree).find((item) => item.type === type)
    assert.equal(after.key, before[index].key)
    assert.equal(after.props.refreshKey, before[index].props.refreshKey + 1)
  }
  elements(tree).find((item) => item.props?.["aria-label"] === "筛选股票行业").props.onChange({ target: { value: "半导体" } })
  tree = page.render()
  for (const type of [Advanced, Members]) {
    const after = elements(tree).find((item) => item.type === type)
    assert.equal(after.key, "半导体")
    assert.equal(after.props.industry, "半导体")
  }
})
